"""qmt-bridge: 大QMT HTTP client.

The 大QMT side runs deploy/daqmt_server.py (Tornado :9000); this module is the
client. Both sides ship in the same package so versions stay aligned.
"""
from __future__ import annotations

import threading
from typing import Any

import requests
from loguru import logger

from .constants import ORDER_TYPE_LIMIT, STATUS_PENDING, STATUS_PARTIAL


def _safe_cb(cb, method: str, *args) -> None:
    if cb is None:
        return
    fn = getattr(cb, method, None)
    if fn is None:
        return
    try:
        fn(*args)
    except Exception as e:
        logger.warning(f"DaQMTBridge callback {method} raised: {e}")


class DaQMTBridge:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:9001",
        timeout: float = 5.0,
        account: str = "stock",
        secret: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.account = account
        self._frozen_warned = False
        self.session = requests.Session()
        self.session.headers.update(
            {"Content-Type": "application/json; charset=utf-8"}
        )
        if secret:
            self.session.headers.update({"X-Bridge-Secret": secret})

        # Callback + polling monitor
        self._callback: Any = None
        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop = threading.Event()
        self._order_snapshot: dict[str, dict] = {}
        self._monitor_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # internal helpers
    # ------------------------------------------------------------------ #
    def _get(
        self, path: str, params: dict | None = None, timeout: float | None = None
    ) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(
                url, params=params, timeout=timeout or self.timeout
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"DaQMTBridge GET {path} failed: {e}")
            return {}
        except ValueError as e:
            logger.error(f"DaQMTBridge GET {path} json decode failed: {e}")
            return {}

    def _post(
        self,
        path: str,
        json_body: dict | None = None,
        params: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.post(
                url,
                json=json_body,
                params=params,
                timeout=timeout or self.timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error(f"DaQMTBridge POST {path} failed: {e}")
            return {"error": str(e)}
        except ValueError as e:
            logger.error(f"DaQMTBridge POST {path} json decode failed: {e}")
            return {"error": f"json decode: {e}"}

    @staticmethod
    def _pick(d: dict, *keys: str, default: Any = None) -> Any:
        """Try multiple keys in order, return first present value."""
        for k in keys:
            if k in d:
                return d[k]
        return default

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #
    def is_available(self) -> bool:
        """Ping server — any HTTP response (incl. 5xx) means the server is up."""
        url = f"{self.base_url}/api/money/total"
        try:
            resp = self.session.get(url, timeout=min(2.0, self.timeout))
            resp.json()  # must parse as JSON; connection errors still return False
            return True
        except Exception:
            return False

    def get_balance(self) -> dict:
        """合并 /api/money/total + /api/money/available。"""
        total_raw = self._get("/api/money/total")
        avail_raw = self._get("/api/money/available")

        total_asset = float(
            self._pick(total_raw, "total_money", "total", "total_asset", "money", default=0.0)
            or 0.0
        )
        available = float(
            self._pick(
                avail_raw, "available_money", "available", "money", "available_cash", default=0.0
            )
            or 0.0
        )

        return {
            "available": available,
            "frozen_cash": None,   # server does not expose this field
            "market_value": None,  # server does not expose this field
            "total_asset": total_asset,
            "raw": {"total": total_raw, "available": avail_raw},
        }

    def get_positions(self) -> list[dict]:
        """GET /api/holding -> list of normalized positions."""
        raw = self._get("/api/holding")
        if not isinstance(raw, dict):
            return []

        # server may return {"holdings": {...}} or direct dict keyed by code
        if "holdings" in raw and isinstance(raw["holdings"], dict):
            holdings = raw["holdings"]
        elif "holdings" in raw and isinstance(raw["holdings"], list):
            # list-of-dicts shape
            holdings = {
                self._pick(item, "StockCode", "code", "stock_code", default=""):
                item
                for item in raw["holdings"]
                if isinstance(item, dict)
            }
        else:
            holdings = raw

        out: list[dict] = []
        if not isinstance(holdings, dict):
            return out

        for key, item in holdings.items():
            if not isinstance(item, dict):
                continue
            code = self._pick(
                item, "StockCode", "code", "stock_code", default=key
            )
            volume = self._pick(item, "Volume", "m_nVolume", default=0) or 0
            available = (
                self._pick(item, "CanUseVolume", "m_nCanUseVolume", default=0)
                or 0
            )
            avg_price = (
                self._pick(item, "OpenPrice", "m_dOpenPrice", default=0.0)
                or 0.0
            )
            market_value = (
                self._pick(item, "MarketValue", "m_dMarketValue", default=0.0)
                or 0.0
            )
            frozen_volume = (
                self._pick(item, "FrozenVolume", "m_nFrozenVolume", default=0)
                or 0
            )
            yesterday_volume = (
                self._pick(
                    item, "YesterdayVolume", "m_nYesterdayVolume", default=0
                )
                or 0
            )
            out.append(
                {
                    "code": code,
                    "volume": int(volume),
                    "available": int(available),
                    "avg_price": float(avg_price),
                    "market_value": float(market_value),
                    "frozen_volume": int(frozen_volume),
                    "yesterday_volume": int(yesterday_volume),
                    "raw": item,
                }
            )
        return out

    def get_today_orders(self) -> list[dict]:
        """GET /api/order/status -> list of orders (schema-normalized)."""
        raw = self._get("/api/order/status")
        if not isinstance(raw, dict):
            return []
        orders = raw.get("orders", [])
        if not isinstance(orders, list):
            return []

        normalized: list[dict] = []
        for o in orders:
            if not isinstance(o, dict):
                continue
            # Schema bottom: pass through, ensuring 'code' key exists
            entry = dict(o)
            if "code" not in entry:
                entry["code"] = self._pick(
                    o, "StockCode", "stock_code", "stock", default=""
                )
            entry.setdefault("strategy_name", None)
            normalized.append(entry)
        return normalized

    def get_today_trades(self) -> list[dict]:
        """GET /api/trade/status -> list of trades (schema-normalized)."""
        raw = self._get("/api/trade/status")
        if not isinstance(raw, dict):
            return []
        trades = raw.get("trades", [])
        if not isinstance(trades, list):
            return []
        normalized = []
        for t in trades:
            if not isinstance(t, dict):
                continue
            entry = dict(t)
            entry.setdefault("trade_id", "")
            entry.setdefault("order_id", "")
            entry.setdefault("code", self._pick(t, "StockCode", "stock_code", default=""))
            entry.setdefault("price", 0.0)
            entry.setdefault("volume", 0)
            entry.setdefault("traded_amount", entry.get("amount", 0.0))
            entry.setdefault("trade_time", "")
            normalized.append(entry)
        return normalized

    def buy(
        self, code: str, price: float, volume: int, pr_type: int = 11
    ) -> dict:
        """POST /api/order/buy."""
        return self._post(
            "/api/order/buy",
            json_body={
                "stock": code,
                "price": price,
                "volume": volume,
                "prType": pr_type,
            },
        )

    def sell(
        self, code: str, price: float, volume: int, pr_type: int = 11
    ) -> dict:
        """POST /api/order/sell."""
        return self._post(
            "/api/order/sell",
            json_body={
                "stock": code,
                "price": price,
                "volume": volume,
                "prType": pr_type,
            },
        )

    def cancel(self, code: str, volume: int) -> dict:
        """POST /api/order/cancel_order — match by code+volume."""
        return self._post(
            "/api/order/cancel_order",
            json_body={
                "stock": code,
                "volume": volume,
                "account": self.account,
            },
        )

    def cancel_all(self) -> dict:
        """POST /api/order/cancel_all."""
        return self._post("/api/order/cancel_all")

    def cancel_by_id(self, order_sys_id: str) -> dict:
        """POST /api/order/cancel_by_id — cancel by exact order system ID.

        Preferred over cancel(code, volume) when the order may be partially filled.
        """
        try:
            resp = self._post("/api/order/cancel_by_id", {"order_sys_id": order_sys_id})
            return resp if resp else {"error": "empty response"}
        except Exception as e:
            logger.error(f"DaQMTBridge cancel_by_id({order_sys_id}) failed: {e}")
            return {"error": str(e)}

    def disconnect(self) -> None:
        """Close the underlying requests.Session connection pool."""
        self.stop_monitor()
        self.session.close()

    # ── callback / order monitor ────────────────────────────────────────────

    def register_callback(self, callback) -> None:
        """Register a BridgeCallback-compatible object.

        After registering, call start_monitor() to begin receiving push events.
        The callback is invoked on the monitor background thread.
        """
        self._callback = callback

    def start_monitor(self, interval: float = 1.0) -> None:
        """Start background polling thread for order state changes.

        Polls get_today_orders() every `interval` seconds and fires:
          callback.on_order(order)  — new order or status/fill change
          callback.on_trade(trade)  — synthetic trade event on fill increase

        Safe to call multiple times; only one thread runs at a time.
        """
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._monitor_stop.clear()
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True,
            name="daqmt-monitor",
        )
        self._monitor_thread.start()
        logger.debug(f"DaQMTBridge monitor started (interval={interval}s)")

    def stop_monitor(self) -> None:
        """Stop the background polling thread."""
        self._monitor_stop.set()
        t = self._monitor_thread
        if t and t.is_alive():
            t.join(timeout=5.0)
        self._monitor_thread = None
        logger.debug("DaQMTBridge monitor stopped")

    def _monitor_loop(self, interval: float) -> None:
        while not self._monitor_stop.wait(interval):
            try:
                self._poll_orders()
            except Exception as e:
                logger.warning(f"DaQMTBridge._monitor_loop error: {e}")

    def _poll_orders(self) -> None:
        if self._callback is None:
            return
        try:
            orders = self.get_today_orders()
        except Exception:
            return

        with self._monitor_lock:
            new_snap: dict[str, dict] = {}
            for o in orders:
                oid = str(o.get("order_id", "") or "")
                if not oid:
                    continue
                new_snap[oid] = o
                prev = self._order_snapshot.get(oid)

                if prev is None:
                    _safe_cb(self._callback, "on_order", o)
                else:
                    prev_status  = prev.get("status")
                    prev_filled  = int(prev.get("filled_volume", 0) or 0)
                    curr_status  = o.get("status")
                    curr_filled  = int(o.get("filled_volume", 0) or 0)

                    if prev_status != curr_status or prev_filled != curr_filled:
                        _safe_cb(self._callback, "on_order", o)

                    if curr_filled > prev_filled:
                        trade = {
                            "order_id":  oid,
                            "code":      o.get("code", ""),
                            "direction": o.get("direction", ""),
                            "price":     o.get("price", 0.0),
                            "volume":    curr_filled - prev_filled,
                            "trade_time": o.get("order_time", ""),
                        }
                        _safe_cb(self._callback, "on_trade", trade)

            self._order_snapshot = new_snap
