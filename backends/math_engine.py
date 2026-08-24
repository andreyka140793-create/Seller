from config import PurchasingConfig

def calculate_unit_economics(
    buy_price: float,
    target_sell_price: float,
    logistics_cost: float = PurchasingConfig.DEFAULT_LOGISTICS_RUB,
    packaging_cost: float = PurchasingConfig.DEFAULT_PACKAGING_RUB,
    mp_commission_pct: float = PurchasingConfig.DEFAULT_MP_COMMISSION_PCT,
    tax_pct: float = PurchasingConfig.DEFAULT_TAX_PCT,
    min_roi_pct: float = PurchasingConfig.MIN_ROI_PCT,
    min_net_profit_rub: float = PurchasingConfig.MIN_NET_PROFIT_RUB
) -> dict:
    """
    Точный математический расчет юнит-экономики без участия LLM.
    """
    if buy_price <= 0 or target_sell_price <= 0:
        return {
            "buy_price": buy_price,
            "target_sell_price": target_sell_price,
            "commission": 0.0,
            "tax": 0.0,
            "total_cost": 0.0,
            "net_profit": 0.0,
            "roi_pct": 0.0,
            "margin_pct": 0.0,
            "is_profitable": False
        }

    commission = round(target_sell_price * (mp_commission_pct / 100.0), 2)
    tax = round(target_sell_price * (tax_pct / 100.0), 2)
    
    investments = buy_price + logistics_cost + packaging_cost
    total_cost = round(investments + commission + tax, 2)
    net_profit = round(target_sell_price - total_cost, 2)
    
    roi = round((net_profit / investments) * 100.0, 2) if investments > 0 else 0.0
    margin_pct = round((net_profit / target_sell_price) * 100.0, 2) if target_sell_price > 0 else 0.0
    is_profitable = (roi >= min_roi_pct) and (net_profit >= min_net_profit_rub)

    return {
        "buy_price": buy_price,
        "target_sell_price": target_sell_price,
        "commission": commission,
        "tax": tax,
        "total_cost": total_cost,
        "net_profit": net_profit,
        "roi_pct": roi,
        "margin_pct": margin_pct,
        "is_profitable": is_profitable
    }
