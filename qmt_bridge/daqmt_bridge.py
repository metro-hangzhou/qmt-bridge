"""qmt-bridge: 大QMT HTTP client.

The 大QMT side runs deploy/daqmt_server.py (Tornado :9000); this module is the
client. Both sides ship in the same package so versions stay aligned.
"""
from __future__ import annotations

from typing import Any

import requests
from loguru import logger

_FROZEN_WARN_ONCE = False
_TRADES_WARN_ONCE = False


class DaQMTBridge:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:9000",
        timeout: float = 5.0,
        account: str = "stock",
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.account = account
        self.session = requests.Session()
        self.session.headers.update(
            {"Content-Type": "application/json; charset=utf-8"}
        )

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
        """Ping /api/money/total — 失败返回 False 不 raise。"""
        url = f"{self.base_url}/api/money/total"
        try:
            resp = self.session.get(url, timeout=2.0)
            resp.raise_for_status()
            resp.json()
            return True
        except Exception:
            return False

    def get_balance(self) -> dict:
        """合并 /api/money/total + /api/money/available。"""
        global _FROZEN_WARN_ONCE

        total_raw = self._get("/api/money/total")
        avail_raw = self._get("/api/money/available")

        total_asset = float(
            self._pick(total_raw, "total", "total_asset", "money", default=0.0)
            or 0.0
        )
        available = float(
            self._pick(
                avail_raw, "available", "money", "available_cash", default=0.0
            )
            or 0.0
        )

        if not _FROZEN_WARN_ONCE:
            logger.warning(
                "DaQMTBridge: server does not expose frozen_cash/market_value; "
                "defaulting to 0.0"
            )
            _FROZEN_WARN_ONCE = True

        return {
            "available": available,
            "frozen_cash": 0.0,
            "market_value": 0.0,
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
            normalized.append(entry)
        return normalized

    def get_today_trades(self) -> list[dict]:
        """大QMT server 暂无 trades 端点 — 返回 []。"""
        global _TRADES_WARN_ONCE
        if not _TRADES_WARN_ONCE:
            logger.warning(
                "DaQMTBridge: server has no trades endpoint; returning []"
            )
            _TRADES_WARN_ONCE = True
        return []

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
