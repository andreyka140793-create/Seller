"""FastAPI application with Telegram bot polling."""
import os
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, Depends, UploadFile, File, Form, HTTPException, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from database import engine, Base, get_db
import models
from config import PurchasingConfig
from bot import get_bot_and_dp
from handlers.marginator import marginator_router  # triggers __init__ -> registers all handlers
from services.marginator.auth import verify_telegram_init_data

# ── Rate limiting (in-memory, per IP+path) ──
_rate_limits: Dict[str, list] = {}


async def rate_limit(request: Request, max_requests: int = 30, window: int = 60):
    key = f"{request.client.host}:{request.url.path}"
    now = time.time()
    _rate_limits.setdefault(key, [])
    _rate_limits[key] = [t for t in _rate_limits[key] if now - t < window]
    if len(_rate_limits[key]) >= max_requests:
        raise HTTPException(429, "Too many requests")
    _rate_limits[key].append(now)


# ── Auth dependency ──
async def get_current_tg_user(x_telegram_init_data: str = Header(None)) -> dict:
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Missing Telegram auth header")
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    validated = verify_telegram_init_data(x_telegram_init_data, bot_token)
    if not validated:
        raise HTTPException(status_code=403, detail="Invalid initData signature")
    return validated


# ── Lifespan ──
@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    bot, dp = get_bot_and_dp()
    bot_task = None
    if bot and dp:
        dp.include_router(marginator_router)
        bot_task = asyncio.create_task(dp.start_polling(bot))
        print("🤖 Telegram bot started")
    yield
    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Trade Agent API", lifespan=lifespan)

# ── CORS ──
_origins = os.getenv("ALLOWED_ORIGINS", "https://web.telegram.org").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["X-Telegram-Init-Data", "Content-Type"],
)

# ── Static frontend ──
frontend_path = os.path.join(os.path.dirname(__file__), "frontend")
if os.path.exists(frontend_path):
    app.mount("/app", StaticFiles(directory=frontend_path, html=True), name="frontend")


@app.get("/")
def root():
    return {"status": "ok", "service": "trade-agent"}


@app.get("/api/health")
def health():
    return {"ok": True, "service": "marginator"}


