"""miniQMT SDK client via xtquant.

依赖：xtquant (miniQMT 安装时附带)。
xtquant 不可用时 is_available() 返回 False，不 raise，不破坏 import。

设计要点（与 deploy/daqmt_server.py 大 QMT HTTP 路径互为兜底）:
- session_id 默认 2，避免与大 QMT server (session_id=1) 冲突
- subscribe(account) 返回非 0 视为 hard fail（不像 easyxt_bridge 只 warning），
  因为下单依赖账户订阅，订阅失败时后续 order_stock 行为不可靠
- cancel 使用 self.account (StockAccount 对象)，不是 account_id 字符串
- xtquant lazy import：module 顶层不依赖 xtquant，未装机器仍可 import
"""

from __future__ import annotations

from typing import Any

from loguru import logger


class XtQuantBridge:
    """miniQMT SDK client (xtquant-based).

    config 字段:
        mini_qmt_path: miniQMT userdata_mini 目录
        account_id:    资金账号
        session_id:    可选，默认 2（避免与 deploy/daqmt_server.py session_id=1 冲突）
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.mini_qmt_path: str = self.config.get("mini_qmt_path", "")
        self.account_id: str = self.config.get("account_id", "")
        self.session_id: int = int(self.config.get("session_id", 2))
        self.xt_trader: Any = None
        self.account: Any = None
        self.connected: bool = False

    def connect(self) -> bool:
        """连接 miniQMT。xtquant 不可用时返回 False，不 raise。

        步骤:
            1. import xtquant.xttrader + xtquant.xttype.StockAccount
            2. XtQuantTrader(path, session_id) + start()
            3. connect()，返回值 != 0 表示失败
            4. StockAccount(account_id)
            5. subscribe(account)，返回值 != 0 视为 hard fail
            6. self.connected = True
        """
        try:
            from xtquant import xttrader  # type: ignore
            from xtquant.xttype import StockAccount  # type: ignore
        except ImportError:
            logger.warning(
                "XtQuantBridge: xtquant not installed (needs miniQMT install). "
                "is_available() will return False."
            )
            return False
        except Exception as e:
            logger.error(f"XtQuantBridge: xtquant import failed: {e}")
            return False

        try:
            self.xt_trader = xttrader.XtQuantTrader(
                self.mini_qmt_path, session_id=self.session_id
            )
            self.xt_trader.start()
            connect_result = self.xt_trader.connect()
            if connect_result != 0:
                logger.error(
                    f"XtQuantBridge: xt_trader.connect failed code={connect_result}"
                )
                return False
            self.account = StockAccount(self.account_id)
            subscribe_result = self.xt_trader.subscribe(self.account)
            if subscribe_result != 0:
                # subscribe 失败是 hard fail（不同于 easyxt_bridge 的 warning 策略），
                # 因为本桥接要支持下单，订阅失败时 order_stock 不可靠
                logger.error(
                    f"XtQuantBridge: subscribe failed code={subscribe_result} "
                    "(treated as hard fail; trading requires subscription)"
                )
                return False
            self.connected = True
            logger.info(
                f"XtQuantBridge connected: account={self.account_id} "
                f"session_id={self.session_id}"
            )
            return True
        except Exception as e:
            logger.error(f"XtQuantBridge connect failed: {e}")
            self.connected = False
            return False

    def is_available(self) -> bool:
        return (
            self.connected
            and self.xt_trader is not None
            and self.account is not None
        )

    # ---------------- read-only queries ----------------

    def get_balance(self) -> dict:
        """query_stock_asset(account) -> 统一资金 schema。"""
        if not self.is_available():
            return {}
        try:
            asset = self.xt_trader.query_stock_asset(self.account)
            if asset is None:
                return {}
            return {
                "available": float(getattr(asset, "cash", 0.0) or 0.0),
                "frozen_cash": float(getattr(asset, "frozen_cash", 0.0) or 0.0),
                "market_value": float(getattr(asset, "market_value", 0.0) or 0.0),
                "total_asset": float(getattr(asset, "total_asset", 0.0) or 0.0),
                "raw": {
                    "cash": getattr(asset, "cash", None),
                    "frozen_cash": getattr(asset, "frozen_cash", None),
                    "market_value": getattr(asset, "market_value", None),
                    "total_asset": getattr(asset, "total_asset", None),
                    "account_type": getattr(asset, "account_type", None),
                    "account_id": getattr(asset, "account_id", None),
                },
            }
        except Exception as e:
            logger.error(f"XtQuantBridge get_balance error: {e}")
            return {}

    def get_positions(self) -> list[dict]:
        """query_stock_positions(account) -> 统一持仓 schema。"""
        if not self.is_available():
            return []
        try:
            positions = self.xt_trader.query_stock_positions(self.account)
            if not positions:
                return []
            return [
                {
                    "code": getattr(p, "stock_code", ""),
                    "volume": int(getattr(p, "volume", 0) or 0),
                    "available": int(getattr(p, "can_use_volume", 0) or 0),
                    "avg_price": float(getattr(p, "avg_price", 0.0) or 0.0),
                    "market_value": float(getattr(p, "market_value", 0.0) or 0.0),
                    "frozen_volume": int(getattr(p, "frozen_volume", 0) or 0),
                    "yesterday_volume": int(getattr(p, "yesterday_volume", 0) or 0),
                }
                for p in positions
            ]
        except Exception as e:
            logger.error(f"XtQuantBridge get_positions error: {e}")
            return []

    def get_today_orders(self) -> list[dict]:
        """query_stock_orders(account) -> 当日委托列表。"""
        if not self.is_available():
            return []
        try:
            orders = self.xt_trader.query_stock_orders(self.account)
            if not orders:
                return []
            return [self._order_to_dict(o) for o in orders]
        except Exception as e:
            logger.error(f"XtQuantBridge get_today_orders error: {e}")
            return []

    def get_today_trades(self) -> list[dict]:
        """query_stock_trades(account) -> 当日成交列表。

        旧 qmt_bridge.py 缺这个方法，本类补全。
        """
        if not self.is_available():
            return []
        try:
            trades = self.xt_trader.query_stock_trades(self.account)
            if not trades:
                return []
            return [self._trade_to_dict(t) for t in trades]
        except Exception as e:
            logger.error(f"XtQuantBridge get_today_trades error: {e}")
            return []

    # ---------------- trading ----------------

    def buy(self, code: str, price: float, volume: int) -> dict:
        """限价买入。返回 {"order_id": id, "status": "submitted"} 或 {"error": str}。"""
        if not self.is_available():
            return {"error": "XtQuantBridge not connected"}
        try:
            from xtquant import xtconstant  # type: ignore

            order_id = self.xt_trader.order_stock(
                self.account,
                code,
                xtconstant.STOCK_BUY,
                volume,
                xtconstant.FIX_PRICE,
                price,
            )
            logger.info(
                f"XtQuantBridge buy: {code} @ {price} x {volume}, order_id={order_id}"
            )
            return {"order_id": order_id, "status": "submitted"}
        except Exception as e:
            logger.error(f"XtQuantBridge buy error: {e}")
            return {"error": str(e)}

    def sell(self, code: str, price: float, volume: int) -> dict:
        """限价卖出。返回 {"order_id": id, "status": "submitted"} 或 {"error": str}。"""
        if not self.is_available():
            return {"error": "XtQuantBridge not connected"}
        try:
            from xtquant import xtconstant  # type: ignore

            order_id = self.xt_trader.order_stock(
                self.account,
                code,
                xtconstant.STOCK_SELL,
                volume,
                xtconstant.FIX_PRICE,
                price,
            )
            logger.info(
                f"XtQuantBridge sell: {code} @ {price} x {volume}, order_id={order_id}"
            )
            return {"order_id": order_id, "status": "submitted"}
        except Exception as e:
            logger.error(f"XtQuantBridge sell error: {e}")
            return {"error": str(e)}

    def cancel(self, order_id: int) -> bool:
        """撤单。注意用 self.account (StockAccount 对象)，不是 account_id 字符串
        （旧 qmt_bridge.py 传 account_id 是 bug）。"""
        if not self.is_available():
            return False
        try:
            self.xt_trader.cancel_order_stock(self.account, order_id)
            return True
        except Exception as e:
            logger.error(f"XtQuantBridge cancel error: {e}")
            return False

    def disconnect(self) -> None:
        if self.xt_trader is not None:
            try:
                self.xt_trader.stop()
            except Exception as e:
                logger.warning(f"XtQuantBridge stop error: {e}")
        self.connected = False

    # ---------------- helpers ----------------

    @staticmethod
    def _order_to_dict(o) -> dict:
        """xtquant order 对象 -> 统一 dict。order_type: 23=buy, 24=sell。"""
        order_type = getattr(o, "order_type", None)
        direction = (
            "buy" if order_type == 23
            else "sell" if order_type == 24
            else "unknown"
        )
        return {
            "order_id": getattr(o, "order_id", None),
            "code": getattr(o, "stock_code", ""),
            "direction": direction,
            "price": float(getattr(o, "price", 0.0) or 0.0),
            "volume": int(getattr(o, "order_volume", 0) or 0),
            "filled_volume": int(getattr(o, "traded_volume", 0) or 0),
            "status": getattr(o, "order_status", None),
            "order_time": getattr(o, "order_time", None),
            "strategy_name": getattr(o, "strategy_name", None),
        }

    @staticmethod
    def _trade_to_dict(t) -> dict:
        """xtquant trade 对象 -> 统一 dict。"""
        order_type = getattr(t, "order_type", None)
        direction = (
            "buy" if order_type == 23
            else "sell" if order_type == 24
            else "unknown"
        )
        return {
            "trade_id": getattr(t, "traded_id", None),
            "order_id": getattr(t, "order_id", None),
            "code": getattr(t, "stock_code", ""),
            "direction": direction,
            "price": float(getattr(t, "traded_price", 0.0) or 0.0),
            "volume": int(getattr(t, "traded_volume", 0) or 0),
            "traded_amount": float(getattr(t, "traded_amount", 0.0) or 0.0),
            "trade_time": getattr(t, "traded_time", None),
        }
