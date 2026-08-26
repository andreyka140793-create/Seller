"""
Юнит-экономика по логике, согласованной со статьёй Т‑Банка (маржа / маржинальность / наценка)
и практикой селлера (комиссия МП, логистика, упаковка, налог).

Маржа, ₽          = Выручка − переменные расходы
Маржинальность %  = Маржа / Выручка × 100
Наценка %         = Маржа / Переменные расходы × 100
Чистая прибыль    = Маржа − налог
Рентабельность %  = Чистая прибыль / Выручка × 100
ROI %             = Чистая прибыль / вложения × 100
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pydantic import BaseModel, Field


class BaseItem(BaseModel):
    product_name: str
    cost_price: float = Field(gt=0, description="Закупочная цена / себестоимость единицы")
    quantity: int = Field(default=1, gt=0)
    weight_kg: float | None = Field(default=None, ge=0, description="Вес единицы, кг")


class CalculationResult(BaseModel):
    revenue: float
    variable_costs: float  # закуп + комиссия + логистика + упаковка (+ фрахт/бонус в B2B)
    margin_rub: float  # маржа (маржинальный доход), ₽
    margin_percent: float  # маржинальность % = маржа / выручка
    markup_percent: float  # наценка % = маржа / переменные
    net_profit: float  # после налога
    net_margin_percent: float  # рентабельность чистая % = чистая / выручка
    roi_percent: float
    tax_amount: float


class BaseCalculator(ABC):
    @abstractmethod
    def calculate_item(self, item: BaseItem, params: BaseModel) -> CalculationResult:
        pass


class MarketplaceParams(BaseModel):
    selling_price: float
    commission_percent: float = Field(default=15.0, ge=0, le=100)
    logistics_cost: float = Field(default=120.0, ge=0)  # фикс ₽/шт
    logistics_per_kg: float | None = Field(default=None, ge=0)  # ₽/кг; если задано и есть вес — используется
    packaging_cost: float = Field(default=30.0, ge=0)
    tax_rate_percent: float = Field(default=6.0, ge=0, le=100)


class MarketplaceCalculator(BaseCalculator):
    def calculate_item(self, item: BaseItem, params: MarketplaceParams) -> CalculationResult:
        qty = item.quantity
        revenue = params.selling_price * qty
        cost = item.cost_price * qty
        commission = revenue * (params.commission_percent / 100.0)
        if params.logistics_per_kg is not None and item.weight_kg and item.weight_kg > 0:
            logistics = params.logistics_per_kg * item.weight_kg * qty
        else:
            logistics = params.logistics_cost * qty
        packaging = params.packaging_cost * qty

        # Переменные: всё, что растёт с продажей (как в статье + комиссия МП)
        variable = cost + commission + logistics + packaging
        margin_rub = revenue - variable
        tax = revenue * (params.tax_rate_percent / 100.0)
        net_profit = margin_rub - tax

        margin_pct = (margin_rub / revenue * 100.0) if revenue > 0 else 0.0
        markup_pct = (margin_rub / variable * 100.0) if variable > 0 else 0.0
        net_margin_pct = (net_profit / revenue * 100.0) if revenue > 0 else 0.0
        # ROI от вложений в товар и доставку/упаковку (без комиссии — она с выручки)
        investments = cost + logistics + packaging
        roi = (net_profit / investments * 100.0) if investments > 0 else 0.0

        return CalculationResult(
            revenue=round(revenue, 2),
            variable_costs=round(variable, 2),
            margin_rub=round(margin_rub, 2),
            margin_percent=round(margin_pct, 2),
            markup_percent=round(markup_pct, 2),
            net_profit=round(net_profit, 2),
            net_margin_percent=round(net_margin_pct, 2),
            roi_percent=round(roi, 2),
            tax_amount=round(tax, 2),
        )


class B2BParams(BaseModel):
    wholesale_price: float
    freight_cost_per_unit: float = Field(default=0.0, ge=0)
    manager_bonus_percent: float = Field(default=0.0, ge=0, le=100)
    is_vat_included: bool = Field(default=True)


class B2BCalculator(BaseCalculator):
    def calculate_item(self, item: BaseItem, params: B2BParams) -> CalculationResult:
        qty = item.quantity
        revenue_gross = params.wholesale_price * qty
        cost_gross = item.cost_price * qty

        if params.is_vat_included:
            revenue_net = revenue_gross / 1.20
            cost_net = cost_gross / 1.20
            vat_output = revenue_gross - revenue_net
            vat_input = cost_gross - cost_net
            tax = vat_output - vat_input  # НДС к уплате
        else:
            revenue_net = revenue_gross
            cost_net = cost_gross
            tax = 0.0

        freight = params.freight_cost_per_unit * qty
        manager_bonus = revenue_net * (params.manager_bonus_percent / 100.0)

        # Переменные в «нетто»-контуре: себестоимость + фрахт + бонус
        variable = cost_net + freight + manager_bonus
        margin_rub = revenue_net - variable
        net_profit = margin_rub - tax

        margin_pct = (margin_rub / revenue_net * 100.0) if revenue_net > 0 else 0.0
        markup_pct = (margin_rub / variable * 100.0) if variable > 0 else 0.0
        net_margin_pct = (net_profit / revenue_net * 100.0) if revenue_net > 0 else 0.0
        roi = (net_profit / cost_net * 100.0) if cost_net > 0 else 0.0

        return CalculationResult(
            revenue=round(revenue_gross, 2),
            variable_costs=round(variable, 2),
            margin_rub=round(margin_rub, 2),
            margin_percent=round(margin_pct, 2),
            markup_percent=round(markup_pct, 2),
            net_profit=round(net_profit, 2),
            net_margin_percent=round(net_margin_pct, 2),
            roi_percent=round(roi, 2),
            tax_amount=round(tax, 2),
        )



def min_selling_price_for_margin(
    cost_price: float,
    *,
    target_margin_percent: float,
    commission_percent: float = 0.0,
    logistics_cost: float = 0.0,
    packaging_cost: float = 0.0,
    tax_rate_percent: float = 0.0,
    use_net_margin: bool = False,
) -> float | None:
    """
    Минимальная цена продажи для целевой маржинальности (до налога)
    или чистой рентабельности (use_net_margin=True).

    P = (cost + logistics + packaging) / (1 - commission/100 - margin/100 [- tax/100])
    """
    if cost_price <= 0:
        return None
    m = target_margin_percent / 100.0
    c = commission_percent / 100.0
    t_ = tax_rate_percent / 100.0 if use_net_margin else 0.0
    denom = 1.0 - c - m - t_
    if denom <= 0.01:
        return None
    price = (cost_price + logistics_cost + packaging_cost) / denom
    return round(max(price, 0.0), 2)