@app.post("/upload-price/")
async def upload_price(
    request: Request,
    telegram_id: int = Form(...),
    logistics_cost: float = Form(PurchasingConfig.DEFAULT_LOGISTICS_RUB),
    packaging_cost: float = Form(PurchasingConfig.DEFAULT_PACKAGING_RUB),
    mp_commission_pct: float = Form(PurchasingConfig.DEFAULT_MP_COMMISSION_PCT),
    tax_pct: float = Form(PurchasingConfig.DEFAULT_TAX_PCT),
    markup_pct: float = Form(PurchasingConfig.DEFAULT_MARKUP_PCT),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    await rate_limit(request, max_requests=10, window=60)

    allowed_ext = (".xlsx", ".xls", ".csv")
    if not file.filename or not file.filename.lower().endswith(allowed_ext):
        raise HTTPException(400, "Only .xlsx, .xls, .csv files are supported")

    user = db.query(models.User).filter(models.User.telegram_id == telegram_id).first()
    if not user:
        user = models.User(telegram_id=telegram_id)
        db.add(user)
        db.commit()
        db.refresh(user)

    upload_record = models.PriceUpload(
        user_id=user.id,
        filename=file.filename,
        status="processing",
    )
    db.add(upload_record)
    db.commit()
    db.refresh(upload_record)

    file_bytes = await file.read()

    try:
        from services.marginator.parser import ExcelParserService
        from services.marginator.calculators import MarketplaceCalculator, MarketplaceParams, BaseItem
        from services.marginator.utils import clean_numeric_value
        import pandas as pd

        parser = ExcelParserService(api_key=os.getenv("XAI_API_KEY"))
        mapping = await asyncio.to_thread(parser.analyze_file_structure_sync, file_bytes, file.filename)
        df = await asyncio.to_thread(parser.load_normalized_dataframe, file_bytes, file.filename, mapping)

        calc = MarketplaceCalculator()
        items = []
        for _, row in df.iterrows():
            try:
                title = str(row.get(mapping.product_name_col) or "").strip()
                if not title:
                    continue
                buy = clean_numeric_value(row.get(mapping.cost_price_col))
                if buy <= 0:
                    continue
                sell = clean_numeric_value(row.get(mapping.selling_price_col)) if mapping.selling_price_col else 0
                if sell <= 0:
                    sell = buy * (1 + markup_pct / 100.0)
                item = BaseItem(product_name=title, cost_price=buy)
                params = MarketplaceParams(
                    selling_price=sell,
                    commission_percent=mp_commission_pct,
                    logistics_cost=logistics_cost,
                    packaging_cost=packaging_cost,
                    tax_rate_percent=tax_pct,
                )
                res = calc.calculate_item(item, params)
                items.append({
                    "title": title,
                    "sku": str(row.get(mapping.quantity_col) or "") if mapping.quantity_col else None,
                    "buy_price": buy,
                    "est_sell_price": sell,
                    "net_profit": res.net_profit,
                    "margin_pct": res.margin_percent,
                    "roi_pct": res.roi_percent,
                    "is_profitable": res.is_profitable,
                })
            except Exception:
                continue

        db_items = [
            models.AnalyzedItem(
                upload_id=upload_record.id,
                title=i["title"],
                sku=i["sku"],
                buy_price=i["buy_price"],
                est_sell_price=i["est_sell_price"],
                net_profit=i["net_profit"],
                margin_pct=i["margin_pct"],
                roi_pct=i["roi_pct"],
                is_profitable=i["is_profitable"],
            )
            for i in items
        ]
        db.add_all(db_items)

        total_revenue = sum(i["est_sell_price"] for i in items)
        total_profit = sum(i["net_profit"] for i in items)
        upload_record.total_revenue = round(total_revenue, 2)
        upload_record.total_profit = round(total_profit, 2)
        upload_record.status = "completed"
        db.commit()

        profitable = [i for i in items if i["is_profitable"]]
        return {
            "status": "success",
            "upload_id": upload_record.id,
            "total_items_processed": len(items),
            "profitable_items_count": len(profitable),
            "top_profitable_items": profitable[:10],
        }
    except Exception:
        upload_record.status = "error"
        db.commit()
        raise HTTPException(500, "Processing error")


@app.get("/api/history/{telegram_id}")
async def get_user_history_api(
    request: Request,
    telegram_id: int,
    db: Session = Depends(get_db),
    tg_data: dict = Depends(get_current_tg_user),
):
    await rate_limit(request, max_requests=20, window=60)
    user_obj = tg_data.get("user") or {}
    requester_id = user_obj.get("id")
    try:
        requester_id = int(requester_id) if requester_id is not None else None
        telegram_id = int(telegram_id)
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid telegram_id")
    if requester_id != telegram_id:
        raise HTTPException(403, "Access denied")

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
            "created_at": u.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for u in uploads
    ]


@app.get("/api/upload/{upload_id}")
async def get_upload_details_api(
    request: Request,
    upload_id: int,
    db: Session = Depends(get_db),
    tg_data: dict = Depends(get_current_tg_user),
):
    await rate_limit(request, max_requests=30, window=60)
    from sqlalchemy.orm import joinedload
    upload = (
        db.query(models.PriceUpload)
        .options(joinedload(models.PriceUpload.user), joinedload(models.PriceUpload.items))
        .filter(models.PriceUpload.id == upload_id)
        .first()
    )
    if not upload:
        raise HTTPException(404, "Upload not found")

    user_obj = tg_data.get("user") or {}
    requester_id = user_obj.get("id")
    if requester_id is None:
        raise HTTPException(401, "No user id in initData")
    try:
        requester_id = int(requester_id)
    except (TypeError, ValueError):
        raise HTTPException(401, "Invalid user id")

    owner_tid = upload.user.telegram_id if upload.user else None
    if owner_tid != requester_id:
        raise HTTPException(403, "Access denied")

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
                "is_profitable": item.is_profitable,
            }
            for item in upload.items
        ],
    }
