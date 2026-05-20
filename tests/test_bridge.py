"""Unit tests for qmt-bridge.

HTTP mocking via `responses` library.
xtquant mocked via sys.modules injection — no real miniQMT needed.
"""
from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import responses as responses_lib

from qmt_bridge.daqmt_bridge import DaQMTBridge
from qmt_bridge.xtquant_bridge import XtQuantBridge
from qmt_bridge.bridge import QMTBridge


BASE = "http://127.0.0.1:9000"


# ============================================================
# DaQMTBridge tests
# ============================================================


class TestDaQMTBridgeAvailability:
    @responses_lib.activate
    def test_is_available_true(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/total",
            json={"total_money": 100000.0},
            status=200,
        )
        bridge = DaQMTBridge(base_url=BASE)
        assert bridge.is_available() is True

    @responses_lib.activate
    def test_is_available_false_on_connection_error(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/total",
            body=ConnectionError("refused"),
        )
        bridge = DaQMTBridge(base_url=BASE)
        assert bridge.is_available() is False

    @responses_lib.activate
    def test_is_available_false_on_500(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/total",
            status=500,
        )
        bridge = DaQMTBridge(base_url=BASE)
        assert bridge.is_available() is False


class TestDaQMTBridgeBalance:
    @responses_lib.activate
    def test_get_balance_schema_keys(self):
        # _pick uses "total", "total_asset", "money" for total
        # _pick uses "available", "money", "available_cash" for available
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/total",
            json={"total": 500000.0},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/available",
            json={"available": 120000.0},
            status=200,
        )
        bridge = DaQMTBridge(base_url=BASE)
        result = bridge.get_balance()

        assert isinstance(result, dict)
        for key in ("available", "frozen_cash", "market_value", "total_asset", "raw"):
            assert key in result, f"missing key: {key}"
        assert result["total_asset"] == 500000.0
        assert result["available"] == 120000.0
        assert result["frozen_cash"] == 0.0
        assert result["market_value"] == 0.0

    @responses_lib.activate
    def test_get_balance_fallback_keys(self):
        """Server returns total_asset / available_cash — _pick should still find them."""
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/total",
            json={"total_asset": 99999.0},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/available",
            json={"available_cash": 30000.0},
            status=200,
        )
        bridge = DaQMTBridge(base_url=BASE)
        result = bridge.get_balance()
        assert result["total_asset"] == 99999.0
        assert result["available"] == 30000.0

    @responses_lib.activate
    def test_get_balance_zeros_on_unknown_keys(self):
        """total_money is NOT in _pick list -> should default to 0."""
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/total",
            json={"total_money": 123456.0},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/available",
            json={"available_money": 50000.0},
            status=200,
        )
        bridge = DaQMTBridge(base_url=BASE)
        result = bridge.get_balance()
        # Neither key is in _pick list -> default 0.0
        assert result["total_asset"] == 0.0
        assert result["available"] == 0.0


class TestDaQMTBridgePositions:
    @responses_lib.activate
    def test_get_positions_m_prefix_fields(self):
        holdings = {
            "600519": {
                "m_nVolume": 200,
                "m_nCanUseVolume": 100,
                "m_dOpenPrice": 1800.5,
                "m_dMarketValue": 360100.0,
                "m_nFrozenVolume": 0,
                "m_nYesterdayVolume": 200,
            }
        }
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/holding",
            json={"holdings": holdings},
            status=200,
        )
        bridge = DaQMTBridge(base_url=BASE)
        positions = bridge.get_positions()

        assert len(positions) == 1
        pos = positions[0]
        for key in ("code", "volume", "available", "avg_price", "market_value",
                    "frozen_volume", "yesterday_volume"):
            assert key in pos, f"missing key: {key}"
        assert pos["code"] == "600519"
        assert pos["volume"] == 200
        assert pos["available"] == 100
        assert pos["avg_price"] == 1800.5
        assert pos["market_value"] == 360100.0

    @responses_lib.activate
    def test_get_positions_empty_holdings(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/holding",
            json={"holdings": {}},
            status=200,
        )
        bridge = DaQMTBridge(base_url=BASE)
        assert bridge.get_positions() == []

    @responses_lib.activate
    def test_server_error_returns_empty_positions(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/holding",
            status=500,
        )
        bridge = DaQMTBridge(base_url=BASE)
        assert bridge.get_positions() == []


