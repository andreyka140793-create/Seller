import pytest
from services.marginator.calculators import (
    MarketplaceCalculator,
    MarketplaceParams,
    B2BCalculator,
    B2BParams,
    BaseItem,
)
from services.marginator.utils import clean_numeric_value


def test_clean_numeric_value_valid():
    assert clean_numeric_value("1 500,50 ₽") == 1500.50
    assert clean_numeric_value("2000.00") == 2000.0
    assert clean_numeric_value(350) == 350.0


def test_clean_numeric_value_edge_cases():
    assert clean_numeric_value(None) == 0.0
    assert clean_numeric_value("N/A") == 0.0
    assert clean_numeric_value("текст_вместо_цены") == 0.0
    assert clean_numeric_value("-500") == 0.0


def test_marketplace_calculator_standard_case():
    calc = MarketplaceCalculator()
    item = BaseItem(product_name="Футболка", cost_price=500.0)
    params = MarketplaceParams(
        selling_price=1000.0,
        commission_percent=15.0,
        logistics_cost=100.0,
        packaging_cost=30.0,
        tax_rate_percent=6.0,
    )
    res = calc.calculate_item(item, params)
    assert res.revenue == 1000.0
    assert res.net_profit == 160.0
    assert res.margin_percent == 16.0
    assert res.roi_percent == round(160 / 630 * 100, 2)


def test_marketplace_calculator_zero_revenue():
    calc = MarketplaceCalculator()
    item = BaseItem(product_name="Бесплатный товар", cost_price=100.0)
    params = MarketplaceParams(
        selling_price=0.0,
        commission_percent=15.0,
        logistics_cost=50.0,
        packaging_cost=0.0,
        tax_rate_percent=6.0,
    )
    res = calc.calculate_item(item, params)
    assert res.net_profit == -150.0
    assert res.margin_percent == 0.0


def test_b2b_calculator_vat_20():
    calc = B2BCalculator()
    item = BaseItem(product_name="Станок B2B", cost_price=120000.0)
    params = B2BParams(
        wholesale_price=180000.0,
        freight_cost_per_unit=5000.0,
        manager_bonus_percent=2.0,
        is_vat_included=True,
    )
    res = calc.calculate_item(item, params)
    assert res.net_profit == 32000.0
    assert res.tax_amount == 10000.0
