"""Tests for XtDataBridge — xtquant.xtdata mocked via sys.modules."""
from __future__ import annotations

import datetime
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from qmt_bridge.xtdata_bridge import XtDataBridge, _parse_qmt_time


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

@contextmanager
def xtdata_mock():
    mock = MagicMock()
    mods = {
        "xtquant": MagicMock(xtdata=mock),
        "xtquant.xtdata": mock,
    }
    with patch.dict(sys.modules, mods):
        yield mock


def _clear_xtquant():
    for key in list(sys.modules.keys()):
        if key.startswith("xtquant"):
            del sys.modules[key]


# ──────────────────────────────────────────────────────────────
# _parse_qmt_time
# ──────────────────────────────────────────────────────────────

class TestParseQmtTime:
    def test_standard(self):
        dt = _parse_qmt_time(20240103093015123)
        assert dt == datetime.datetime(2024, 1, 3, 9, 30, 15, 123000)

    def test_zero_ms(self):
        dt = _parse_qmt_time(20240101093000000)
        assert dt is not None
        assert dt.year == 2024 and dt.microsecond == 0

    def test_invalid_date_returns_none(self):
        assert _parse_qmt_time(20241399000000000) is None


# ──────────────────────────────────────────────────────────────
# is_available
# ──────────────────────────────────────────────────────────────

class TestIsAvailable:
    def test_true_when_importable(self):
        with xtdata_mock():
            assert XtDataBridge().is_available() is True

    def test_false_when_absent(self):
        _clear_xtquant()
        assert XtDataBridge().is_available() is False


# ──────────────────────────────────────────────────────────────
# get_klines
# ──────────────────────────────────────────────────────────────

_KLINE_RAW = {
    "600519.SH": {
        "time":   [20240103093000000, 20240104093000000],
        "open":   [1800.0, 1810.0],
        "high":   [1820.0, 1830.0],
        "low":    [1795.0, 1800.0],
        "close":  [1810.0, 1820.0],
        "volume": [10000.0, 12000.0],
        "amount": [18100000.0, 21840000.0],
    }
}


class TestGetKlines:
    def test_returns_polars_dataframe(self):
        with xtdata_mock() as xd:
            xd.get_market_data_ex.return_value = _KLINE_RAW
            df = XtDataBridge().get_klines(["600519.SH"], period="1d", count=2)
        assert isinstance(df, pl.DataFrame)

    def test_required_columns(self):
        with xtdata_mock() as xd:
            xd.get_market_data_ex.return_value = _KLINE_RAW
            df = XtDataBridge().get_klines(["600519.SH"])
        for col in ("code", "time", "open", "high", "low", "close", "volume", "amount"):
            assert col in df.columns

    def test_row_count(self):
        with xtdata_mock() as xd:
            xd.get_market_data_ex.return_value = _KLINE_RAW
            df = XtDataBridge().get_klines(["600519.SH"])
        assert len(df) == 2

    def test_code_column(self):
        with xtdata_mock() as xd:
            xd.get_market_data_ex.return_value = _KLINE_RAW
            df = XtDataBridge().get_klines(["600519.SH"])
        assert all(c == "600519.SH" for c in df["code"].to_list())

    def test_multi_stock(self):
        raw = {
            "000001.SZ": {
                "time": [20240103093000000], "open": [10.0], "high": [10.5],
                "low": [9.8], "close": [10.2], "volume": [5000.0], "amount": [51000.0],
            },
            "600519.SH": {
                "time": [20240103093000000], "open": [1800.0], "high": [1820.0],
                "low": [1795.0], "close": [1810.0], "volume": [10000.0], "amount": [18100000.0],
            },
        }
        with xtdata_mock() as xd:
            xd.get_market_data_ex.return_value = raw
            df = XtDataBridge().get_klines(["000001.SZ", "600519.SH"])
        assert len(df) == 2
        assert set(df["code"].to_list()) == {"000001.SZ", "600519.SH"}

    def test_empty_raw_returns_empty_schema(self):
        with xtdata_mock() as xd:
            xd.get_market_data_ex.return_value = {}
            df = XtDataBridge().get_klines(["600519.SH"])
        assert isinstance(df, pl.DataFrame) and len(df) == 0

    def test_xtdata_unavailable_returns_empty(self):
        _clear_xtquant()
        df = XtDataBridge().get_klines(["600519.SH"])
        assert isinstance(df, pl.DataFrame) and len(df) == 0

    def test_api_exception_returns_empty(self):
        with xtdata_mock() as xd:
            xd.get_market_data_ex.side_effect = RuntimeError("network error")
            df = XtDataBridge().get_klines(["600519.SH"])
        assert isinstance(df, pl.DataFrame) and len(df) == 0


