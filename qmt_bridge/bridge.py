"""qmt-bridge: unified router for 大QMT HTTP and miniQMT xtquant."""
from __future__ import annotations
from typing import Any, Literal
from loguru import logger

from .daqmt_bridge import DaQMTBridge
from .xtquant_bridge import XtQuantBridge
from .xtdata_bridge import XtDataBridge


Mode = Literal["auto", "daqmt", "miniqmt"]


class QMTBridge:
    """统一 broker bridge。

    config:
        mode:          "auto" | "daqmt" | "miniqmt"，默认 auto
        base_url:      DaQMTBridge HTTP server URL (default 127.0.0.1:9000)
        timeout:       DaQMTBridge HTTP timeout (default 5.0)
        account:       DaQMTBridge account_type 字符串 (default "stock")
        mini_qmt_path: XtQuantBridge miniQMT userdata_mini 路径
        account_id:    XtQuantBridge 资金账号
        session_id:    XtQuantBridge session_id (default 2)

    auto 决策：先 DaQMTBridge.is_available()，通则用它；否则 XtQuantBridge.connect()。
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.mode: Mode = self.config.get("mode", "auto")
        self._daqmt: DaQMTBridge | None = None
        self._xtquant: XtQuantBridge | None = None
        self._active: DaQMTBridge | XtQuantBridge | None = None
        self._active_mode: str = "none"
        self._data: XtDataBridge | None = None

    @property
    def data(self) -> XtDataBridge:
        """Lazy-initialized XtDataBridge for in-process market data."""
        if self._data is None:
            self._data = XtDataBridge(self.config)
        return self._data

    def connect(self) -> bool:
        if self.mode == "daqmt":
            return self._try_daqmt()
        if self.mode == "miniqmt":
            return self._try_xtquant()
        # auto
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
                logger.info(f"QMTBridge: connected via daqmt at {self._daqmt.base_url}")
                return True
        except Exception as e:
            logger.warning(f"QMTBridge: daqmt connect failed: {e}")
        return False

    def _try_xtquant(self) -> bool:
        try:
            self._xtquant = XtQuantBridge({
                "mini_qmt_path": self.config.get("mini_qmt_path", ""),
                "account_id": self.config.get("account_id", ""),
                "session_id": int(self.config.get("session_id", 2)),
            })
            if self._xtquant.connect():
                self._active = self._xtquant
                self._active_mode = "miniqmt"
                logger.info("QMTBridge: connected via miniqmt xtquant")
                return True
        except Exception as e:
            logger.warning(f"QMTBridge: miniqmt connect failed: {e}")
        return False

    def is_available(self) -> bool:
        return self._active is not None and self._active.is_available()

    def mode_used(self) -> str:
        return self._active_mode

    # ---- Read methods (代理到 active) ----
    def get_balance(self) -> dict:
        return self._active.get_balance() if self._active else {}

    def get_positions(self) -> list[dict]:
        return self._active.get_positions() if self._active else []

    def get_today_orders(self) -> list[dict]:
        return self._active.get_today_orders() if self._active else []

    def get_today_trades(self) -> list[dict]:
        return self._active.get_today_trades() if self._active else []

    # ---- Trade methods ----
    def buy(self, code: str, price: float, volume: int, **kwargs) -> dict:
        if not self._active:
            return {"error": "not connected"}
        if self._active_mode == "daqmt":
            return self._daqmt.buy(code, price, volume, pr_type=kwargs.get("pr_type", 11))
        return self._xtquant.buy(code, price, volume)

    def sell(self, code: str, price: float, volume: int, **kwargs) -> dict:
        if not self._active:
            return {"error": "not connected"}
        if self._active_mode == "daqmt":
            return self._daqmt.sell(code, price, volume, pr_type=kwargs.get("pr_type", 11))
        return self._xtquant.sell(code, price, volume)

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
            return {"status": "success" if ok else "error", "order_id": kwargs.get("order_id")}
        return {"error": "not connected"}

    def cancel_by_id(self, order_sys_id: str) -> dict:
        """Cancel by exact order_sys_id. daqmt only (miniQMT uses integer order_id via cancel())."""
        if not self._active:
            return {"error": "not connected"}
        if self._active_mode == "daqmt":
            return self._daqmt.cancel_by_id(order_sys_id)
        if self._active_mode == "miniqmt":
            return {"error": "miniqmt uses cancel(order_id=int), not cancel_by_id"}
        return {"error": "not connected"}

    def cancel_all(self) -> dict:
        if self._active_mode == "daqmt":
            return self._daqmt.cancel_all()
        # miniQMT 没有 cancel_all：遍历 orders 逐个 cancel
        if self._active_mode == "miniqmt" and self._xtquant is not None:
            orders = self._xtquant.get_today_orders()
            canceled = []
            for o in orders:
                if self._xtquant.cancel(o.get("order_id")):
                    canceled.append(o.get("order_id"))
            return {"status": "success", "canceled_order_ids": canceled}
        return {"error": "not connected"}

    def disconnect(self) -> None:
        if self._active_mode == "daqmt" and self._daqmt is not None:
            self._daqmt.disconnect()
        if self._active_mode == "miniqmt" and self._xtquant is not None:
            self._xtquant.disconnect()
        self._active = None
        self._active_mode = "none"