class TestDaQMTBridgeOrders:
    @responses_lib.activate
    def test_get_today_orders_returns_list(self):
        order = {
            "order_id": "ORD001",
            "code": "600519",
            "direction": "buy",
            "price": 1800.0,
            "volume": 100,
            "filled_volume": 0,
            "status": "pending",
            "order_time": "2026-05-21 09:30:00",
        }
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/order/status",
            json={"orders": [order]},
            status=200,
        )
        bridge = DaQMTBridge(base_url=BASE)
        orders = bridge.get_today_orders()
        assert isinstance(orders, list)
        assert len(orders) == 1
        assert orders[0]["code"] == "600519"

    @responses_lib.activate
    def test_get_today_orders_empty(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/order/status",
            json={"orders": []},
            status=200,
        )
        bridge = DaQMTBridge(base_url=BASE)
        assert bridge.get_today_orders() == []

    def test_get_today_trades_always_empty(self):
        bridge = DaQMTBridge(base_url=BASE)
        result = bridge.get_today_trades()
        assert result == []


class TestDaQMTBridgeTrading:
    @responses_lib.activate
    def test_buy_posts_correct_json(self):
        responses_lib.add(
            responses_lib.POST,
            f"{BASE}/api/order/buy",
            json={"order_id": "BUY001", "status": "ok"},
            status=200,
        )
        bridge = DaQMTBridge(base_url=BASE)
        result = bridge.buy("600519", 1800.0, 100)

        assert len(responses_lib.calls) == 1
        req_body = responses_lib.calls[0].request.body
        import json
        body = json.loads(req_body)
        assert body["stock"] == "600519"
        assert body["price"] == 1800.0
        assert body["volume"] == 100
        assert body["prType"] == 11

    @responses_lib.activate
    def test_sell_posts_correct_json(self):
        responses_lib.add(
            responses_lib.POST,
            f"{BASE}/api/order/sell",
            json={"order_id": "SELL001", "status": "ok"},
            status=200,
        )
        bridge = DaQMTBridge(base_url=BASE)
        result = bridge.sell("600519", 1810.0, 100, pr_type=11)

        assert len(responses_lib.calls) == 1
        req_body = responses_lib.calls[0].request.body
        import json
        body = json.loads(req_body)
        assert body["stock"] == "600519"
        assert body["price"] == 1810.0
        assert body["volume"] == 100
        assert body["prType"] == 11

    @responses_lib.activate
    def test_cancel_all_posts(self):
        responses_lib.add(
            responses_lib.POST,
            f"{BASE}/api/order/cancel_all",
            json={"status": "ok"},
            status=200,
        )
        bridge = DaQMTBridge(base_url=BASE)
        result = bridge.cancel_all()
        assert len(responses_lib.calls) == 1
        assert isinstance(result, dict)

    @responses_lib.activate
    def test_buy_server_error_returns_error_dict(self):
        responses_lib.add(
            responses_lib.POST,
            f"{BASE}/api/order/buy",
            status=500,
        )
        bridge = DaQMTBridge(base_url=BASE)
        result = bridge.buy("600519", 1800.0, 100)
        assert "error" in result


# ============================================================
# XtQuantBridge tests (sys.modules injection)
# ============================================================


def _make_xtquant_mock():
    """Build a minimal xtquant mock hierarchy for sys.modules injection."""
    xtquant = MagicMock()

    # xttrader mock
    trader_instance = MagicMock()
    trader_instance.connect.return_value = 0
    trader_instance.subscribe.return_value = 0
    trader_class = MagicMock(return_value=trader_instance)
    xttrader_mod = MagicMock()
    xttrader_mod.XtQuantTrader = trader_class

    # xttype mock
    stock_account_instance = MagicMock()
    stock_account_class = MagicMock(return_value=stock_account_instance)
    xttype_mod = MagicMock()
    xttype_mod.StockAccount = stock_account_class

    # xtconstant mock
    xtconstant_mod = MagicMock()
    xtconstant_mod.STOCK_BUY = 23
    xtconstant_mod.STOCK_SELL = 24
    xtconstant_mod.FIX_PRICE = 11

    xtquant.xttrader = xttrader_mod
    xtquant.xttype = xttype_mod
    xtquant.xtconstant = xtconstant_mod

    return xtquant, xttrader_mod, xttype_mod, xtconstant_mod, trader_instance, stock_account_instance