# ──────────────────────────────────────────────────────────────
# get_latest_quote
# ──────────────────────────────────────────────────────────────

def _quote_raw():
    return {
        "time":      {"600519.SH": [20240103145959000]},
        "lastPrice": {"600519.SH": [1810.0]},
        "open":      {"600519.SH": [1800.0]},
        "high":      {"600519.SH": [1820.0]},
        "low":       {"600519.SH": [1795.0]},
        "lastClose": {"600519.SH": [1805.0]},
        "amount":    {"600519.SH": [18100000.0]},
        "volume":    {"600519.SH": [10000.0]},
        "bidPrice":  {"600519.SH": [[1809.0, 1808.0]]},
        "askPrice":  {"600519.SH": [[1811.0, 1812.0]]},
        "bidVol":    {"600519.SH": [[100.0, 200.0]]},
        "askVol":    {"600519.SH": [[150.0, 300.0]]},
    }


class TestGetLatestQuote:
    def test_required_columns(self):
        with xtdata_mock() as xd:
            xd.get_market_data.return_value = _quote_raw()
            df = XtDataBridge().get_latest_quote(["600519.SH"])
        for col in ("code", "last_price", "open", "high", "low", "prev_close",
                    "bid1", "ask1", "bid_vol1", "ask_vol1"):
            assert col in df.columns

    def test_price_values(self):
        with xtdata_mock() as xd:
            xd.get_market_data.return_value = _quote_raw()
            df = XtDataBridge().get_latest_quote(["600519.SH"])
        assert df["last_price"][0] == 1810.0
        assert df["bid1"][0] == 1809.0
        assert df["ask1"][0] == 1811.0

    def test_empty_raw_returns_empty(self):
        with xtdata_mock() as xd:
            xd.get_market_data.return_value = {}
            df = XtDataBridge().get_latest_quote(["600519.SH"])
        assert len(df) == 0

    def test_unavailable_returns_empty(self):
        _clear_xtquant()
        df = XtDataBridge().get_latest_quote(["600519.SH"])
        assert isinstance(df, pl.DataFrame) and len(df) == 0


# ──────────────────────────────────────────────────────────────
# get_full_tick
# ──────────────────────────────────────────────────────────────

def _tick_obj():
    tick = MagicMock()
    tick.time = 20240103145959000
    tick.lastPrice = 1810.0
    tick.volume = 10000.0
    tick.amount = 18100000.0
    tick.bidPrice = [1809.0, 1808.0, 1807.0, 1806.0, 1805.0]
    tick.askPrice = [1811.0, 1812.0, 1813.0, 1814.0, 1815.0]
    tick.bidVol   = [100.0, 200.0, 300.0, 400.0, 500.0]
    tick.askVol   = [150.0, 250.0, 350.0, 450.0, 550.0]
    return tick


class TestGetFullTick:
    def test_5_level_columns(self):
        with xtdata_mock() as xd:
            xd.get_full_tick.return_value = {"600519.SH": _tick_obj()}
            df = XtDataBridge().get_full_tick(["600519.SH"])
        for i in range(1, 6):
            assert f"bid{i}" in df.columns
            assert f"ask{i}" in df.columns

    def test_price_values(self):
        with xtdata_mock() as xd:
            xd.get_full_tick.return_value = {"600519.SH": _tick_obj()}
            df = XtDataBridge().get_full_tick(["600519.SH"])
        assert df["last_price"][0] == 1810.0
        assert df["bid1"][0] == 1809.0
        assert df["ask5"][0] == 1815.0

    def test_empty_returns_empty(self):
        with xtdata_mock() as xd:
            xd.get_full_tick.return_value = {}
            df = XtDataBridge().get_full_tick(["600519.SH"])
        assert len(df) == 0

    def test_unavailable_returns_empty(self):
        _clear_xtquant()
        df = XtDataBridge().get_full_tick(["600519.SH"])
        assert isinstance(df, pl.DataFrame) and len(df) == 0


# ──────────────────────────────────────────────────────────────
# get_index_weight
# ──────────────────────────────────────────────────────────────

