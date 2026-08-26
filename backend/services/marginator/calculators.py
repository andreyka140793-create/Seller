"""
Unit economics calculators.
Supports: USN 6%, USN 15%, OSNO 20%.
B2B with configurable VAT rate.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Literal
from pydantic import BaseModel, Field


class BaseItem(BaseModel):
    product_name: str
    cost_price: float = Field(gt=0)
    quantity: int = Field(default=1, gt=0)
    weight_kg: float | None = Field(default=None, ge=0)


class CalculationResult(BaseModel):
    revenue: float
    variable_costs: float
    margin_rub: float
    margin_percent: float
    markup_percent: float
    net_profit: float
    net_margin_percent: float
    roi_percent: float
    tax_amount: float
    is_profitable: bool = False


class BaseCalculator(ABC):
    @abstractmethod
    def calculate_item(self, item: BaseItem, params: BaseModel) -> CalculationResult:
        pass


class MarketplaceParams(BaseModel):
    selling_price: float = Field(gt=0)
    commission_percent: float = Field(default=15.0, ge=0, le=100)
    logistics_cost: float = Field(default=120.0, ge=0)
    logistics_per_kg: float | None = Field(default=None, ge=0)
    packaging_cost: float = Field(default=30.0, ge=0)
    tax_rate_percent: float = Field(default=6.0, ge=0, le=100)
    tax_mode: Literal["usn_6", "usn_15", "osno_20"] = "usn_6"


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

        variable = cost + commission + logistics + packaging
        margin_rub = revenue - variable

        # Tax calculation based on mode
        if params.tax_mode == "usn_6":
            tax = revenue * (params.tax_rate_percent / 100.0)
        elif params.tax_mode == "usn_15":
            tax = max(0.0, (revenue - variable) * (params.tax_rate_percent / 100.0))
        elif params.tax_mode == "osno_20":
            # Simplified: 20% on profit
            tax = max(0.0, margin_rub * 0.20)
        else:
            tax = revenue * (params.tax_rate_percent / 100.0)

        net_profit = margin_rub - tax
        margin_pct = (margin_rub / revenue * 100.0) if revenue > 0 else 0.0
        markup_pct = (margin_rub / variable * 100.0) if variable > 0 else 0.0
        net_margin_pct = (net_profit / revenue * 100.0) if revenue > 0 else 0.0
        investments = cost + logistics + packaging
        roi = (net_profit / investments * 100.0) if investments > 0 else 0.0

        from config import PurchasingConfig
        is_profitable = (
            roi >= PurchasingConfig.MIN_ROI_PCT
            and net_profit >= PurchasingConfig.MIN_NET_PROFIT_RUB
        )

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
            is_profitable=is_profitable,
        )


class B2BParams(BaseModel):
    wholesale_price: float = Field(gt=0)
    freight_cost_per_unit: float = Field(default=0.0, ge=0)
    manager_bonus_percent: float = Field(default=0.0, ge=0, le=100)
    is_vat_included: bool = Field(default=True)
    vat_rate_percent: float = Field(default=20.0, ge=0, le=100)


class B2BCalculator(BaseCalculator):
    def calculate_item(self, item: BaseItem, params: B2BParams) -> CalculationResult:
        qty = item.quantity
        revenue_gross = params.wholesale_price * qty
        cost_gross = item.cost_price * qty
        vat_rate = params.vat_rate_percent / 100.0

        if params.is_vat_included and vat_rate > 0:
            revenue_net = revenue_gross / (1 + vat_rate)
            cost_net = cost_gross / (1 + vat_rate)
            vat_output = revenue_gross - revenue_net
            vat_input = cost_gross - cost_net
            tax = vat_output - vat_input
        else:
            revenue_net = revenue_gross
            cost_net = cost_gross
            tax = 0.0

        freight = params.freight_cost_per_unit * qty
        manager_bonus = revenue_net * (params.manager_bonus_percent / 100.0)
        variable = cost_net + freight + manager_bonus
        margin_rub = revenue_net - variable
        net_profit = margin_rub - tax

        margin_pct = (margin_rub / revenue_net * 100.0) if revenue_net > 0 else 0.0
        markup_pct = (margin_rub / variable * 100.0) if variable > 0 else 0.0
        net_margin_pct = (net_profit / revenue_net * 100.0) if revenue_net > 0 else 0.0
        roi = (net_profit / cost_net * 100.0) if cost_net > 0 else 0.0

        from config import PurchasingConfig
        is_profitable = (
            roi >= PurchasingConfig.MIN_ROI_PCT
            and net_profit >= PurchasingConfig.MIN_NET_PROFIT_RUB
        )

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
            is_profitable=is_profitable,
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