class TestXtQuantBridgeNoXtquant:
    def test_connect_returns_false_when_no_xtquant(self):
        """xtquant not installed -> connect() returns False."""
        # Ensure xtquant is absent from sys.modules
        for key in list(sys.modules.keys()):
            if key.startswith("xtquant"):
                del sys.modules[key]

        bridge = XtQuantBridge({"mini_qmt_path": "/fake", "account_id": "test123"})
        result = bridge.connect()
        assert result is False

    def test_is_available_false_when_not_connected(self):
        bridge = XtQuantBridge()
        assert bridge.is_available() is False

    def test_get_balance_returns_empty_when_not_connected(self):
        bridge = XtQuantBridge()
        assert bridge.get_balance() == {}

    def test_get_positions_returns_empty_when_not_connected(self):
        bridge = XtQuantBridge()
        assert bridge.get_positions() == []

    def test_get_today_orders_returns_empty_when_not_connected(self):
        bridge = XtQuantBridge()
        assert bridge.get_today_orders() == []

    def test_get_today_trades_returns_empty_when_not_connected(self):
        bridge = XtQuantBridge()
        assert bridge.get_today_trades() == []

    def test_buy_not_connected_returns_error_dict(self):
        bridge = XtQuantBridge()
        result = bridge.buy("000001", 10.0, 100)
        assert isinstance(result, dict)
        assert "error" in result

    def test_sell_not_connected_returns_error_dict(self):
        bridge = XtQuantBridge()
        result = bridge.sell("000001", 10.0, 100)
        assert isinstance(result, dict)
        assert "error" in result

    def test_cancel_not_connected_returns_false(self):
        bridge = XtQuantBridge()
        assert bridge.cancel(12345) is False


class TestXtQuantBridgeConnected:
    """Tests with mocked xtquant available."""

    def _make_connected_bridge(self):
        (xtquant_mock, xttrader_mod, xttype_mod,
         xtconstant_mod, trader_instance, account_instance) = _make_xtquant_mock()

        mods = {
            "xtquant": xtquant_mock,
            "xtquant.xttrader": xttrader_mod,
            "xtquant.xttype": xttype_mod,
            "xtquant.xtconstant": xtconstant_mod,
        }
        with patch.dict(sys.modules, mods):
            bridge = XtQuantBridge({"mini_qmt_path": "/fake", "account_id": "88888888"})
            connected = bridge.connect()

        # Re-inject so methods can import xtconstant at call time
        for k, v in mods.items():
            sys.modules[k] = v

        return bridge, trader_instance, account_instance, mods

    def test_connect_succeeds_with_mock(self):
        (xtquant_mock, xttrader_mod, xttype_mod,
         xtconstant_mod, trader_instance, account_instance) = _make_xtquant_mock()
        mods = {
            "xtquant": xtquant_mock,
            "xtquant.xttrader": xttrader_mod,
            "xtquant.xttype": xttype_mod,
            "xtquant.xtconstant": xtconstant_mod,
        }
        with patch.dict(sys.modules, mods):
            bridge = XtQuantBridge({"mini_qmt_path": "/fake", "account_id": "88888888"})
            result = bridge.connect()
        assert result is True
        assert bridge.connected is True

    def test_get_balance_connected(self):
        bridge, trader_instance, account_instance, mods = self._make_connected_bridge()
        asset = MagicMock()
        asset.cash = 50000.0
        asset.frozen_cash = 0.0
        asset.market_value = 100000.0
        asset.total_asset = 150000.0
        asset.account_type = "stock"
        asset.account_id = "88888888"
        trader_instance.query_stock_asset.return_value = asset

        with patch.dict(sys.modules, mods):
            result = bridge.get_balance()

        assert result["available"] == 50000.0
        assert result["total_asset"] == 150000.0
        assert "frozen_cash" in result
        assert "market_value" in result
        assert "raw" in result

    def test_get_positions_connected(self):
        bridge, trader_instance, account_instance, mods = self._make_connected_bridge()
        pos = MagicMock()
        pos.stock_code = "000001"
        pos.volume = 500
        pos.can_use_volume = 500
        pos.avg_price = 12.5
        pos.market_value = 6250.0
        pos.frozen_volume = 0
        pos.yesterday_volume = 500
        trader_instance.query_stock_positions.return_value = [pos]

        with patch.dict(sys.modules, mods):
            positions = bridge.get_positions()

        assert len(positions) == 1
        p = positions[0]
        assert p["code"] == "000001"
        assert p["volume"] == 500
        assert p["avg_price"] == 12.5

    def test_buy_connected(self):
        bridge, trader_instance, account_instance, mods = self._make_connected_bridge()
        trader_instance.order_stock.return_value = 9001

        with patch.dict(sys.modules, mods):
            result = bridge.buy("000001", 12.5, 100)

        assert result.get("order_id") == 9001
        assert result.get("status") == "submitted"


