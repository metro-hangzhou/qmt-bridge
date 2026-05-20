"""Tests for MockBridge — paper trading in-memory simulation."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from qmt_bridge.mock_bridge import MockBridge
from qmt_bridge.constants import STATUS_FILLED, STATUS_PARTIAL, STATUS_CANCELED, STATUS_PENDING


# ── fixtures ──────────────────────────────────────────────────────────────────

def make_bridge(**kw) -> MockBridge:
    return MockBridge(kw if kw else None)


# ── lifecycle ─────────────────────────────────────────────────────────────────

class TestLifecycle:
    def test_connect_returns_true(self):
        assert MockBridge().connect() is True

    def test_is_available_always_true(self):
        assert MockBridge().is_available() is True

    def test_disconnect_noop(self):
        b = MockBridge()
        b.disconnect()  # must not raise

    def test_reset_clears_state(self):
        b = make_bridge(initial_cash=100_000.0)
        b.buy("600519.SH", 1800.0, 10)
        b.reset()
        assert b.get_balance()["available"] == 100_000.0
        assert b.get_positions() == []
        assert b.get_today_orders() == []
        assert b.get_today_trades() == []

    def test_reset_with_new_cash(self):
        b = MockBridge()
        b.reset(initial_cash=500_000.0)
        assert b.get_balance()["available"] == 500_000.0


# ── balance ───────────────────────────────────────────────────────────────────

class TestBalance:
    def test_initial_balance_keys(self):
        b = make_bridge(initial_cash=1_000_000.0)
        bal = b.get_balance()
        for k in ("available", "frozen_cash", "market_value", "total_asset"):
            assert k in bal

    def test_initial_cash(self):
        b = make_bridge(initial_cash=500_000.0)
        assert b.get_balance()["available"] == 500_000.0

    def test_total_asset_equals_cash_when_no_positions(self):
        b = make_bridge(initial_cash=200_000.0)
        bal = b.get_balance()
        assert bal["total_asset"] == bal["available"]

    def test_cash_decreases_after_buy(self):
        b = make_bridge(initial_cash=1_000_000.0)
        b.buy("600519.SH", 1800.0, 100)
        bal = b.get_balance()
        assert bal["available"] == 1_000_000.0 - 1800.0 * 100


# ── instant fill mode ─────────────────────────────────────────────────────────

class TestInstantFill:
    def test_buy_returns_order_id(self):
        result = MockBridge().buy("600519.SH", 1800.0, 100)
        assert "order_id" in result
        assert result["status"] == "submitted"

    def test_buy_creates_position(self):
        b = MockBridge()
        b.buy("600519.SH", 1800.0, 100)
        positions = b.get_positions()
        assert len(positions) == 1
        assert positions[0]["code"] == "600519.SH"
        assert positions[0]["volume"] == 100

    def test_sell_reduces_position(self):
        b = MockBridge()
        b.set_position("600519.SH", 200, 1800.0)
        b.sell("600519.SH", 1820.0, 100)
        pos = b.get_positions()
        assert pos[0]["volume"] == 100

    def test_sell_increases_cash(self):
        b = make_bridge(initial_cash=0.0)
        b.set_position("600519.SH", 100, 1800.0)
        b.sell("600519.SH", 1820.0, 100)
        assert b.get_balance()["available"] == pytest.approx(1820.0 * 100)

    def test_order_status_filled(self):
        b = MockBridge()
        b.buy("600519.SH", 1800.0, 100)
        orders = b.get_today_orders()
        assert orders[0]["status"] == STATUS_FILLED

    def test_trade_recorded(self):
        b = MockBridge()
        b.buy("600519.SH", 1800.0, 100)
        trades = b.get_today_trades()
        assert len(trades) == 1
        assert trades[0]["code"] == "600519.SH"
        assert trades[0]["volume"] == 100
        assert trades[0]["price"] == 1800.0

    def test_avg_price_correct(self):
        b = MockBridge()
        b.buy("600519.SH", 1800.0, 100)
        b.buy("600519.SH", 1820.0, 100)
        pos = b.get_positions()[0]
        expected = (1800.0 * 100 + 1820.0 * 100) / 200
        assert pos["avg_price"] == pytest.approx(expected)


# ── never fill mode ───────────────────────────────────────────────────────────

class TestNeverFill:
    def test_order_stays_pending(self):
        b = make_bridge(fill_mode="never")
        b.buy("600519.SH", 1800.0, 100)
        orders = b.get_today_orders()
        assert orders[0]["status"] == STATUS_PENDING

    def test_no_trades(self):
        b = make_bridge(fill_mode="never")
        b.buy("600519.SH", 1800.0, 100)
        assert b.get_today_trades() == []

    def test_no_position_created(self):
        b = make_bridge(fill_mode="never")
        b.buy("600519.SH", 1800.0, 100)
        assert b.get_positions() == []

    def test_cancel_pending_order(self):
        b = make_bridge(fill_mode="never")
        result = b.buy("600519.SH", 1800.0, 100)
        ok = b.cancel(result["order_id"])
        assert ok is True
        orders = b.get_today_orders()
        assert orders[0]["status"] == STATUS_CANCELED

    def test_cancel_nonexistent_returns_false(self):
        b = make_bridge(fill_mode="never")
        assert b.cancel("nonexistent") is False

    def test_cancel_all(self):
        b = make_bridge(fill_mode="never")
        b.buy("600519.SH", 1800.0, 100)
        b.buy("000001.SZ", 10.0, 1000)
        result = b.cancel_all()
        assert len(result["canceled"]) == 2
        for o in b.get_today_orders():
            assert o["status"] == STATUS_CANCELED


# ── partial fill mode ─────────────────────────────────────────────────────────

class TestPartialFill:
    def test_order_status_partial(self):
        b = make_bridge(fill_mode="partial")
        b.buy("600519.SH", 1800.0, 100)
        orders = b.get_today_orders()
        assert orders[0]["status"] == STATUS_PARTIAL

    def test_filled_volume_is_half(self):
        b = make_bridge(fill_mode="partial")
        b.buy("600519.SH", 1800.0, 100)
        orders = b.get_today_orders()
        assert orders[0]["filled_volume"] == 50  # ceil(100/2)

    def test_position_reflects_partial(self):
        b = make_bridge(fill_mode="partial")
        b.buy("600519.SH", 1800.0, 100)
        pos = b.get_positions()
        assert pos[0]["volume"] == 50


# ── fill price override ───────────────────────────────────────────────────────

class TestFillPriceOverride:
    def test_fill_at_override_price(self):
        b = MockBridge({"fill_price": 1900.0, "initial_cash": 1_000_000.0})
        b.buy("600519.SH", 1800.0, 100)
        trades = b.get_today_trades()
        assert trades[0]["price"] == 1900.0


# ── callbacks ─────────────────────────────────────────────────────────────────

class TestCallbacks:
    def test_on_order_called_on_submit(self):
        cb = MagicMock()
        b = MockBridge()
        b.register_callback(cb)
        b.buy("600519.SH", 1800.0, 100)
        assert cb.on_order.called

    def test_on_trade_called_on_fill(self):
        cb = MagicMock()
        b = MockBridge()
        b.register_callback(cb)
        b.buy("600519.SH", 1800.0, 100)
        assert cb.on_trade.called
        trade = cb.on_trade.call_args[0][0]
        assert trade["volume"] == 100

    def test_on_order_called_on_cancel(self):
        cb = MagicMock()
        b = make_bridge(fill_mode="never")
        b.register_callback(cb)
        result = b.buy("600519.SH", 1800.0, 100)
        cb.reset_mock()
        b.cancel(result["order_id"])
        cb.on_order.assert_called_once()
        order = cb.on_order.call_args[0][0]
        assert order["status"] == STATUS_CANCELED

    def test_callback_exception_does_not_propagate(self):
        cb = MagicMock()
        cb.on_order.side_effect = RuntimeError("boom")
        b = MockBridge()
        b.register_callback(cb)
        b.buy("600519.SH", 1800.0, 100)  # must not raise

    def test_no_callback_registered_is_fine(self):
        b = MockBridge()
        b.buy("600519.SH", 1800.0, 100)  # must not raise


# ── helpers ───────────────────────────────────────────────────────────────────

class TestHelpers:
    def test_set_position(self):
        b = MockBridge()
        b.set_position("600519.SH", 300, 1750.0)
        pos = b.get_positions()
        assert pos[0]["volume"] == 300
        assert pos[0]["avg_price"] == 1750.0

    def test_mark_to_market(self):
        b = MockBridge()
        b.set_position("600519.SH", 100, 1800.0)
        b.mark_to_market({"600519.SH": 1900.0})
        pos = b.get_positions()
        assert pos[0]["market_value"] == pytest.approx(1900.0 * 100)

    def test_zero_volume_position_excluded_from_get_positions(self):
        b = MockBridge()
        b.set_position("600519.SH", 100, 1800.0)
        b.set_position("600519.SH", 0, 0.0)
        assert b.get_positions() == []
