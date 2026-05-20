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

        Supported periods: "tick", "1m", "5m", "15m", "30m", "1h",
                           "1d", "1w", "1mon", "1q", "1hy", "1y".
        The period string is passed directly to xtdata without transformation.

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

    # ------------------------------------------------------------------
    # Trading calendar
    # ------------------------------------------------------------------

    def get_trading_dates(
        self,
        exchange: str = "SSE",
        start: str = "",
        end: str = "",
    ) -> list[str]:
        """Return trading date strings for an exchange.

        Args:
            exchange: "SSE" (上交所) or "SZSE" (深交所).
            start: start date string e.g. "20240101".
            end:   end date string e.g. "20241231".

        Returns list of date strings like ["20240102", "20240103", ...].
        Returns [] on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            result = xtdata.get_trading_dates(
                market=exchange,
                start_time=start,
                end_time=end,
            )
            if result is None:
                return []
            return [str(d) for d in result]
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_trading_dates failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Financial data
    # ------------------------------------------------------------------

    def get_financial_data(
        self,
        codes: list[str],
        tables: "list[str] | None" = None,
        start: str = "",
        end: str = "",
    ) -> "pl.DataFrame":
        """Fetch financial statement data for a list of codes.

        Args:
            codes:  list of stock codes.
            tables: subset of ["Balance", "Income", "CashFlow", "Capital", "Holders"].
                    None means all tables.
            start:  start date string.
            end:    end date string.

        Returns a flat Polars DataFrame with columns:
            code, table, report_date, + all financial fields present in the data.
        Returns empty DataFrame on error.
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_financial_data: xtdata unavailable: %s", e)
            return pl.DataFrame()

        try:
            raw = xtdata.get_financial_data(
                stock_list=codes,
                table_list=tables,
                start_time=start,
                end_time=end,
            )
        except Exception as e:
            logger.error("XtDataBridge.get_financial_data failed: %s", e)
            return pl.DataFrame()

        if not raw:
            return pl.DataFrame()

        # raw: {table_name: {code: {report_date_or_field: value}}}
        # or   {table_name: {code: list_of_dicts}}
        rows = []
        try:
            for table_name, code_dict in raw.items():
                if not isinstance(code_dict, dict):
                    continue
                for code, data in code_dict.items():
                    if isinstance(data, list):
                        # list of records
                        for record in data:
                            if isinstance(record, dict):
                                row = {"code": code, "table": table_name}
                                row.update(record)
                                rows.append(row)
                    elif isinstance(data, dict):
                        row = {"code": code, "table": table_name}
                        row.update(data)
                        rows.append(row)
        except Exception as e:
            logger.error("XtDataBridge.get_financial_data: flatten failed: %s", e)
            return pl.DataFrame()

        if not rows:
            return pl.DataFrame()

        try:
            return pl.DataFrame(rows)
        except Exception as e:
            logger.error("XtDataBridge.get_financial_data: DataFrame build failed: %s", e)
            return pl.DataFrame()

    # ------------------------------------------------------------------
    # Index weights
    # ------------------------------------------------------------------

    def get_index_weight(self, index_code: str) -> "pl.DataFrame":
        """Fetch constituent weights for an index.

        Returns a Polars DataFrame with columns: index_code, code, weight.
        Returns empty DataFrame on error.
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_index_weight: xtdata unavailable: %s", e)
            return pl.DataFrame(schema={"index_code": pl.Utf8, "code": pl.Utf8, "weight": pl.Float64})

        try:
            raw = xtdata.get_index_weight(index_code=index_code)
        except Exception as e:
            logger.error("XtDataBridge.get_index_weight(%s) failed: %s", index_code, e)
            return pl.DataFrame(schema={"index_code": pl.Utf8, "code": pl.Utf8, "weight": pl.Float64})

        if not raw:
            return pl.DataFrame(schema={"index_code": pl.Utf8, "code": pl.Utf8, "weight": pl.Float64})

        try:
            # raw is {code: weight_float}
            codes_list = list(raw.keys())
            weights = [float(raw[c]) for c in codes_list]
            return pl.DataFrame({
                "index_code": [index_code] * len(codes_list),
                "code": codes_list,
                "weight": weights,
            })
        except Exception as e:
            logger.error("XtDataBridge.get_index_weight: DataFrame build failed: %s", e)
            return pl.DataFrame(schema={"index_code": pl.Utf8, "code": pl.Utf8, "weight": pl.Float64})

    # ------------------------------------------------------------------
    # Dividend / split factors
    # ------------------------------------------------------------------

    def get_divid_factors(
        self,
        code: str,
        start: str = "",
        end: str = "",
    ) -> "pl.DataFrame":
        """Fetch dividend and split adjustment factors for a stock.

        Returns a Polars DataFrame with columns: code, date, + factor fields.
        Returns empty DataFrame on error.
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_divid_factors: xtdata unavailable: %s", e)
            return pl.DataFrame()

        try:
            raw = xtdata.get_divid_factors(
                stock_code=code,
                start_time=start,
                end_time=end,
            )
        except Exception as e:
            logger.error("XtDataBridge.get_divid_factors(%s) failed: %s", code, e)
            return pl.DataFrame()

        if not raw:
            return pl.DataFrame()

        try:
            if isinstance(raw, list):
                rows = []
                for item in raw:
                    if isinstance(item, dict):
                        row = {"code": code}
                        row.update(item)
                        rows.append(row)
                    elif hasattr(item, "__dict__"):
                        row = {"code": code}
                        row.update(vars(item))
                        rows.append(row)
                if not rows:
                    return pl.DataFrame()
                return pl.DataFrame(rows)
            elif isinstance(raw, dict):
                # {date_str: factor_dict} or flat dict
                rows = []
                for k, v in raw.items():
                    if isinstance(v, dict):
                        row = {"code": code, "date": str(k)}
                        row.update(v)
                        rows.append(row)
                    else:
                        rows.append({"code": code, "date": str(k), "factor": float(v or 0)})
                if not rows:
                    return pl.DataFrame()
                return pl.DataFrame(rows)
            else:
                logger.warning("XtDataBridge.get_divid_factors: unexpected type %s", type(raw))
                return pl.DataFrame()
        except Exception as e:
            logger.error("XtDataBridge.get_divid_factors: DataFrame build failed: %s", e)
            return pl.DataFrame()

    # ------------------------------------------------------------------
    # Sector list
    # ------------------------------------------------------------------

    def get_sector_list(self) -> list[str]:
        """Return all available sector names.

        Returns [] on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            result = xtdata.get_sector_list()
            if result is None:
                return []
            return list(result)
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_sector_list failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # Instrument type
    # ------------------------------------------------------------------

    def get_instrument_type(self, code: str) -> str:
        """Derive instrument type from code suffix or instrument detail.

        Returns one of: "stock", "index", "fund", "etf", "bond", "cb",
                        "future", "option", "unknown".
        Returns "unknown" on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_instrument_type: xtdata unavailable: %s", e)
            return "unknown"

        # Try native API first
        try:
            result = xtdata.get_instrument_type(code)
            if result:
                return str(result).lower()
        except Exception:
            pass

        # Fallback: derive from instrument_detail
        try:
            detail = xtdata.get_instrument_detail(code)
            if detail is None:
                return "unknown"
            instrument_type = (
                getattr(detail, "InstrumentType", None)
                or (detail.get("InstrumentType") if isinstance(detail, dict) else None)
            )
            if instrument_type is not None:
                _type_map = {
                    0: "stock",
                    1: "index",
                    2: "fund",
                    3: "etf",
                    4: "bond",
                    5: "cb",
                    6: "future",
                    7: "option",
                }
                return _type_map.get(int(instrument_type), "unknown")
        except Exception as e:
            logger.warning("XtDataBridge.get_instrument_type(%s) fallback failed: %s", code, e)

        return "unknown"

    # ------------------------------------------------------------------
    # Holidays
    # ------------------------------------------------------------------

    def get_holidays(self, exchange: str = "SSE") -> list[str]:
        """Return holiday date strings for the given exchange.

        Tries xtdata.get_holidays first; falls back to deriving from
        get_trading_dates vs natural calendar days.

        Returns list of date strings like ["20240101", ...].
        Returns [] on error.
        """
        import datetime

        # Try native API
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            result = xtdata.get_holidays(market=exchange)
            if result is not None:
                return [str(d) for d in result]
        except (ImportError, AttributeError):
            pass
        except Exception as e:
            logger.warning("XtDataBridge.get_holidays native call failed: %s", e)

        # Fallback: derive from trading calendar for current year
        try:
            now = datetime.date.today()
            year_start = f"{now.year}0101"
            year_end = f"{now.year}1231"
            trading = set(self.get_trading_dates(exchange=exchange, start=year_start, end=year_end))
            if not trading:
                return []
            start_d = datetime.date(now.year, 1, 1)
            end_d = datetime.date(now.year, 12, 31)
            holidays = []
            current = start_d
            while current <= end_d:
                if current.weekday() < 5:  # Mon-Fri
                    ds = current.strftime("%Y%m%d")
                    if ds not in trading:
                        holidays.append(ds)
                current += datetime.timedelta(days=1)
            return holidays
        except Exception as e:
            logger.warning("XtDataBridge.get_holidays fallback failed: %s", e)
            return []

    # ------------------------------------------------------------------
    # L2 data
    # ------------------------------------------------------------------

    def get_l2_quote(self, codes: list[str]) -> "pl.DataFrame":
        """Fetch L2 full order book snapshot (10-level bid/ask).

        Requires L2 subscription from broker. Returns empty DataFrame if
        L2 is not available or on any error.

        Columns: code, time, last_price, volume, amount,
                 bid1..bid10, ask1..ask10, bid_vol1..bid_vol10, ask_vol1..ask_vol10
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_l2_quote: xtdata unavailable: %s", e)
            return pl.DataFrame()

        try:
            raw = xtdata.get_l2_quote(stock_list=codes)
        except Exception as e:
            logger.warning("XtDataBridge.get_l2_quote failed (L2 may require subscription): %s", e)
            return pl.DataFrame()

        if not raw:
            return pl.DataFrame()

        rows = []
        for code in codes:
            entry = raw.get(code)
            if not entry:
                continue
            try:
                def _attr(obj, key, default=0.0):
                    v = getattr(obj, key, None) if not isinstance(obj, dict) else obj.get(key)
                    return float(v or default)

                def _list_attr(obj, key):
                    v = getattr(obj, key, None) if not isinstance(obj, dict) else obj.get(key, [])
                    return list(v or [])

                time_raw = getattr(entry, "time", None) or (entry.get("time") if isinstance(entry, dict) else None) or 0
                parsed_time = _parse_qmt_time(int(time_raw)) if time_raw else None

                bid_prices = _list_attr(entry, "bidPrice")
                ask_prices = _list_attr(entry, "askPrice")
                bid_vols   = _list_attr(entry, "bidVol")
                ask_vols   = _list_attr(entry, "askVol")

                row: dict = {
                    "code":       code,
                    "time":       parsed_time,
                    "last_price": _attr(entry, "lastPrice"),
                    "volume":     _attr(entry, "volume"),
                    "amount":     _attr(entry, "amount"),
                }
                for i in range(10):
                    row[f"bid{i+1}"]     = float(bid_prices[i]) if i < len(bid_prices) else 0.0
                    row[f"ask{i+1}"]     = float(ask_prices[i]) if i < len(ask_prices) else 0.0
                    row[f"bid_vol{i+1}"] = float(bid_vols[i])   if i < len(bid_vols)   else 0.0
                    row[f"ask_vol{i+1}"] = float(ask_vols[i])   if i < len(ask_vols)   else 0.0
                rows.append(row)
            except Exception as e:
                logger.error("XtDataBridge.get_l2_quote: failed for %s: %s", code, e)

        if not rows:
            return pl.DataFrame()

        try:
            return pl.DataFrame(rows).with_columns(pl.col("time").cast(pl.Datetime))
        except Exception as e:
            logger.error("XtDataBridge.get_l2_quote: DataFrame build failed: %s", e)
            return pl.DataFrame()

    def get_l2_order(self, codes: list[str]) -> "pl.DataFrame":
        """Fetch L2 individual order entries for a list of codes.

        Columns: code, order_id, direction, price, volume, time.
        Returns empty DataFrame on error or if L2 not subscribed.
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_l2_order: xtdata unavailable: %s", e)
            return pl.DataFrame()

        try:
            raw = xtdata.get_l2_order(stock_list=codes)
        except Exception as e:
            logger.warning("XtDataBridge.get_l2_order failed (L2 may require subscription): %s", e)
            return pl.DataFrame()

        if not raw:
            return pl.DataFrame()

        rows = []
        for code in codes:
            entries = raw.get(code, [])
            if not entries:
                continue
            if not isinstance(entries, list):
                entries = [entries]
            for entry in entries:
                try:
                    if isinstance(entry, dict):
                        row = {"code": code}
                        row.update(entry)
                    else:
                        row = {
                            "code":      code,
                            "order_id":  getattr(entry, "orderId", None) or getattr(entry, "order_id", None),
                            "direction": getattr(entry, "direction", None),
                            "price":     float(getattr(entry, "price", 0) or 0),
                            "volume":    float(getattr(entry, "volume", 0) or 0),
                            "time":      getattr(entry, "time", None),
                        }
                    rows.append(row)
                except Exception as e:
                    logger.error("XtDataBridge.get_l2_order: failed for %s entry: %s", code, e)

        if not rows:
            return pl.DataFrame()

        try:
            return pl.DataFrame(rows)
        except Exception as e:
            logger.error("XtDataBridge.get_l2_order: DataFrame build failed: %s", e)
            return pl.DataFrame()

    def get_l2_transaction(self, codes: list[str]) -> "pl.DataFrame":
        """Fetch L2 individual trade records (tick trades) for a list of codes.

        Columns: code, trade_id, price, volume, direction, time.
        Returns empty DataFrame on error or if L2 not subscribed.
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_l2_transaction: xtdata unavailable: %s", e)
            return pl.DataFrame()

        try:
            raw = xtdata.get_l2_transaction(stock_list=codes)
        except Exception as e:
            logger.warning("XtDataBridge.get_l2_transaction failed (L2 may require subscription): %s", e)
            return pl.DataFrame()

        if not raw:
            return pl.DataFrame()

        rows = []
        for code in codes:
            entries = raw.get(code, [])
            if not entries:
                continue
            if not isinstance(entries, list):
                entries = [entries]
            for entry in entries:
                try:
                    if isinstance(entry, dict):
                        row = {"code": code}
                        row.update(entry)
                    else:
                        row = {
                            "code":      code,
                            "trade_id":  getattr(entry, "tradeId", None) or getattr(entry, "trade_id", None),
                            "price":     float(getattr(entry, "price", 0) or 0),
                            "volume":    float(getattr(entry, "volume", 0) or 0),
                            "direction": getattr(entry, "direction", None),
                            "time":      getattr(entry, "time", None),
                        }
                    rows.append(row)
                except Exception as e:
                    logger.error("XtDataBridge.get_l2_transaction: failed for %s entry: %s", code, e)

        if not rows:
            return pl.DataFrame()

        try:
            return pl.DataFrame(rows)
        except Exception as e:
            logger.error("XtDataBridge.get_l2_transaction: DataFrame build failed: %s", e)
            return pl.DataFrame()

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def subscribe_quote(
        self,
        codes: list[str],
        period: str = "tick",
        callback: "Callable | None" = None,
        start: str = "",
        end: str = "",
    ) -> int:
        """Subscribe to real-time quote updates for a list of codes.

        Args:
            codes:    list of stock codes to subscribe.
            period:   data period, e.g. "tick", "1m", "1d".
            callback: called on each update with signature callback(data: dict) -> None.
            start:    optional start time string.
            end:      optional end time string.

        Returns subscription id (int). Returns -1 on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            seq = xtdata.subscribe_quote(
                stock_code=codes,
                period=period,
                start_time=start,
                end_time=end,
                callback=callback,
            )
            return int(seq) if seq is not None else -1
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.subscribe_quote failed: %s", e)
            return -1

    def unsubscribe_quote(self, seq: int) -> bool:
        """Unsubscribe a real-time quote subscription by id.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.unsubscribe_quote(seq)
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.unsubscribe_quote(%s) failed: %s", seq, e)
            return False

    def subscribe_whole_quote(
        self,
        markets: "list[str] | None" = None,
        callback: "Callable | None" = None,
    ) -> int:
        """Subscribe to whole-market real-time quote feed.

        Args:
            markets:  list of market codes e.g. ["SH", "SZ"], or None for all.
            callback: called on each update.

        Returns subscription id, -1 on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            seq = xtdata.subscribe_whole_quote(
                code_list=markets,
                callback=callback,
            )
            return int(seq) if seq is not None else -1
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.subscribe_whole_quote failed: %s", e)
            return -1

    def unsubscribe_whole_quote(self, seq: int) -> bool:
        """Unsubscribe a whole-market quote subscription by id.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.unsubscribe_whole_quote(seq)
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.unsubscribe_whole_quote(%s) failed: %s", seq, e)
            return False

    # ------------------------------------------------------------------
    # ETF info
    # ------------------------------------------------------------------

    def get_etf_info(self, code: str) -> dict:
        """Fetch ETF-specific metadata for a given code.

        Tries xtdata.get_etf_info first; falls back to get_instrument_detail.
        Returns {} on error or if code is not an ETF.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_etf_info: xtdata unavailable: %s", e)
            return {}

        # Try native ETF API
        try:
            result = xtdata.get_etf_info(stock_code=code)
            if result is not None:
                if isinstance(result, dict):
                    return result
                return {k: getattr(result, k) for k in dir(result) if not k.startswith("_")}
        except AttributeError:
            pass
        except Exception as e:
            logger.warning("XtDataBridge.get_etf_info native call failed for %s: %s", code, e)

        # Fallback: get_instrument_detail
        try:
            detail = xtdata.get_instrument_detail(code)
            if detail is None:
                return {}
            if isinstance(detail, dict):
                return detail
            return {k: getattr(detail, k) for k in dir(detail) if not k.startswith("_")}
        except Exception as e:
            logger.warning("XtDataBridge.get_etf_info fallback failed for %s: %s", code, e)
            return {}

    # ------------------------------------------------------------------
    # Convertible bond info
    # ------------------------------------------------------------------

    def get_cb_info(self, codes: list[str]) -> "pl.DataFrame":
        """Fetch convertible bond (CB) metadata for a list of codes.

        Columns: cb_code, stock_code, convert_price, maturity_date, + CB-specific fields.
        Returns empty DataFrame on error or if CB data is unavailable.
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_cb_info: xtdata unavailable: %s", e)
            return pl.DataFrame()

        try:
            raw = xtdata.get_cb_info(stock_list=codes)
        except (AttributeError, Exception) as e:
            logger.warning("XtDataBridge.get_cb_info failed: %s", e)
            return pl.DataFrame()

        if not raw:
            return pl.DataFrame()

        rows = []
        try:
            if isinstance(raw, dict):
                for code, info in raw.items():
                    if isinstance(info, dict):
                        row = {"cb_code": code}
                        row.update(info)
                        rows.append(row)
                    elif info is not None and hasattr(info, "__dict__"):
                        row = {"cb_code": code}
                        row.update(vars(info))
                        rows.append(row)
            elif isinstance(raw, list):
                for item in raw:
                    if isinstance(item, dict):
                        rows.append(item)
                    elif hasattr(item, "__dict__"):
                        rows.append(vars(item))
        except Exception as e:
            logger.error("XtDataBridge.get_cb_info: flatten failed: %s", e)
            return pl.DataFrame()

        if not rows:
            return pl.DataFrame()

        try:
            return pl.DataFrame(rows)
        except Exception as e:
            logger.error("XtDataBridge.get_cb_info: DataFrame build failed: %s", e)
            return pl.DataFrame()

    def _get_xtdata(self):
        """Return the xtquant.xtdata module, or None if unavailable."""
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            return xtdata
        except (ImportError, Exception):
            return None

    def connect(self) -> int:
        """Connect xtdata to the quote server independently of XtQuantTrader.

        Required before calling get_market_data / subscribe_quote when used
        without XtQuantTrader (xtdata-only mode).
        Returns 0 on success, non-zero on failure, -1 if unavailable.
        """
        try:
            xtdata = self._get_xtdata()
            if xtdata is None:
                return -1
            return xtdata.connect() or 0
        except Exception as e:
            logger.warning(f"XtDataBridge.connect() failed: {e}")
            return -1

    def get_quote_server_status(self) -> dict:
        """Get current quote server connection status.

        Returns dict with server status info, {} on error.
        """
        try:
            xtdata = self._get_xtdata()
            if xtdata is None:
                return {}
            result = xtdata.get_quote_server_status()
            return result if isinstance(result, dict) else {}
        except Exception as e:
            logger.warning(f"XtDataBridge.get_quote_server_status() failed: {e}")
            return {}

    def watch_quote_server_status(self, callback: "Callable | None" = None) -> int:
        """Subscribe to quote server status changes.

        callback: called when server status changes
        Returns subscription id, -1 on error.
        """
        try:
            xtdata = self._get_xtdata()
            if xtdata is None:
                return -1
            seq = xtdata.watch_quote_server_status(callback=callback)
            return seq if seq is not None else -1
        except Exception as e:
            logger.warning(f"XtDataBridge.watch_quote_server_status() failed: {e}")
            return -1

    def get_option_detail_data(self, code: str) -> dict:
        """Get option contract detail data.

        Returns option-specific fields: strike price, expiry, option type etc.
        Returns {} on error or if code is not an option.
        """
        try:
            xtdata = self._get_xtdata()
            if xtdata is None:
                return {}
            result = xtdata.get_option_detail_data(stock_code=code)
            return result if isinstance(result, dict) else {}
        except Exception as e:
            logger.warning(f"XtDataBridge.get_option_detail_data({code}) failed: {e}")
            return {}

    def download_history_data(
        self, code: str, period: str = "1d", start: str = "", end: str = ""
    ) -> bool:
        """Single-stock historical data download (legacy API).

        Prefer download() or download_with_progress() for batch downloads.
        """
        try:
            xtdata = self._get_xtdata()
            if xtdata is None:
                return False
            xtdata.download_history_data(
                stock_code=code, period=period, start_time=start, end_time=end
            )
            return True
        except Exception as e:
            logger.warning(f"XtDataBridge.download_history_data({code}) failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Download with progress
    # ------------------------------------------------------------------

    def download_with_progress(
        self,
        codes: list[str],
        period: str = "1d",
        start: str = "",
        end: str = "",
        callback: "Callable | None" = None,
    ) -> bool:
        """Download historical data with incremental progress callback.

        Args:
            codes:    list of stock codes.
            period:   data period string, e.g. "1d", "1m".
            start:    start date string.
            end:      end date string.
            callback: optional progress callback with signature
                      callback(code, download_count, total_count) -> None.

        Returns True on success, False on any error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.download_with_progress: xtdata unavailable: %s", e)
            return False

        try:
            xtdata.download_history_data2(
                stock_list=codes,
                period=period,
                start_time=start,
                end_time=end,
                incrementally=True,
                callback=callback,
            )
            return True
        except Exception as e:
            logger.error("XtDataBridge.download_with_progress failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Group 1: Session lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the xtdata event loop.

        MUST be called after subscribe_quote / subscribe_whole_quote to
        receive push callbacks. Blocks the calling thread — run in a
        separate thread if needed. No-op if xtdata is unavailable.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.run()
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.run: %s", e)

    def stop(self) -> None:
        """Stop the xtdata event loop.

        No-op if xtdata is unavailable or does not expose stop().
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            stop_fn = getattr(xtdata, "stop", None)
            if callable(stop_fn):
                stop_fn()
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.stop: %s", e)

    # ------------------------------------------------------------------
    # Group 2: Local data query
    # ------------------------------------------------------------------

    def get_local_data(
        self,
        codes: list[str],
        period: str = "1d",
        start: str = "",
        end: str = "",
        count: int = -1,
    ) -> "pl.DataFrame":
        """Read already-downloaded local cache (no network fetch).

        Same output schema as get_klines.
        Returns empty DataFrame on error.
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_local_data: xtdata unavailable: %s", e)
            return _empty_klines_schema()

        try:
            raw = xtdata.get_local_data(
                field_list=[],
                stock_list=codes,
                period=period,
                start_time=start,
                end_time=end,
                count=count,
            )
        except Exception as e:
            logger.error("XtDataBridge.get_local_data failed: %s", e)
            return _empty_klines_schema()

        if not raw:
            return _empty_klines_schema()

        frames = []
        for code, field_dict in raw.items():
            if not isinstance(field_dict, dict):
                continue
            try:
                times_raw = field_dict.get("time", [])
                n = len(times_raw)
                if n == 0:
                    continue
                parsed_times = [_parse_qmt_time(t) for t in times_raw]
                df = pl.DataFrame({
                    "code":   [code] * n,
                    "time":   parsed_times,
                    "open":   [float(v) for v in field_dict.get("open",   [0.0] * n)],
                    "high":   [float(v) for v in field_dict.get("high",   [0.0] * n)],
                    "low":    [float(v) for v in field_dict.get("low",    [0.0] * n)],
                    "close":  [float(v) for v in field_dict.get("close",  [0.0] * n)],
                    "volume": [float(v) for v in field_dict.get("volume", [0.0] * n)],
                    "amount": [float(v) for v in field_dict.get("amount", [0.0] * n)],
                }).with_columns(pl.col("time").cast(pl.Datetime))
                frames.append(df)
            except Exception as e:
                logger.error("XtDataBridge.get_local_data: failed for %s: %s", code, e)

        if not frames:
            return _empty_klines_schema()

        try:
            return pl.concat(frames)
        except Exception as e:
            logger.error("XtDataBridge.get_local_data: concat failed: %s", e)
            return _empty_klines_schema()

    def get_full_kline(
        self,
        codes: list[str],
        period: str = "1d",
        start: str = "",
        end: str = "",
        count: int = -1,
    ) -> "pl.DataFrame":
        """Fetch extended OHLCV + additional fields vs get_klines.

        Returns a Polars DataFrame; same base schema as get_klines but may
        include extra columns depending on xtdata version.
        Returns empty DataFrame on error.
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_full_kline: xtdata unavailable: %s", e)
            return _empty_klines_schema()

        try:
            raw = xtdata.get_full_kline(
                stock_list=codes,
                period=period,
                start_time=start,
                end_time=end,
                count=count,
            )
        except Exception as e:
            logger.error("XtDataBridge.get_full_kline failed: %s", e)
            return _empty_klines_schema()

        if not raw:
            return _empty_klines_schema()

        frames = []
        for code, field_dict in raw.items():
            if not isinstance(field_dict, dict):
                continue
            try:
                times_raw = field_dict.get("time", [])
                n = len(times_raw)
                if n == 0:
                    continue
                parsed_times = [_parse_qmt_time(t) for t in times_raw]
                row_data: dict = {
                    "code":   [code] * n,
                    "time":   parsed_times,
                    "open":   [float(v) for v in field_dict.get("open",   [0.0] * n)],
                    "high":   [float(v) for v in field_dict.get("high",   [0.0] * n)],
                    "low":    [float(v) for v in field_dict.get("low",    [0.0] * n)],
                    "close":  [float(v) for v in field_dict.get("close",  [0.0] * n)],
                    "volume": [float(v) for v in field_dict.get("volume", [0.0] * n)],
                    "amount": [float(v) for v in field_dict.get("amount", [0.0] * n)],
                }
                # Include any extra fields xtdata returns beyond the base schema
                for extra_key, extra_vals in field_dict.items():
                    if extra_key not in row_data and hasattr(extra_vals, "__len__") and len(extra_vals) == n:
                        try:
                            row_data[extra_key] = [float(v) for v in extra_vals]
                        except Exception:
                            pass
                df = pl.DataFrame(row_data).with_columns(pl.col("time").cast(pl.Datetime))
                frames.append(df)
            except Exception as e:
                logger.error("XtDataBridge.get_full_kline: failed for %s: %s", code, e)

        if not frames:
            return _empty_klines_schema()

        try:
            return pl.concat(frames, how="diagonal")
        except Exception as e:
            logger.error("XtDataBridge.get_full_kline: concat failed: %s", e)
            return _empty_klines_schema()

    # ------------------------------------------------------------------
    # Group 3: Download methods
    # ------------------------------------------------------------------

    def download_index_weight(self, index_code: str = "") -> bool:
        """Download index constituent weight data.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            if index_code:
                xtdata.download_index_weight(index_code=index_code)
            else:
                xtdata.download_index_weight()
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.download_index_weight failed: %s", e)
            return False

    def download_financial_data(self, code: str, table: str = "") -> bool:
        """Download financial statement data for a single stock code.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.download_financial_data(stock_code=code, table_name=table)
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.download_financial_data(%s) failed: %s", code, e)
            return False

    def download_financial_data2(
        self, codes: list[str], tables: "list[str] | None" = None
    ) -> bool:
        """Batch-download financial statement data for multiple codes.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.download_financial_data2(
                stock_list=codes,
                table_list=tables or [],
            )
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.download_financial_data2 failed: %s", e)
            return False

    def download_sector_data(self) -> bool:
        """Download all sector/industry classification data.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.download_sector_data()
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.download_sector_data failed: %s", e)
            return False

    def download_holiday_data(self) -> bool:
        """Download holiday calendar data.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.download_holiday_data()
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.download_holiday_data failed: %s", e)
            return False

    def download_cb_data(self, codes: "list[str] | None" = None) -> bool:
        """Download convertible bond data.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            if codes is not None:
                xtdata.download_cb_data(stock_list=codes)
            else:
                xtdata.download_cb_data()
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.download_cb_data failed: %s", e)
            return False

    def download_etf_info(self, codes: "list[str] | None" = None) -> bool:
        """Download ETF metadata.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            if codes is not None:
                xtdata.download_etf_info(stock_list=codes)
            else:
                xtdata.download_etf_info()
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.download_etf_info failed: %s", e)
            return False

    def download_history_contracts(self) -> bool:
        """Download historical futures/options contract specs.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.download_history_contracts()
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.download_history_contracts failed: %s", e)
            return False

    # ------------------------------------------------------------------
    # Group 4: Sector / watchlist management
    # ------------------------------------------------------------------

    def create_sector_folder(self, folder_name: str) -> bool:
        """Create a sector folder.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.create_sector_folder(folder_name)
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.create_sector_folder(%s) failed: %s", folder_name, e)
            return False

    def create_sector(self, sector_name: str) -> bool:
        """Create a new sector.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.create_sector(sector_name)
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.create_sector(%s) failed: %s", sector_name, e)
            return False

    def add_stock_to_sector(self, sector_name: str, codes: list[str]) -> bool:
        """Add stocks to an existing sector.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.add_sector(sector_name=sector_name, stock_list=codes)
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.add_stock_to_sector(%s) failed: %s", sector_name, e)
            return False

    def remove_stock_from_sector(self, sector_name: str, codes: list[str]) -> bool:
        """Remove stocks from a sector.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.remove_stock_from_sector(sector_name=sector_name, stock_list=codes)
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.remove_stock_from_sector(%s) failed: %s", sector_name, e)
            return False

    def remove_sector(self, sector_name: str) -> bool:
        """Delete a sector entirely.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.remove_sector(sector_name)
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.remove_sector(%s) failed: %s", sector_name, e)
            return False

    def reset_sector(self, sector_name: str, codes: list[str]) -> bool:
        """Replace entire sector contents with a new stock list.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.reset_sector(sector_name=sector_name, stock_list=codes)
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.reset_sector(%s) failed: %s", sector_name, e)
            return False

    # ------------------------------------------------------------------
    # Group 5: Formula engine
    # ------------------------------------------------------------------

    def call_formula(
        self,
        formula_name: str,
        code: str,
        period: str,
        start: str = "",
        end: str = "",
        count: int = -1,
        **params,
    ) -> "pl.DataFrame":
        """Evaluate a named formula / indicator for a single code.

        Returns indicator values as a Polars DataFrame.
        Returns empty DataFrame on error.
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.call_formula: xtdata unavailable: %s", e)
            return pl.DataFrame()

        try:
            raw = xtdata.call_formula(
                formula_name=formula_name,
                stock_code=code,
                period=period,
                start_time=start,
                end_time=end,
                count=count,
                **params,
            )
        except Exception as e:
            logger.error("XtDataBridge.call_formula(%s, %s) failed: %s", formula_name, code, e)
            return pl.DataFrame()

        if not raw:
            return pl.DataFrame()

        try:
            if isinstance(raw, dict):
                return pl.DataFrame(raw)
            if isinstance(raw, list):
                return pl.DataFrame(raw)
            return pl.DataFrame()
        except Exception as e:
            logger.error("XtDataBridge.call_formula: DataFrame build failed: %s", e)
            return pl.DataFrame()

    def call_formula_batch(
        self,
        formula_name: str,
        codes: list[str],
        period: str,
        start: str = "",
        end: str = "",
        count: int = -1,
        **params,
    ) -> "pl.DataFrame":
        """Batch-evaluate a named formula / indicator for multiple codes.

        Returns indicator values as a Polars DataFrame.
        Returns empty DataFrame on error.
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.call_formula_batch: xtdata unavailable: %s", e)
            return pl.DataFrame()

        try:
            raw = xtdata.call_formula_batch(
                formula_name=formula_name,
                stock_list=codes,
                period=period,
                start_time=start,
                end_time=end,
                count=count,
                **params,
            )
        except Exception as e:
            logger.error("XtDataBridge.call_formula_batch(%s) failed: %s", formula_name, e)
            return pl.DataFrame()

        if not raw:
            return pl.DataFrame()

        try:
            if isinstance(raw, dict):
                return pl.DataFrame(raw)
            if isinstance(raw, list):
                return pl.DataFrame(raw)
            return pl.DataFrame()
        except Exception as e:
            logger.error("XtDataBridge.call_formula_batch: DataFrame build failed: %s", e)
            return pl.DataFrame()

    def subscribe_formula(
        self,
        formula_name: str,
        codes: list[str],
        period: str,
        callback: "Callable | None" = None,
        **params,
    ) -> int:
        """Subscribe to real-time formula / indicator updates.

        Returns subscription id, -1 on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            seq = xtdata.subscribe_formula(
                formula_name=formula_name,
                stock_list=codes,
                period=period,
                callback=callback,
                **params,
            )
            return int(seq) if seq is not None else -1
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.subscribe_formula(%s) failed: %s", formula_name, e)
            return -1

    def unsubscribe_formula(self, seq: int) -> bool:
        """Unsubscribe a formula subscription by id.

        Returns True on success, False on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            xtdata.unsubscribe_formula(seq)
            return True
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.unsubscribe_formula(%s) failed: %s", seq, e)
            return False

    def generate_index_data(
        self,
        index_code: str,
        constituents: list[str],
        period: str = "1d",
    ) -> "pl.DataFrame":
        """Generate a custom index from constituent stocks.

        Returns a Polars DataFrame with the synthesised index data.
        Returns empty DataFrame on error.
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.generate_index_data: xtdata unavailable: %s", e)
            return pl.DataFrame()

        try:
            raw = xtdata.generate_index_data(
                index_code=index_code,
                stock_list=constituents,
                period=period,
            )
        except Exception as e:
            logger.error("XtDataBridge.generate_index_data(%s) failed: %s", index_code, e)
            return pl.DataFrame()

        if not raw:
            return pl.DataFrame()

        try:
            if isinstance(raw, dict):
                return pl.DataFrame(raw)
            if isinstance(raw, list):
                return pl.DataFrame(raw)
            return pl.DataFrame()
        except Exception as e:
            logger.error("XtDataBridge.generate_index_data: DataFrame build failed: %s", e)
            return pl.DataFrame()

    # ------------------------------------------------------------------
    # Group 6: Utility
    # ------------------------------------------------------------------

    def get_period_list(self) -> list[str]:
        """Return list of supported period strings.

        Falls back to a hardcoded list if xtdata does not expose the API.
        """
        _FALLBACK = ["tick", "1m", "5m", "15m", "30m", "1h", "1d", "1w", "1mon", "1q", "1hy", "1y"]
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            result = xtdata.get_period_list()
            if result:
                return list(result)
            return _FALLBACK
        except (ImportError, AttributeError):
            return _FALLBACK
        except Exception as e:
            logger.warning("XtDataBridge.get_period_list failed: %s", e)
            return _FALLBACK

    def get_ipo_info(
        self,
        codes: "list[str] | None" = None,
        start: str = "",
        end: str = "",
    ) -> "pl.DataFrame":
        """Fetch IPO details (code, name, issue_date, issue_price, etc.).

        Returns empty DataFrame on error.
        """
        if not _POLARS_AVAILABLE:
            return []  # type: ignore

        try:
            import xtquant.xtdata as xtdata  # type: ignore
        except (ImportError, Exception) as e:
            logger.warning("XtDataBridge.get_ipo_info: xtdata unavailable: %s", e)
            return pl.DataFrame()

        try:
            raw = xtdata.get_ipo_info(
                stock_list=codes,
                start_time=start,
                end_time=end,
            )
        except Exception as e:
            logger.error("XtDataBridge.get_ipo_info failed: %s", e)
            return pl.DataFrame()

        if not raw:
            return pl.DataFrame()

        try:
            if isinstance(raw, list):
                rows = []
                for item in raw:
                    if isinstance(item, dict):
                        rows.append(item)
                    elif hasattr(item, "__dict__"):
                        rows.append(vars(item))
                return pl.DataFrame(rows) if rows else pl.DataFrame()
            if isinstance(raw, dict):
                rows = []
                for code, info in raw.items():
                    if isinstance(info, dict):
                        row = {"code": code}
                        row.update(info)
                        rows.append(row)
                return pl.DataFrame(rows) if rows else pl.DataFrame()
            return pl.DataFrame()
        except Exception as e:
            logger.error("XtDataBridge.get_ipo_info: DataFrame build failed: %s", e)
            return pl.DataFrame()

    def get_trading_calendar(
        self,
        exchange: str = "SSE",
        start: str = "",
        end: str = "",
    ) -> list[str]:
        """Return trading calendar date strings for an exchange.

        Similar to get_trading_dates but may return richer info depending
        on xtdata version. Returns list of date strings, [] on error.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore
            result = xtdata.get_trading_calendar(
                market=exchange,
                start_time=start,
                end_time=end,
            )
            if result is None:
                return []
            return [str(d) for d in result]
        except (ImportError, AttributeError):
            # Fallback to get_trading_dates if get_trading_calendar not available
            return self.get_trading_dates(exchange=exchange, start=start, end=end)
        except Exception as e:
            logger.warning("XtDataBridge.get_trading_calendar failed: %s", e)
            return []
