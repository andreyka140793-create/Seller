import os
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException, Header
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from database import engine, Base, get_db
import models
from excel_processor import process_excel_file
from config import PurchasingConfig
from bot import get_bot_and_dp
from handlers.marginator_handler import marginator_router
from services.marginator.auth import verify_telegram_init_data


async def get_current_tg_user(x_telegram_init_data: str = Header(None)) -> dict:
    """Зависимость FastAPI для проверки прав доступа из Mini App."""
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Отсутствует заголовок авторизации Telegram")

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    validated_data = verify_telegram_init_data(x_telegram_init_data, bot_token)

    if not validated_data:
        raise HTTPException(status_code=403, detail="Недействительная подпись initData")

    return validated_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Инициализация таблиц БД
    Base.metadata.create_all(bind=engine)

    # 2. Получение экземпляров бота и диспетчера
    bot, dp = get_bot_and_dp()
    bot_task = None

    if bot and dp:
        # Регистрируем роутер Маржинатора в диспетчере
        dp.include_router(marginator_router)

        # Запускаем поллинг Telegram-бота в фоновой задаче
        bot_task = asyncio.create_task(dp.start_polling(bot))
        print("🤖 Telegram-бот с модулем Маржинатора успешно запущен в фоновом режиме")

    yield

    if bot_task:
        bot_task.cancel()


app = FastAPI(title="Trade Agent API", lifespan=lifespan)

# CORS: Mini App открывается в WebView Telegram с отдельного origin.
# В проде рекомендуется сузить allow_origins до конкретного домена вместо "*".
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Монтируем папку frontend для раздачи index.html
frontend_path = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.exists(frontend_path):
    app.mount("/app", StaticFiles(directory=frontend_path, html=True), name="frontend")


@app.get("/")
def root():
    return {"status": "ok", "message": "Сервер и Telegram-бот Trade Agent работают"}


@app.post("/upload-price/")
async def upload_price(
    telegram_id: int = Form(...),
    logistics_cost: float = Form(PurchasingConfig.DEFAULT_LOGISTICS_RUB),
    packaging_cost: float = Form(PurchasingConfig.DEFAULT_PACKAGING_RUB),
    mp_commission_pct: float = Form(PurchasingConfig.DEFAULT_MP_COMMISSION_PCT),
    tax_pct: float = Form(PurchasingConfig.DEFAULT_TAX_PCT),
    markup_pct: float = Form(PurchasingConfig.DEFAULT_MARKUP_PCT),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    allowed_ext = (".xlsx", ".xls", ".csv")
    if not file.filename or not file.filename.lower().endswith(allowed_ext):
        raise HTTPException(status_code=400, detail="Поддерживаются только файлы .xlsx, .xls, .csv")

    user = db.query(models.User).filter(models.User.telegram_id == telegram_id).first()
    if not user:
        user = models.User(telegram_id=telegram_id)
        db.add(user)
        db.commit()
        db.refresh(user)

    upload_record = models.PriceUpload(
        user_id=user.id,
        filename=file.filename,
        status="processing"
    )
    db.add(upload_record)
    db.commit()
    db.refresh(upload_record)

    file_bytes = await file.read()

    try:
        items = process_excel_file(
            file_bytes=file_bytes,
            filename=file.filename,
            logistics_cost=logistics_cost,
            packaging_cost=packaging_cost,
            mp_commission_pct=mp_commission_pct,
            tax_pct=tax_pct,
            markup_pct=markup_pct
        )

        db_items = [
            models.AnalyzedItem(
                upload_id=upload_record.id,
                title=item["title"],
                sku=item["sku"],
                buy_price=item["buy_price"],
                est_sell_price=item["est_sell_price"],
                net_profit=item["net_profit"],
                margin_pct=item["margin_pct"],
                roi_pct=item["roi_pct"],
                is_profitable=item["is_profitable"]
            )
            for item in items
        ]

        db.bulk_save_objects(db_items)

        total_revenue = sum(item["est_sell_price"] for item in items)
        total_profit = sum(item["net_profit"] for item in items)
        upload_record.total_revenue = round(total_revenue, 2)
        upload_record.total_profit = round(total_profit, 2)
        upload_record.status = "completed"
        db.commit()

        profitable_items = [i for i in items if i["is_profitable"]]

        return {
            "status": "success",
            "upload_id": upload_record.id,
            "total_items_processed": len(items),
            "profitable_items_count": len(profitable_items),
            "top_profitable_items": profitable_items[:10]
        }

    except Exception as e:
        upload_record.status = "error"
        db.commit()
        raise HTTPException(status_code=500, detail=f"Ошибка обработки: {str(e)}")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "marginator"}


