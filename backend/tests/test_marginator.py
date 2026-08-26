"""Tests for marginator service."""
import pytest
from services.marginator.calculators import MarketplaceCalculator, B2BCalculator, MarketplaceParams, B2BParams, BaseItem
from services.marginator.utils import clean_numeric_value, sanitize_filename
from services.marginator.auth import verify_telegram_init_data


class TestCalculators:
    def test_marketplace_basic(self):
        calc = MarketplaceCalculator()
        item = BaseItem(product_name="Test", cost_price=100.0)
        params = MarketplaceParams(selling_price=250.0, commission_percent=15.0, logistics_cost=50.0, packaging_cost=20.0, tax_rate_percent=6.0)
        res = calc.calculate_item(item, params)
        assert res.revenue == 250.0
        assert res.net_profit > 0
        assert res.roi_percent > 0

    def test_marketplace_usn15(self):
        calc = MarketplaceCalculator()
        item = BaseItem(product_name="Test", cost_price=100.0)
        params = MarketplaceParams(selling_price=250.0, commission_percent=15.0, logistics_cost=50.0, packaging_cost=20.0, tax_rate_percent=15.0, tax_mode="usn_15")
        res = calc.calculate_item(item, params)
        assert res.tax_amount >= 0

    def test_b2b_vat_included(self):
        calc = B2BCalculator()
        item = BaseItem(product_name="Test", cost_price=100.0)
        params = B2BParams(wholesale_price=200.0, is_vat_included=True, vat_rate_percent=20.0)
        res = calc.calculate_item(item, params)
        assert res.revenue == 200.0
        assert res.net_profit > 0

    def test_b2b_vat_10_percent(self):
        calc = B2BCalculator()
        item = BaseItem(product_name="Test", cost_price=100.0)
        params = B2BParams(wholesale_price=200.0, is_vat_included=True, vat_rate_percent=10.0)
        res = calc.calculate_item(item, params)
        assert res.tax_amount >= 0


class TestUtils:
    def test_clean_numeric(self):
        assert clean_numeric_value("1 234,56") == 1234.56
        assert clean_numeric_value("-50", allow_negative=True) == -50.0
        assert clean_numeric_value("-50", allow_negative=False) == 0.0
        assert clean_numeric_value(None) == 0.0

    def test_sanitize_filename(self):
        assert sanitize_filename("../../etc/passwd") == "etcpasswd"
        assert sanitize_filename("price 2024.xlsx") == "price2024.xlsx"


class TestAuth:
    def test_verify_invalid(self):
        assert verify_telegram_init_data("invalid", "token") is None

    def test_verify_missing_hash(self):
        assert verify_telegram_init_data("foo=bar", "token") is None


class TestAPI:
    def test_health(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json()["service"] == "trade-agent"
