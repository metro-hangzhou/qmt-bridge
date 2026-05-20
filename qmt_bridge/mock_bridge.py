"""Paper trading mock bridge — in-memory fill simulation.

Drop-in replacement for DaQMTBridge / XtQuantBridge. No real broker needed.
Thread-safe. Callbacks fire synchronously in the calling thread.

fill_mode:
    "instant"  — fills at submitted price immediately (default)
    "never"    — orders stay pending; test rejection/cancel flows
    "partial"  — fills ceil(volume/2) then stays pending
"""
from __future__ import annotations

import datetime
import math
import threading
import uuid
from typing import Any

from loguru import logger

from .constants import (
    STATUS_CANCELED, STATUS_FILLED, STATUS_PARTIAL, STATUS_PENDING,
)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _safe_cb(cb, method: str, *args) -> None:
    if cb is None:
        return
    fn = getattr(cb, method, None)
    if fn is None:
        return
    try:
        fn(*args)
    except Exception as e:
        logger.warning(f"MockBridge callback {method} raised: {e}")


class MockBridge:
    """In-memory paper trading bridge.

    config keys (all optional):
        initial_cash  float   starting available cash (default 1_000_000)
        fill_mode     str     "instant" | "never" | "partial" (default "instant")
        fill_price    float   override fill price; None = use submitted price
    """

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self._initial_cash: float  = float(cfg.get("initial_cash", 1_000_000.0))
        self.fill_mode: str        = cfg.get("fill_mode", "instant")
        self._fill_price_override: float | None = cfg.get("fill_price")

        self._cash: float = self._initial_cash
        self._positions: dict[str, dict] = {}   # code -> position
        self._orders: list[dict]          = []
        self._trades: list[dict]          = []
        self._callback: Any               = None
        self._lock                        = threading.Lock()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        return True

    def is_available(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass

    def register_callback(self, callback) -> None:
        """Register a BridgeCallback-compatible object for order/trade events."""
        self._callback = callback

    def reset(self, initial_cash: float | None = None) -> None:
        """Clear all state. Useful between backtest runs."""
        with self._lock:
            self._cash = initial_cash if initial_cash is not None else self._initial_cash
            self._positions.clear()
            self._orders.clear()
            self._trades.clear()
        logger.debug("MockBridge reset")

    # ── read methods ─────────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        with self._lock:
            mv = sum(p["market_value"] for p in self._positions.values())
            return {
                "available":   self._cash,
                "frozen_cash": 0.0,
                "market_value": mv,
                "total_asset": self._cash + mv,
                "raw": {},
            }

    def get_positions(self) -> list[dict]:
        with self._lock:
            return [dict(p) for p in self._positions.values() if p["volume"] > 0]

    def get_today_orders(self) -> list[dict]:
        with self._lock:
            return [dict(o) for o in self._orders]

    def get_today_trades(self) -> list[dict]:
        with self._lock:
            return [dict(t) for t in self._trades]

    # ── trade methods ─────────────────────────────────────────────────────────

    def buy(
        self,
        code: str,
        price: float,
        volume: int,
        pr_type: int = 11,
        price_type: int = 11,
        **_kwargs,
    ) -> dict:
        return self._submit("buy", code, price, volume)

    def sell(
        self,
        code: str,
        price: float,
        volume: int,
        pr_type: int = 11,
        price_type: int = 11,
        **_kwargs,
    ) -> dict:
        return self._submit("sell", code, price, volume)

    def cancel(self, order_id, **_kwargs) -> bool:
        with self._lock:
            oid = str(order_id)
            for o in self._orders:
                if str(o["order_id"]) == oid and o["status"] == STATUS_PENDING:
                    o["status"] = STATUS_CANCELED
                    _safe_cb(self._callback, "on_order", dict(o))
                    return True
        return False

    def cancel_all(self) -> dict:
        canceled: list = []
        with self._lock:
            for o in self._orders:
                if o["status"] in (STATUS_PENDING, STATUS_PARTIAL):
                    o["status"] = STATUS_CANCELED
                    canceled.append(o["order_id"])
                    _safe_cb(self._callback, "on_order", dict(o))
        return {"status": "success", "canceled": canceled}

    def cancel_by_id(self, order_sys_id: str) -> dict:
        ok = self.cancel(order_sys_id)
        return {"status": "success" if ok else "not_found", "order_sys_id": order_sys_id}

    # ── internals ─────────────────────────────────────────────────────────────

    def _submit(self, direction: str, code: str, price: float, volume: int) -> dict:
        order_id = uuid.uuid4().hex[:8]
        order: dict = {
            "order_id":     order_id,
            "code":         code,
            "direction":    direction,
            "price":        float(price),
            "volume":       int(volume),
            "filled_volume": 0,
            "status":       STATUS_PENDING,
            "order_time":   _now(),
        }

        with self._lock:
            self._orders.append(order)
            _safe_cb(self._callback, "on_order", dict(order))

            fill_price = self._fill_price_override if self._fill_price_override is not None else price

            if self.fill_mode == "instant":
                self._fill(order, volume, fill_price)
            elif self.fill_mode == "partial":
                fill_vol = math.ceil(volume / 2)
                self._fill(order, fill_vol, fill_price)
            # "never" → stays pending

        return {"order_id": order_id, "status": "submitted"}

    def _fill(self, order: dict, fill_volume: int, fill_price: float) -> None:
        """Execute fill. Must be called under self._lock."""
        code      = order["code"]
        direction = order["direction"]
        amount    = fill_price * fill_volume

        if direction == "buy":
            self._cash -= amount
            if code not in self._positions:
                self._positions[code] = {
                    "code": code, "volume": 0, "available": 0,
                    "avg_price": 0.0, "market_value": 0.0,
                    "frozen_volume": 0, "yesterday_volume": 0,
                }
            pos = self._positions[code]
            total_cost = pos["avg_price"] * pos["volume"] + amount
            pos["volume"]   += fill_volume
            pos["available"] += fill_volume
            pos["avg_price"]   = total_cost / pos["volume"]
            pos["market_value"] = pos["volume"] * fill_price

        else:  # sell
            pos = self._positions.get(code)
            if pos:
                pos["volume"]    -= fill_volume
                pos["available"] -= fill_volume
                pos["market_value"] = pos["volume"] * fill_price
            self._cash += amount

        order["filled_volume"] += fill_volume
        order["status"] = (
            STATUS_FILLED if order["filled_volume"] >= order["volume"]
            else STATUS_PARTIAL
        )

        trade: dict = {
            "trade_id":     uuid.uuid4().hex[:8],
            "order_id":     order["order_id"],
            "code":         code,
            "direction":    direction,
            "price":        fill_price,
            "volume":       fill_volume,
            "traded_amount": amount,
            "trade_time":   _now(),
        }
        self._trades.append(trade)
        _safe_cb(self._callback, "on_order", dict(order))
        _safe_cb(self._callback, "on_trade", dict(trade))

    # ── helpers for tests / strategy dev ──────────────────────────────────────

    def set_position(self, code: str, volume: int, avg_price: float) -> None:
        """Directly seed a position (useful for test setup)."""
        with self._lock:
            self._positions[code] = {
                "code": code,
                "volume": volume,
                "available": volume,
                "avg_price": avg_price,
                "market_value": volume * avg_price,
                "frozen_volume": 0,
                "yesterday_volume": volume,
            }

    def mark_to_market(self, prices: dict[str, float]) -> None:
        """Update market_value of all positions with latest prices."""
        with self._lock:
            for code, price in prices.items():
                if code in self._positions:
                    p = self._positions[code]
                    p["market_value"] = p["volume"] * price