# ============================================================
# QMTBridge tests
# ============================================================


class TestQMTBridgeAutoMode:
    @responses_lib.activate
    def test_auto_mode_uses_daqmt_when_available(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/total",
            json={"total": 100000.0},
            status=200,
        )
        bridge = QMTBridge({"mode": "auto", "base_url": BASE})
        connected = bridge.connect()
        assert connected is True
        assert bridge.mode_used() == "daqmt"

    def test_auto_mode_falls_back_to_none_when_both_unavailable(self):
        # No HTTP mock -> DaQMTBridge.is_available() will fail (connection refused)
        # xtquant not installed -> XtQuantBridge.connect() returns False
        for key in list(sys.modules.keys()):
            if key.startswith("xtquant"):
                del sys.modules[key]

        bridge = QMTBridge({"mode": "auto", "base_url": "http://127.0.0.1:19999"})
        connected = bridge.connect()
        assert connected is False
        assert bridge.mode_used() == "none"

    @responses_lib.activate
    def test_explicit_daqmt_mode(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/total",
            json={"total": 200000.0},
            status=200,
        )
        bridge = QMTBridge({"mode": "daqmt", "base_url": BASE})
        connected = bridge.connect()
        assert connected is True
        assert bridge.mode_used() == "daqmt"

    def test_explicit_miniqmt_mode_no_xtquant(self):
        for key in list(sys.modules.keys()):
            if key.startswith("xtquant"):
                del sys.modules[key]

        bridge = QMTBridge({"mode": "miniqmt"})
        connected = bridge.connect()
        assert connected is False

    @responses_lib.activate
    def test_get_balance_delegates_to_daqmt(self):
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/total",
            json={"total": 300000.0},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/total",
            json={"total": 300000.0},
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{BASE}/api/money/available",
            json={"available": 80000.0},
            status=200,
        )
        bridge = QMTBridge({"mode": "daqmt", "base_url": BASE})
        bridge.connect()
        result = bridge.get_balance()
        assert isinstance(result, dict)
        assert result["total_asset"] == 300000.0

    def test_not_connected_get_balance_returns_empty(self):
        bridge = QMTBridge({"mode": "daqmt", "base_url": "http://127.0.0.1:19999"})
        # Don't connect — _active is None
        result = bridge.get_balance()
        assert result == {}

    def test_not_connected_get_positions_returns_empty(self):
        bridge = QMTBridge()
        result = bridge.get_positions()
        assert result == []

    def test_not_connected_buy_returns_error(self):
        bridge = QMTBridge()
        result = bridge.buy("600519", 1800.0, 100)
        assert "error" in result

    def test_is_available_false_before_connect(self):
        bridge = QMTBridge()
        assert bridge.is_available() is False

    @responses_lib.activate
    def test_mode_used_none_before_connect(self):
        bridge = QMTBridge()
        assert bridge.mode_used() == "none"