class TestGetIndexWeight:
    def test_dataframe_shape(self):
        with xtdata_mock() as xd:
            xd.get_index_weight.return_value = {"600519.SH": 0.025, "000001.SZ": 0.01}
            df = XtDataBridge().get_index_weight("000300.SH")
        assert isinstance(df, pl.DataFrame)
        assert set(df.columns) >= {"index_code", "code", "weight"}
        assert len(df) == 2

    def test_index_code_populated(self):
        with xtdata_mock() as xd:
            xd.get_index_weight.return_value = {"600519.SH": 0.025}
            df = XtDataBridge().get_index_weight("000300.SH")
        assert df["index_code"][0] == "000300.SH"

    def test_empty_returns_empty(self):
        with xtdata_mock() as xd:
            xd.get_index_weight.return_value = {}
            df = XtDataBridge().get_index_weight("000300.SH")
        assert len(df) == 0

    def test_unavailable_returns_empty(self):
        _clear_xtquant()
        df = XtDataBridge().get_index_weight("000300.SH")
        assert isinstance(df, pl.DataFrame) and len(df) == 0


# ──────────────────────────────────────────────────────────────
# get_instrument_type
# ──────────────────────────────────────────────────────────────

class TestGetInstrumentType:
    def test_returns_type_string(self):
        with xtdata_mock() as xd:
            xd.get_instrument_type.return_value = "stock"
            assert XtDataBridge().get_instrument_type("600519.SH") == "stock"

    def test_fallback_from_detail_attribute(self):
        # InstrumentType=1 (index) — avoids the falsy 0 issue in the or-chain fallback
        with xtdata_mock() as xd:
            xd.get_instrument_type.side_effect = AttributeError
            detail = MagicMock()
            detail.InstrumentType = 1
            xd.get_instrument_detail.return_value = detail
            result = XtDataBridge().get_instrument_type("600519.SH")
        assert result == "index"

    def test_unknown_on_unavailable(self):
        _clear_xtquant()
        assert XtDataBridge().get_instrument_type("600519.SH") == "unknown"


# ──────────────────────────────────────────────────────────────
# Simple list-returning wrappers
# ──────────────────────────────────────────────────────────────

class TestSimpleWrappers:
    def test_get_stock_list(self):
        with xtdata_mock() as xd:
            xd.get_stock_list_in_sector.return_value = ["600519.SH", "000001.SZ"]
            result = XtDataBridge().get_stock_list("沪深A股")
        assert "600519.SH" in result

    def test_get_stock_list_unavailable(self):
        _clear_xtquant()
        assert XtDataBridge().get_stock_list() == []

    def test_get_instrument_detail_dict(self):
        with xtdata_mock() as xd:
            xd.get_instrument_detail.return_value = {"StockCode": "600519.SH"}
            result = XtDataBridge().get_instrument_detail("600519.SH")
        assert result["StockCode"] == "600519.SH"

    def test_get_instrument_detail_unavailable(self):
        _clear_xtquant()
        assert XtDataBridge().get_instrument_detail("600519.SH") == {}

    def test_get_trading_dates(self):
        with xtdata_mock() as xd:
            xd.get_trading_dates.return_value = [20240102, 20240103]
            result = XtDataBridge().get_trading_dates("SSE", "20240101", "20240131")
        assert result == ["20240102", "20240103"]

    def test_get_trading_dates_unavailable(self):
        _clear_xtquant()
        assert XtDataBridge().get_trading_dates() == []

    def test_get_sector_list(self):
        with xtdata_mock() as xd:
            xd.get_sector_list.return_value = ["沪深A股", "上证50"]
            result = XtDataBridge().get_sector_list()
        assert "沪深A股" in result

    def test_get_sector_list_unavailable(self):
        _clear_xtquant()
        assert XtDataBridge().get_sector_list() == []

    def test_get_period_list_from_xtdata(self):
        with xtdata_mock() as xd:
            xd.get_period_list.return_value = ["1d", "1m", "tick"]
            result = XtDataBridge().get_period_list()
        assert "1d" in result

    def test_get_period_list_fallback(self):
        _clear_xtquant()
        result = XtDataBridge().get_period_list()
        assert {"1d", "1m", "tick"}.issubset(set(result))


# ──────────────────────────────────────────────────────────────
# Subscriptions
# ──────────────────────────────────────────────────────────────

class TestSubscriptions:
    def test_subscribe_quote_returns_seq(self):
        with xtdata_mock() as xd:
            xd.subscribe_quote.return_value = 42
            assert XtDataBridge().subscribe_quote(["600519.SH"]) == 42

    def test_subscribe_quote_unavailable(self):
        _clear_xtquant()
        assert XtDataBridge().subscribe_quote(["600519.SH"]) == -1

    def test_unsubscribe_quote_true(self):
        with xtdata_mock() as xd:
            xd.unsubscribe_quote.return_value = None
            assert XtDataBridge().unsubscribe_quote(42) is True

    def test_unsubscribe_quote_unavailable(self):
        _clear_xtquant()
        assert XtDataBridge().unsubscribe_quote(42) is False

    def test_subscribe_whole_quote(self):
        with xtdata_mock() as xd:
            xd.subscribe_whole_quote.return_value = 99
            assert XtDataBridge().subscribe_whole_quote(markets=["SH", "SZ"]) == 99

    def test_unsubscribe_whole_quote(self):
        with xtdata_mock() as xd:
            xd.unsubscribe_whole_quote.return_value = None
            assert XtDataBridge().unsubscribe_whole_quote(99) is True