@app.get("/api/history/{telegram_id}")
def get_user_history_api(
    telegram_id: int,
    db: Session = Depends(get_db),
    tg_data: dict = Depends(get_current_tg_user),
):
    """Возвращает историю расчетов пользователя для Mini App."""
    user_obj = tg_data.get("user") or {}
    requester_id = user_obj.get("id") if isinstance(user_obj, dict) else None
    try:
        requester_id = int(requester_id) if requester_id is not None else None
        telegram_id = int(telegram_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректный telegram_id")
    if requester_id != telegram_id:
        raise HTTPException(status_code=403, detail="Доступ запрещён: чужая история расчётов")

    user = db.query(models.User).filter(models.User.telegram_id == telegram_id).first()
    if not user:
        return []

    uploads = (
        db.query(models.PriceUpload)
        .filter(models.PriceUpload.user_id == user.id)
        .order_by(models.PriceUpload.created_at.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "id": u.id,
            "filename": u.filename,
            "total_revenue": u.total_revenue,
            "total_profit": u.total_profit,
            "created_at": u.created_at.strftime("%Y-%m-%d %H:%M")
        }
        for u in uploads
    ]


@app.get("/api/upload/{upload_id}")
def get_upload_details_api(
    upload_id: int,
    db: Session = Depends(get_db),
    tg_data: dict = Depends(get_current_tg_user),
):
    from sqlalchemy.orm import joinedload
    upload = (
        db.query(models.PriceUpload)
        .options(joinedload(models.PriceUpload.user), joinedload(models.PriceUpload.items))
        .filter(models.PriceUpload.id == upload_id)
        .first()
    )
    if not upload:
        raise HTTPException(status_code=404, detail="Расчёт не найден. Сделайте новый расчёт в боте.")

    user_obj = tg_data.get("user") or {}
    requester_id = user_obj.get("id") if isinstance(user_obj, dict) else None
    if requester_id is None:
        raise HTTPException(status_code=401, detail="Нет user id в initData")
    try:
        requester_id = int(requester_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail=f"Некорректный user id: {requester_id!r}")

    owner = upload.user
    if owner is None and upload.user_id:
        owner = db.query(models.User).filter(models.User.id == upload.user_id).first()

    if owner is None:
        raise HTTPException(
            status_code=403,
            detail="У расчёта нет владельца в БД. Сделайте новый расчёт в боте.",
        )

    try:
        owner_tid = int(owner.telegram_id)
    except (TypeError, ValueError):
        owner_tid = None

    if owner_tid != requester_id:
        raise HTTPException(
            status_code=403,
            detail=(
                f"ID не совпал (в БД {owner_tid}, в Telegram {requester_id}). "
                "Сделайте новый расчёт после обновления бота — старые записи могли "
                "сохраниться с обрезанным telegram_id."
            ),
        )

    return {
        "id": upload.id,
        "filename": upload.filename,
        "total_revenue": upload.total_revenue,
        "total_profit": upload.total_profit,
        "items": [
            {
                "title": item.title,
                "buy_price": item.buy_price,
                "est_sell_price": item.est_sell_price,
                "net_profit": item.net_profit,
                "margin_pct": item.margin_pct,
                "roi_pct": item.roi_pct,
                "is_profitable": item.is_profitable
            }
            for item in upload.items
        ]
    }
