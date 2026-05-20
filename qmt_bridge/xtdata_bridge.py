"""Market data bridge via xtquant.xtdata (direct in-process, Polars output).

xtdata reads from QMT's local data cache -- no network, no IPC.
Always available on machines where 大QMT or miniQMT is installed.
"""
from __future__ import annotations

from loguru import logger

try:
    import polars as pl
    _POLARS_AVAILABLE = True
except ImportError:
    logger.warning("XtDataBridge: polars not installed; methods return list[dict] fallback")
    pl = None  # type: ignore
    _POLARS_AVAILABLE = False


def _empty_klines_schema():
    """Return an empty Polars DataFrame with the canonical klines schema."""
    return pl.DataFrame(schema={
        "code": pl.Utf8,
        "time": pl.Datetime,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "close": pl.Float64,
        "volume": pl.Float64,
        "amount": pl.Float64,
    })


def _empty_quote_schema():
    return pl.DataFrame(schema={
        "code": pl.Utf8,
        "time": pl.Datetime,
        "last_price": pl.Float64,
        "open": pl.Float64,
        "high": pl.Float64,
        "low": pl.Float64,
        "prev_close": pl.Float64,
        "amount": pl.Float64,
        "volume": pl.Float64,
        "bid1": pl.Float64,
        "ask1": pl.Float64,
        "bid_vol1": pl.Float64,
        "ask_vol1": pl.Float64,
    })


def _empty_tick_schema():
    cols = {
        "code": pl.Utf8,
        "time": pl.Datetime,
        "last_price": pl.Float64,
        "volume": pl.Float64,
        "amount": pl.Float64,
    }
    for i in range(1, 6):
        cols[f"bid{i}"] = pl.Float64
        cols[f"ask{i}"] = pl.Float64
        cols[f"bid_vol{i}"] = pl.Float64
        cols[f"ask_vol{i}"] = pl.Float64
    return pl.DataFrame(schema=cols)


def _parse_qmt_time(ts_int: int):
    """Convert QMT integer timestamp (20240101093000000) to Python datetime.

    Format: YYYYMMDDHHmmssmmm (17 digits). The trailing 3 digits are milliseconds.
    """
    import datetime
    s = str(int(ts_int)).zfill(17)
    year   = int(s[0:4])
    month  = int(s[4:6])
    day    = int(s[6:8])
    hour   = int(s[8:10])
    minute = int(s[10:12])
    second = int(s[12:14])
    ms     = int(s[14:17])
    try:
        return datetime.datetime(year, month, day, hour, minute, second, ms * 1000)
    except ValueError:
        return None


