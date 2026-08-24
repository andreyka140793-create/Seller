# backend/config.py

class PurchasingConfig:
    # Минимальные пороги рентабельности
    MIN_ROI_PCT: float = 35.0          # Минимальный ROI в %
    MIN_NET_PROFIT_RUB: float = 200.0   # Минимальная прибыль с 1 шт в рублях
    MIN_SUPPLIER_STOCK: int = 50       # Минимальный остаток на складе

    # Стандартные комиссии и расходы (можно переопределять в Mini App)
    DEFAULT_LOGISTICS_RUB: float = 150.0  # Логистика до склада/маркетплейса
    DEFAULT_PACKAGING_RUB: float = 30.0   # Упаковка и маркировка
    DEFAULT_MP_COMMISSION_PCT: float = 15.0 # Комиссия маркетплейса (WB/Ozon)
    DEFAULT_TAX_PCT: float = 6.0          # Налог (УСН "Доходы" 6%)

    # Параметры наценки для первичного анализа
    DEFAULT_MARKUP_PCT: float = 100.0     # Базовая наценка (+100% к закупке)
