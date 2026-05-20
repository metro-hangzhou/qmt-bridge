"""qmt-bridge: unified router for 大QMT HTTP and miniQMT xtquant."""
from __future__ import annotations

import threading
from typing import Any, Literal

from loguru import logger

from .constants import ORDER_TYPE_LIMIT
from .daqmt_bridge import DaQMTBridge
from .xtquant_bridge import XtQuantBridge
from .xtdata_bridge import XtDataBridge


Mode = Literal["auto", "daqmt", "miniqmt"]


def _safe_cb(cb, method: str, *args) -> None:
    if cb is None:
        return
    fn = getattr(cb, method, None)
    if fn is None:
        return
    try:
        fn(*args)
    except Exception as e:
        logger.warning(f"QMTBridge callback {method} raised: {e}")


class QMTBridge:
    """Unified broker bridge with auto-reconnect and callback support.

    config:
        mode:               "auto" | "daqmt" | "miniqmt"  (default auto)
        base_url:           大QMT server URL (default http://127.0.0.1:9000)
        timeout:            HTTP timeout seconds (default 5.0)
        account:            大QMT account_type (default "stock")
        mini_qmt_path:      miniQMT userdata_mini path
        account_id:         miniQMT account id
        session_id:         miniQMT session_id (default 2)
        reconnect_interval: keepalive check interval in seconds (default 30.0)
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.mode: Mode = self.config.get("mode", "auto")
        self._daqmt:   DaQMTBridge  | None = None
        self._xtquant: XtQuantBridge | None = None
        self._active:  DaQMTBridge | XtQuantBridge | None = None
        self._active_mode: str = "none"
        self._data: XtDataBridge | None = None

        self._callback: Any = None
        self._intentionally_disconnected: bool = False
        self._keepalive_stop = threading.Event()
        self._keepalive_thread: threading.Thread | None = None

    # ── market data ───────────────────────────────────────────────────────────

    @property
    def data(self) -> XtDataBridge:
        if self._data is None:
            self._data = XtDataBridge(self.config)
        return self._data

    # ── connection ────────────────────────────────────────────────────────────

    def connect(self) -> bool:
        self._intentionally_disconnected = False
        if self.mode == "daqmt":
            return self._try_daqmt()
        if self.mode == "miniqmt":
            return self._try_xtquant()
        if self._try_daqmt():
            return True
        return self._try_xtquant()

    def _try_daqmt(self) -> bool:
        try:
            self._daqmt = DaQMTBridge(
                base_url=self.config.get("base_url", "http://127.0.0.1:9000"),
                timeout=float(self.config.get("timeout", 5.0)),
                account=self.config.get("account", "stock"),
            )
            if self._daqmt.is_available():
                self._active = self._daqmt
                self._active_mode = "daqmt"
                if self._callback:
                    self._daqmt.register_callback(self._callback)
                logger.info(f"QMTBridge: connected via daqmt at {self._daqmt.base_url}")
                return True
        except Exception as e:
            logger.warning(f"QMTBridge: daqmt connect failed: {e}")
        return False

    def _try_xtquant(self) -> bool:
        try:
            self._xtquant = XtQuantBridge({
                "mini_qmt_path": self.config.get("mini_qmt_path", ""),
                "account_id":    self.config.get("account_id", ""),
                "session_id":    int(self.config.get("session_id", 2)),
            })
            if self._xtquant.connect():
                self._active = self._xtquant
                self._active_mode = "miniqmt"
                if self._callback:
                    self._xtquant.register_callback(self._callback)
                logger.info("QMTBridge: connected via miniqmt xtquant")
                return True
        except Exception as e:
            logger.warning(f"QMTBridge: miniqmt connect failed: {e}")
        return False

    def is_available(self) -> bool:
        return self._active is not None and self._active.is_available()

    def mode_used(self) -> str:
        return self._active_mode

    def disconnect(self) -> None:
        self._intentionally_disconnected = True
        self.stop_keepalive()
        if self._active_mode == "daqmt" and self._daqmt is not None:
            self._daqmt.disconnect()
        if self._active_mode == "miniqmt" and self._xtquant is not None:
            self._xtquant.disconnect()
        self._active = None
        self._active_mode = "none"

    # ── callbacks ─────────────────────────────────────────────────────────────

    def register_callback(self, callback) -> None:
        """Register a BridgeCallback for order/trade/connection events.

        Must be called before connect() or start_keepalive() to take effect on
        the active sub-bridge. Safe to call after connect() — wires immediately.
        """
        self._callback = callback
        if self._active_mode == "daqmt" and self._daqmt is not None:
            self._daqmt.register_callback(callback)
        if self._active_mode == "miniqmt" and self._xtquant is not None:
            self._xtquant.register_callback(callback)

    # ── auto-reconnect ────────────────────────────────────────────────────────

    def start_keepalive(self, interval: float | None = None) -> None:
        """Start background keepalive/reconnect thread.

        If the active bridge becomes unavailable, automatically reconnects
        using the same mode and fires on_disconnected / on_reconnected callbacks.
        Safe to call multiple times; only one thread runs at a time.
        """
        if self._keepalive_thread and self._keepalive_thread.is_alive():
            return
        secs = interval if interval is not None else float(
            self.config.get("reconnect_interval", 30.0)
        )
        self._keepalive_stop.clear()
        self._keepalive_thread = threading.Thread(
            target=self._keepalive_loop,
            args=(secs,),
            daemon=True,
            name="qmt-keepalive",
        )
        self._keepalive_thread.start()
        logger.debug(f"QMTBridge keepalive started (interval={secs}s)")

    def stop_keepalive(self) -> None:
        """Stop the keepalive thread."""
        self._keepalive_stop.set()
        t = self._keepalive_thread
        if t and t.is_alive():
            t.join(timeout=5.0)
        self._keepalive_thread = None

    def _keepalive_loop(self, interval: float) -> None:
        while not self._keepalive_stop.wait(interval):
            if self._intentionally_disconnected:
                break
            try:
                if not self.is_available():
                    logger.warning("QMTBridge keepalive: connection lost, reconnecting…")
                    _safe_cb(self._callback, "on_disconnected")
                    if self.connect():
                        logger.info("QMTBridge keepalive: reconnected")
                        _safe_cb(self._callback, "on_reconnected")
                    else:
                        logger.warning("QMTBridge keepalive: reconnect failed, will retry")
            except Exception as e:
                logger.warning(f"QMTBridge keepalive error: {e}")

    # ── read methods ──────────────────────────────────────────────────────────

    def get_balance(self) -> dict:
        return self._active.get_balance() if self._active else {}

    def get_positions(self) -> list[dict]:
        return self._active.get_positions() if self._active else []

    def get_today_orders(self) -> list[dict]:
        return self._active.get_today_orders() if self._active else []

    def get_today_trades(self) -> list[dict]:
        return self._active.get_today_trades() if self._active else []

    # ── trade methods ─────────────────────────────────────────────────────────

    def buy(
        self,
        code: str,
        price: float,
        volume: int,
        price_type: int = ORDER_TYPE_LIMIT,
        **kwargs,
    ) -> dict:
        """买入。price_type=11 限价，price_type=5 市价（见 constants.ORDER_TYPE_*)。"""
        if not self._active:
            return {"error": "not connected"}
        if self._active_mode == "daqmt":
            return self._daqmt.buy(code, price, volume,
                                   pr_type=kwargs.get("pr_type", price_type))
        return self._xtquant.buy(code, price, volume, price_type=price_type)

    def sell(
        self,
        code: str,
        price: float,
        volume: int,
        price_type: int = ORDER_TYPE_LIMIT,
        **kwargs,
    ) -> dict:
        """卖出。price_type=11 限价，price_type=5 市价。"""
        if not self._active:
            return {"error": "not connected"}
        if self._active_mode == "daqmt":
            return self._daqmt.sell(code, price, volume,
                                    pr_type=kwargs.get("pr_type", price_type))
        return self._xtquant.sell(code, price, volume, price_type=price_type)

    def cancel(self, **kwargs) -> Any:
        if not self._active:
            return {"error": "not connected"}
        if self._active_mode == "daqmt":
            if "code" not in kwargs or "volume" not in kwargs:
                return {"error": "daqmt mode requires code= and volume= kwargs"}
            return self._daqmt.cancel(kwargs["code"], kwargs["volume"])
        if self._active_mode == "miniqmt":
            if "order_id" not in kwargs:
                return {"error": "miniqmt mode requires order_id= kwarg"}
            ok = self._xtquant.cancel(kwargs["order_id"])
            return {"status": "success" if ok else "error",
                    "order_id": kwargs.get("order_id")}
        return {"error": "not connected"}

    def cancel_by_id(self, order_sys_id: str) -> dict:
        if not self._active:
            return {"error": "not connected"}
        if self._active_mode == "daqmt":
            return self._daqmt.cancel_by_id(order_sys_id)
        return {"error": "miniqmt uses cancel(order_id=int), not cancel_by_id"}

    def cancel_all(self) -> dict:
        if self._active_mode == "daqmt":
            return self._daqmt.cancel_all()
        if self._active_mode == "miniqmt" and self._xtquant is not None:
            orders   = self._xtquant.get_today_orders()
            canceled = []
            for o in orders:
                if self._xtquant.cancel(o.get("order_id")):
                    canceled.append(o.get("order_id"))
            return {"status": "success", "canceled_order_ids": canceled}
        return {"error": "not connected"}


# ── multi-account pool ────────────────────────────────────────────────────────

class BridgePool:
    """Named container for multiple QMTBridge instances.

    Usage::

        pool = BridgePool({
            "main":  QMTBridge({"mode": "daqmt", "base_url": "http://127.0.0.1:9000"}),
            "hedge": QMTBridge({"mode": "miniqmt", "account_id": "88888888", ...}),
        })
        pool.connect_all()

        pool.get_balance()            # primary (first) account
        pool.get_balance("hedge")     # explicit account
        pool.buy("600519.SH", 1800.0, 100)
        pool.buy("600519.SH", 1800.0, 100, account="hedge")
    """

    def __init__(self, bridges: dict[str, QMTBridge], primary: str | None = None):
        if not bridges:
            raise ValueError("BridgePool requires at least one bridge")
        self._bridges = bridges
        self._primary = primary or next(iter(bridges))

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def connect_all(self) -> dict[str, bool]:
        return {name: b.connect() for name, b in self._bridges.items()}

    def disconnect_all(self) -> None:
        for b in self._bridges.values():
            b.disconnect()

    def start_keepalive_all(self, interval: float = 30.0) -> None:
        for b in self._bridges.values():
            b.start_keepalive(interval)

    def __getitem__(self, name: str) -> QMTBridge:
        return self._bridges[name]

    @property
    def primary(self) -> QMTBridge:
        return self._bridges[self._primary]

    # ── delegating read methods ───────────────────────────────────────────────

    def get_balance(self, account: str | None = None) -> dict:
        return self._resolve(account).get_balance()

    def get_positions(self, account: str | None = None) -> list[dict]:
        return self._resolve(account).get_positions()

    def get_today_orders(self, account: str | None = None) -> list[dict]:
        return self._resolve(account).get_today_orders()

    def get_today_trades(self, account: str | None = None) -> list[dict]:
        return self._resolve(account).get_today_trades()

    # ── delegating trade methods ──────────────────────────────────────────────

    def buy(self, code: str, price: float, volume: int,
            account: str | None = None, **kwargs) -> dict:
        return self._resolve(account).buy(code, price, volume, **kwargs)

    def sell(self, code: str, price: float, volume: int,
             account: str | None = None, **kwargs) -> dict:
        return self._resolve(account).sell(code, price, volume, **kwargs)

    def cancel(self, account: str | None = None, **kwargs) -> Any:
        return self._resolve(account).cancel(**kwargs)

    def cancel_all(self, account: str | None = None) -> dict:
        return self._resolve(account).cancel_all()

    # ── internal ──────────────────────────────────────────────────────────────

    def _resolve(self, account: str | None) -> QMTBridge:
        if account:
            if account not in self._bridges:
                raise KeyError(f"BridgePool: unknown account '{account}'")
            return self._bridges[account]
        return self.primary