class XtDataBridge:
    """Market data via xtquant.xtdata (in-process, Polars output).

    config keys:
        download_timeout: int  -- seconds to wait for download_history_data2 (default 30)
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._download_timeout: int = int(self.config.get("download_timeout", 30))

    def is_available(self) -> bool:
        """Return True if xtquant.xtdata can be imported."""
        try:
            import xtquant.xtdata  # type: ignore  # noqa: F401
            return True
        except (ImportError, Exception):
            return False

    def get_klines(
        self,
        codes: list[str],
        period: str = "1d",
        start: str = "",
        end: str = "",
        count: int = -1,
        adjust: str = "forward",
    ) -> "pl.DataFrame":
        """Fetch historical K-lines for one or more codes.

        Returns a Polars DataFrame with columns:
            code, time, open, high, low, close, volume, amount
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_klines: xtdata unavailable: %s", e)
            return _empty_klines_schema()

        try:
            raw = xtdata.get_market_data_ex(
                field_list=["time", "open", "high", "low", "close", "volume", "amount"],
                stock_list=codes,
                period=period,
                start_time=start,
                end_time=end,
                count=count,
                dividend_type=adjust,
                fill_data=True,
            )
        except Exception as e:
            logger.error("XtDataBridge.get_klines: get_market_data_ex failed: %s", e)
            return _empty_klines_schema()

        if not raw:
            return _empty_klines_schema()

        frames = []
        for code, field_dict in raw.items():
            if not isinstance(field_dict, dict):
                continue
            try:
                import numpy as np  # type: ignore
                times_raw = field_dict.get("time", [])
                n = len(times_raw)
                if n == 0:
                    continue

                parsed_times = [_parse_qmt_time(t) for t in times_raw]

                df = pl.DataFrame({
                    "code": [code] * n,
                    "time": parsed_times,
                    "open":   [float(v) for v in field_dict.get("open",   [0.0] * n)],
                    "high":   [float(v) for v in field_dict.get("high",   [0.0] * n)],
                    "low":    [float(v) for v in field_dict.get("low",    [0.0] * n)],
                    "close":  [float(v) for v in field_dict.get("close",  [0.0] * n)],
                    "volume": [float(v) for v in field_dict.get("volume", [0.0] * n)],
                    "amount": [float(v) for v in field_dict.get("amount", [0.0] * n)],
                }).with_columns(pl.col("time").cast(pl.Datetime))
                frames.append(df)
            except Exception as e:
                logger.error("XtDataBridge.get_klines: failed to build df for %s: %s", code, e)

        if not frames:
            return _empty_klines_schema()

        try:
            return pl.concat(frames)
        except Exception as e:
            logger.error("XtDataBridge.get_klines: concat failed: %s", e)
            return _empty_klines_schema()

    def get_latest_quote(self, codes: list[str]) -> "pl.DataFrame":
        """Fetch latest tick quote for one or more codes.

        Returns a Polars DataFrame with columns:
            code, time, last_price, open, high, low, prev_close,
            amount, volume, bid1, ask1, bid_vol1, ask_vol1
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_latest_quote: xtdata unavailable: %s", e)
            return _empty_quote_schema()

        try:
            raw = xtdata.get_market_data(
                field_list=[
                    "time", "lastPrice", "open", "high", "low", "lastClose",
                    "amount", "volume", "bidPrice", "askPrice", "bidVol", "askVol",
                ],
                stock_list=codes,
                period="tick",
                count=1,
            )
        except Exception as e:
            logger.error("XtDataBridge.get_latest_quote: get_market_data failed: %s", e)
            return _empty_quote_schema()

        if not raw:
            return _empty_quote_schema()

        rows = []
        for code in codes:
            try:
                def _scalar(field, default=0.0):
                    arr = raw.get(field, {}).get(code, [default])
                    v = arr[-1] if hasattr(arr, "__len__") and len(arr) > 0 else default
                    return v

                time_raw = _scalar("time", 0)
                parsed_time = _parse_qmt_time(int(time_raw)) if time_raw else None

                bid_prices = raw.get("bidPrice", {}).get(code, [[]])
                ask_prices = raw.get("askPrice", {}).get(code, [[]])
                bid_vols   = raw.get("bidVol", {}).get(code, [[]])
                ask_vols   = raw.get("askVol", {}).get(code, [[]])

                def _level(arr, idx, default=0.0):
                    try:
                        row = arr[-1] if hasattr(arr, "__len__") and len(arr) > 0 else []
                        return float(row[idx]) if hasattr(row, "__len__") and len(row) > idx else default
                    except Exception:
                        return default

                rows.append({
                    "code":       code,
                    "time":       parsed_time,
                    "last_price": float(_scalar("lastPrice", 0.0)),
                    "open":       float(_scalar("open", 0.0)),
                    "high":       float(_scalar("high", 0.0)),
                    "low":        float(_scalar("low", 0.0)),
                    "prev_close": float(_scalar("lastClose", 0.0)),
                    "amount":     float(_scalar("amount", 0.0)),
                    "volume":     float(_scalar("volume", 0.0)),
                    "bid1":       _level(bid_prices, 0),
                    "ask1":       _level(ask_prices, 0),
                    "bid_vol1":   _level(bid_vols, 0),
                    "ask_vol1":   _level(ask_vols, 0),
                })
            except Exception as e:
                logger.error("XtDataBridge.get_latest_quote: failed for %s: %s", code, e)

        if not rows:
            return _empty_quote_schema()

        try:
            return pl.DataFrame(rows).with_columns(pl.col("time").cast(pl.Datetime))
        except Exception as e:
            logger.error("XtDataBridge.get_latest_quote: DataFrame build failed: %s", e)
            return _empty_quote_schema()

    def get_full_tick(self, codes: list[str]) -> "pl.DataFrame":
        """Fetch full 5-level tick data for one or more codes.

        Returns a Polars DataFrame with columns:
            code, time, last_price, volume, amount,
            bid1..bid5, ask1..ask5, bid_vol1..bid_vol5, ask_vol1..ask_vol5
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_full_tick: xtdata unavailable: %s", e)
            return _empty_tick_schema()

        try:
            raw = xtdata.get_full_tick(codes)
        except Exception as e:
            logger.error("XtDataBridge.get_full_tick: get_full_tick failed: %s", e)
            return _empty_tick_schema()

        if not raw:
            return _empty_tick_schema()

        rows = []
        for code in codes:
            tick = raw.get(code)
            if not tick:
                continue
            try:
                def _f(key, default=0.0):
                    return float(getattr(tick, key, default) or default)

                def _bid(i):
                    arr = getattr(tick, "bidPrice", [])
                    return float(arr[i]) if hasattr(arr, "__len__") and len(arr) > i else 0.0

                def _ask(i):
                    arr = getattr(tick, "askPrice", [])
                    return float(arr[i]) if hasattr(arr, "__len__") and len(arr) > i else 0.0

                def _bv(i):
                    arr = getattr(tick, "bidVol", [])
                    return float(arr[i]) if hasattr(arr, "__len__") and len(arr) > i else 0.0

                def _av(i):
                    arr = getattr(tick, "askVol", [])
                    return float(arr[i]) if hasattr(arr, "__len__") and len(arr) > i else 0.0

                time_raw = getattr(tick, "time", 0) or 0
                parsed_time = _parse_qmt_time(int(time_raw)) if time_raw else None

                row = {
                    "code":       code,
                    "time":       parsed_time,
                    "last_price": _f("lastPrice"),
                    "volume":     _f("volume"),
                    "amount":     _f("amount"),
                }
                for i in range(1, 6):
                    row[f"bid{i}"]     = _bid(i - 1)
                    row[f"ask{i}"]     = _ask(i - 1)
                    row[f"bid_vol{i}"] = _bv(i - 1)
                    row[f"ask_vol{i}"] = _av(i - 1)
                rows.append(row)
            except Exception as e:
                logger.error("XtDataBridge.get_full_tick: failed for %s: %s", code, e)

        if not rows:
            return _empty_tick_schema()

        try:
            return pl.DataFrame(rows).with_columns(pl.col("time").cast(pl.Datetime))
        except Exception as e:
            logger.error("XtDataBridge.get_full_tick: DataFrame build failed: %s", e)
            return _empty_tick_schema()

    def get_stock_list(self, sector: str = "沪深A股") -> list[str]:
        """Return list of stock codes in the given sector."""
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            result = xtdata.get_stock_list_in_sector(sector)
            return list(result) if result else []
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_stock_list failed: %s", e)
            return []

    def get_instrument_detail(self, code: str) -> dict:
        """Return instrument detail dict for a given code."""
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            detail = xtdata.get_instrument_detail(code)
            if detail is None:
                return {}
            if isinstance(detail, dict):
                return detail
            # Object with attributes
            return {k: getattr(detail, k) for k in dir(detail) if not k.startswith("_")}
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_instrument_detail(%s) failed: %s", code, e)
            return {}

    def download(
        self,
        codes: list[str],
        period: str = "1d",
        start: str = "",
        end: str = "",
    ) -> bool:
        """Download historical data via xtdata.download_history_data2.

        Blocks until done or timeout (config["download_timeout"] seconds).
        Returns True on success, False on any error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.download: xtdata unavailable: %s", e)
            return False

        try:
            xtdata.download_history_data2(codes, period, start, end)
            return True
        except Exception as e:
            logger.error("XtDataBridge.download failed: %s", e)
            return False
