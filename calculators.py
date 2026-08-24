from abc import ABC, abstractmethod
from pydantic import BaseModel, Field

class BaseItem(BaseModel):
    product_name: str
    cost_price: float = Field(gt=0, description="Закупочная цена / себестоимость единицы")
    quantity: int = Field(default=1, gt=0)

class CalculationResult(BaseModel):
    revenue: float
    net_profit: float
    margin_percent: float
    roi_percent: float
    tax_amount: float

class BaseCalculator(ABC):
    @abstractmethod
    def calculate_item(self, item: BaseItem, params: BaseModel) -> CalculationResult:
        pass

class MarketplaceParams(BaseModel):
    selling_price: float
    commission_percent: float = Field(default=15.0, ge=0, le=100)
    logistics_cost: float = Field(default=120.0, ge=0)
    tax_rate_percent: float = Field(default=6.0, ge=0, le=100)

class MarketplaceCalculator(BaseCalculator):
    def calculate_item(self, item: BaseItem, params: MarketplaceParams) -> CalculationResult:
        revenue = params.selling_price * item.quantity
        commission = revenue * (params.commission_percent / 100.0)
        logistics = params.logistics_cost * item.quantity
        total_cost = item.cost_price * item.quantity
        
        tax = revenue * (params.tax_rate_percent / 100.0)
        net_profit = revenue - total_cost - commission - logistics - tax
        
        margin = (net_profit / revenue * 100.0) if revenue > 0 else 0.0
        roi = (net_profit / total_cost * 100.0) if total_cost > 0 else 0.0
        
        return CalculationResult(
            revenue=round(revenue, 2),
            net_profit=round(net_profit, 2),
            margin_percent=round(margin, 2),
            roi_percent=round(roi, 2),
            tax_amount=round(tax, 2)
        )

class B2BParams(BaseModel):
    wholesale_price: float
    freight_cost_per_unit: float = Field(default=0.0, ge=0)
    manager_bonus_percent: float = Field(default=0.0, ge=0, le=100)
    is_vat_included: bool = Field(default=True)

class B2BCalculator(BaseCalculator):
    def calculate_item(self, item: BaseItem, params: B2BParams) -> CalculationResult:
        revenue_gross = params.wholesale_price * item.quantity
        total_cost_gross = item.cost_price * item.quantity
        
        if params.is_vat_included:
            revenue_net = revenue_gross / 1.20
            cost_net = total_cost_gross / 1.20
            vat_output = revenue_gross - revenue_net
            vat_input = total_cost_gross - cost_net
            tax = vat_output - vat_input
        else:
            revenue_net = revenue_gross
            cost_net = total_cost_gross
            tax = 0.0

        freight = params.freight_cost_per_unit * item.quantity
        manager_bonus = revenue_net * (params.manager_bonus_percent / 100.0)
        
        net_profit = revenue_net - cost_net - freight - manager_bonus - tax
        margin = (net_profit / revenue_net * 100.0) if revenue_net > 0 else 0.0
        roi = (net_profit / cost_net * 100.0) if cost_net > 0 else 0.0
        
        return CalculationResult(
            revenue=round(revenue_gross, 2),
            net_profit=round(net_profit, 2),
            margin_percent=round(margin, 2),
            roi_percent=round(roi, 2),
            tax_amount=round(tax, 2)
        )
