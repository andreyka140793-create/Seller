import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException
from sqlalchemy.orm import Session

from database import engine, Base, get_db
import models
from excel_processor import process_excel_file
from config import PurchasingConfig
from bot import get_bot_and_dp
from handlers.marginator_handler import marginator_router

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

# Монтируем папку frontend для раздачи index.html
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/app", StaticFiles(directory=frontend_path, html=True), name="frontend")
# Монтируем папку frontend для раздачи index.html
frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
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
                roi_pct=item["roi_pct"],
                is_profitable=item["is_profitable"]
            )
            for item in items
        ]

        db.bulk_save_objects(db_items)
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

@app.get("/api/history/{telegram_id}")
def get_user_history_api(telegram_id: int, db: Session = Depends(get_db)):
    """Возвращает историю расчетов пользователя для Mini App."""
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
def get_upload_details_api(upload_id: int, db: Session = Depends(get_db)):
    """Возвращает детальную аналитику по конкретной партии для построения графиков."""
    upload = db.query(models.PriceUpload).filter(models.PriceUpload.id == upload_id).first()
    if not upload:
        raise HTTPException(status_code=404, detail="Расчет не найден")
        
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