# ──────────────────────────────────────────────────────────────
# Download methods
# ──────────────────────────────────────────────────────────────

class TestDownloads:
    def test_download_history_data_true(self):
        with xtdata_mock() as xd:
            xd.download_history_data.return_value = None
            assert XtDataBridge().download_history_data("600519.SH") is True

    def test_download_history_data_unavailable(self):
        _clear_xtquant()
        assert XtDataBridge().download_history_data("600519.SH") is False

    def test_download_with_progress_true(self):
        with xtdata_mock() as xd:
            xd.download_history_data2.return_value = None
            assert XtDataBridge().download_with_progress(["600519.SH"]) is True

    def test_download_index_weight_true(self):
        with xtdata_mock() as xd:
            xd.download_index_weight.return_value = None
            assert XtDataBridge().download_index_weight("000300.SH") is True

    def test_download_sector_data_true(self):
        with xtdata_mock() as xd:
            xd.download_sector_data.return_value = None
            assert XtDataBridge().download_sector_data() is True

    def test_download_financial_data_true(self):
        with xtdata_mock() as xd:
            xd.download_financial_data.return_value = None
            assert XtDataBridge().download_financial_data("600519.SH") is True

    def test_download_financial_data2_true(self):
        with xtdata_mock() as xd:
            xd.download_financial_data2.return_value = None
            assert XtDataBridge().download_financial_data2(["600519.SH"]) is True

    def test_download_cb_data_true(self):
        with xtdata_mock() as xd:
            xd.download_cb_data.return_value = None
            assert XtDataBridge().download_cb_data() is True

    def test_download_etf_info_true(self):
        with xtdata_mock() as xd:
            xd.download_etf_info.return_value = None
            assert XtDataBridge().download_etf_info() is True


# ──────────────────────────────────────────────────────────────
# connect / server status
# ──────────────────────────────────────────────────────────────

class TestConnectionMethods:
    def test_connect_returns_0(self):
        with xtdata_mock() as xd:
            xd.connect.return_value = 0
            assert XtDataBridge().connect() == 0

    def test_connect_unavailable_returns_minus_1(self):
        _clear_xtquant()
        assert XtDataBridge().connect() == -1

    def test_get_quote_server_status_returns_dict(self):
        with xtdata_mock() as xd:
            xd.get_quote_server_status.return_value = {"connected": True, "server": "127.0.0.1"}
            result = XtDataBridge().get_quote_server_status()
        assert isinstance(result, dict)

    def test_get_quote_server_status_unavailable(self):
        _clear_xtquant()
        assert XtDataBridge().get_quote_server_status() == {}

    def test_watch_quote_server_status_seq(self):
        with xtdata_mock() as xd:
            xd.watch_quote_server_status.return_value = 7
            assert XtDataBridge().watch_quote_server_status() == 7

    def test_watch_quote_server_status_unavailable(self):
        _clear_xtquant()
        assert XtDataBridge().watch_quote_server_status() == -1


# ──────────────────────────────────────────────────────────────
# Sector management
# ──────────────────────────────────────────────────────────────

class TestSectorManagement:
    def test_create_sector_folder_true(self):
        with xtdata_mock() as xd:
            xd.create_sector_folder.return_value = None
            assert XtDataBridge().create_sector_folder("my_folder") is True

    def test_create_sector_true(self):
        with xtdata_mock() as xd:
            xd.create_sector.return_value = None
            assert XtDataBridge().create_sector("my_sector") is True

    def test_add_stock_to_sector_true(self):
        with xtdata_mock() as xd:
            xd.add_sector.return_value = None
            assert XtDataBridge().add_stock_to_sector("my_sector", ["600519.SH"]) is True

    def test_remove_sector_true(self):
        with xtdata_mock() as xd:
            xd.remove_sector.return_value = None
            assert XtDataBridge().remove_sector("my_sector") is True

    def test_reset_sector_true(self):
        with xtdata_mock() as xd:
            xd.reset_sector.return_value = None
            assert XtDataBridge().reset_sector("my_sector", ["600519.SH"]) is True
