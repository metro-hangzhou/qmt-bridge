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

    def buy(self, code: str, price: float, volume: int, price_type: int = 11) -> dict:
        """买入。price_type=11 限价，price_type=5 市价。
        返回 {"order_id": id, "status": "submitted"} 或 {"error": str}。"""
        if not self.is_available():
            return {"error": "XtQuantBridge not connected"}
        try:
            order_id = self.xt_trader.order_stock(
                self.account, code, 23, volume, price_type, price,
            )
            logger.info(f"XtQuantBridge buy: {code} @ {price} x {volume} type={price_type}, order_id={order_id}")
            return {"order_id": order_id, "status": "submitted"}
        except Exception as e:
            logger.error(f"XtQuantBridge buy error: {e}")
            return {"error": str(e)}

    def sell(self, code: str, price: float, volume: int, price_type: int = 11) -> dict:
        """卖出。price_type=11 限价，price_type=5 市价。
        返回 {"order_id": id, "status": "submitted"} 或 {"error": str}。"""
        if not self.is_available():
            return {"error": "XtQuantBridge not connected"}
        try:
            order_id = self.xt_trader.order_stock(
                self.account, code, 24, volume, price_type, price,
            )
            logger.info(f"XtQuantBridge sell: {code} @ {price} x {volume} type={price_type}, order_id={order_id}")
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

    # ---------------- session / lifecycle ----------------

    def run_forever(self) -> None:
        """启动事件循环，阻塞调用线程。需在 register_callback 之后调用。"""
        if not self.is_available():
            return
        try:
            self.xt_trader.run_forever()
        except Exception as e:
            logger.warning(f"XtQuantBridge.run_forever() failed: {e}")

    def set_relaxed_response_order_enabled(self, enabled: bool) -> None:
        """控制响应顺序检查是否宽松。"""
        if not self.is_available():
            return
        try:
            self.xt_trader.set_relaxed_response_order_enabled(enabled)
        except Exception as e:
            logger.warning(f"XtQuantBridge.set_relaxed_response_order_enabled() failed: {e}")

    # ---------------- callbacks ----------------

    def register_callback(self, callback: Any) -> None:
        """注册回调对象。callback 需实现以下方法：

        on_disconnected()
        on_account_status(status)
        on_stock_order(account, order)
        on_stock_trade(account, trade)
        on_order_error(account, order_error)
        on_cancel_error(account, cancel_error)
        on_order_stock_async_response(account, seq, order_id)
        on_cancel_order_stock_async_response(account, seq, order_sysid)
        on_stock_asset(account, asset)

        未连接时 no-op，不 raise。"""
        if not self.is_available():
            return
        try:
            self.xt_trader.register_callback(callback)
        except Exception as e:
            logger.warning(f"XtQuantBridge.register_callback() failed: {e}")

    # ---------------- async order operations ----------------

    def buy_async(self, code: str, price: float, volume: int, price_type: int = 11) -> int:
        """异步买入。返回 async seq（int），-1 表示失败。
        Result delivered via `on_order_stock_async_response(account, seq, order_id)` callback."""
        if not self.is_available():
            return -1
        try:
            seq = self.xt_trader.order_stock_async(
                self.account, code, 23, volume, price_type, price
            )
            logger.info(
                f"XtQuantBridge buy_async: {code} @ {price} x {volume}, seq={seq}"
            )
            return seq if seq is not None else -1
        except Exception as e:
            logger.warning(f"XtQuantBridge.buy_async() failed: {e}")
            return -1

    def sell_async(self, code: str, price: float, volume: int, price_type: int = 11) -> int:
        """异步卖出。返回 async seq（int），-1 表示失败。
        Result delivered via `on_order_stock_async_response(account, seq, order_id)` callback."""
        if not self.is_available():
            return -1
        try:
            seq = self.xt_trader.order_stock_async(
                self.account, code, 24, volume, price_type, price
            )
            logger.info(
                f"XtQuantBridge sell_async: {code} @ {price} x {volume}, seq={seq}"
            )
            return seq if seq is not None else -1
        except Exception as e:
            logger.warning(f"XtQuantBridge.sell_async() failed: {e}")
            return -1

    def cancel_async(self, order_id: int) -> int:
        """异步撤单。返回 async seq，-1 表示失败。
        Result delivered via `on_order_stock_async_response(account, seq, order_id)` callback."""
        if not self.is_available():
            return -1
        try:
            seq = self.xt_trader.cancel_order_stock_async(self.account, order_id)
            return seq if seq is not None else -1
        except Exception as e:
            logger.warning(f"XtQuantBridge.cancel_async() failed: {e}")
            return -1

    def cancel_by_sysid(self, market: int, sysid: str) -> bool:
        """按系统委托号撤单（同步）。market: 0=上海, 1=深圳。成功返回 True。"""
        if not self.is_available():
            return False
        try:
            self.xt_trader.cancel_order_stock_sysid(self.account, market, sysid)
            return True
        except Exception as e:
            logger.warning(f"XtQuantBridge.cancel_by_sysid() failed: {e}")
            return False

    def cancel_by_sysid_async(self, market: int, sysid: str) -> int:
        """按系统委托号异步撤单。返回 async seq，-1 表示失败。
        Result delivered via `on_cancel_order_stock_async_response(account, seq, order_sysid)` callback."""
        if not self.is_available():
            return -1
        try:
            seq = self.xt_trader.cancel_order_stock_sysid_async(
                self.account, market, sysid
            )
            return seq if seq is not None else -1
        except Exception as e:
            logger.warning(f"XtQuantBridge.cancel_by_sysid_async() failed: {e}")
            return -1

    # ---------------- account management ----------------

    def unsubscribe(self) -> int:
        """取消账户订阅。成功返回 0。"""
        if not self.is_available():
            return -1
        try:
            result = self.xt_trader.unsubscribe(self.account)
            return result if result is not None else -1
        except Exception as e:
            logger.warning(f"XtQuantBridge.unsubscribe() failed: {e}")
            return -1

    def query_account_infos(self) -> list[dict]:
        """查询本 trader 绑定的所有账户信息。失败返回 []。"""
        if not self.is_available():
            return []
        try:
            result = self.xt_trader.query_account_infos()
            if not result:
                return []
            return result if isinstance(result, list) else list(result)
        except Exception as e:
            logger.warning(f"XtQuantBridge.query_account_infos() failed: {e}")
            return []

    def query_account_status(self) -> list[dict]:
        """查询账户状态列表。失败返回 []。"""
        if not self.is_available():
            return []
        try:
            result = self.xt_trader.query_account_status()
            if not result:
                return []
            return result if isinstance(result, list) else list(result)
        except Exception as e:
            logger.warning(f"XtQuantBridge.query_account_status() failed: {e}")
            return []

    def fund_transfer(self, transfer_type: int, amount: float) -> dict:
        """资金划转。transfer_type 含义见 xtquant 文档。失败返回 {}。"""
        if not self.is_available():
            return {}
        try:
            result = self.xt_trader.fund_transfer(self.account, transfer_type, amount)
            if result is None:
                return {}
            return result if isinstance(result, dict) else {"raw": result}
        except Exception as e:
            logger.warning(f"XtQuantBridge.fund_transfer() failed: {e}")
            return {}

    def sync_transaction_from_external(self, transaction: dict) -> bool:
        """将外部交易记录同步进系统。失败返回 False。"""
        if not self.is_available():
            return False
        try:
            self.xt_trader.sync_transaction_from_external(transaction)
            return True
        except Exception as e:
            logger.warning(f"XtQuantBridge.sync_transaction_from_external() failed: {e}")
            return False

    # ---------------- extended queries ----------------

    def query_position_statistics(self) -> list[dict]:
        """持仓统计（行业/板块分布等）。失败返回 []。"""
        if not self.is_available():
            return []
        try:
            result = self.xt_trader.query_position_statistics(self.account)
            if not result:
                return []
            return result if isinstance(result, list) else list(result)
        except Exception as e:
            logger.warning(f"XtQuantBridge.query_position_statistics() failed: {e}")
            return []

    def query_new_purchase_limit(self) -> dict:
        """查询新股申购额度/限额。失败返回 {}。"""
        if not self.is_available():
            return {}
        try:
            result = self.xt_trader.query_new_purchase_limit(self.account)
            if result is None:
                return {}
            return result if isinstance(result, dict) else {"raw": result}
        except Exception as e:
            logger.warning(f"XtQuantBridge.query_new_purchase_limit() failed: {e}")
            return {}

    def query_ipo_data(self) -> list[dict]:
        """查询可申购新股信息。失败返回 []。"""
        if not self.is_available():
            return []
        try:
            result = self.xt_trader.query_ipo_data(self.account)
            if not result:
                return []
            return result if isinstance(result, list) else list(result)
        except Exception as e:
            logger.warning(f"XtQuantBridge.query_ipo_data() failed: {e}")
            return []

    def export_data(self, data_type: str, start: str = "", end: str = "") -> list[dict]:
        """导出历史数据记录。失败返回 []。"""
        if not self.is_available():
            return []
        try:
            result = self.xt_trader.export_data(self.account, data_type, start, end)
            if not result:
                return []
            return result if isinstance(result, list) else list(result)
        except Exception as e:
            logger.warning(f"XtQuantBridge.export_data() failed: {e}")
            return []

    def query_data(self, data_type: str, **kwargs) -> list[dict]:
        """通用查询接口。失败返回 []。"""
        if not self.is_available():
            return []
        try:
            result = self.xt_trader.query_data(self.account, data_type, **kwargs)
            if not result:
                return []
            return result if isinstance(result, list) else list(result)
        except Exception as e:
            logger.warning(f"XtQuantBridge.query_data() failed: {e}")
            return []

    # ---------------- credit / margin queries ----------------

    def query_credit_detail(self) -> dict:
        """融资融券账户详情。非信用账户或失败返回 {}。"""
        if not self.is_available():
            return {}
        try:
            result = self.xt_trader.query_credit_detail(self.account)
            if result is None:
                return {}
            return result if isinstance(result, dict) else {"raw": result}
        except Exception as e:
            logger.warning(f"XtQuantBridge.query_credit_detail() failed: {e}")
            return {}

    def query_stk_compacts(self) -> list[dict]:
        """查询负债合约。失败返回 []。"""
        if not self.is_available():
            return []
        try:
            result = self.xt_trader.query_stk_compacts(self.account)
            if not result:
                return []
            return result if isinstance(result, list) else list(result)
        except Exception as e:
            logger.warning(f"XtQuantBridge.query_stk_compacts() failed: {e}")
            return []

    def query_credit_subjects(self) -> list[dict]:
        """查询标的证券。失败返回 []。"""
        if not self.is_available():
            return []
        try:
            result = self.xt_trader.query_credit_subjects(self.account)
            if not result:
                return []
            return result if isinstance(result, list) else list(result)
        except Exception as e:
            logger.warning(f"XtQuantBridge.query_credit_subjects() failed: {e}")
            return []

    def query_credit_slo_code(self) -> list[dict]:
        """查询可融券标的。失败返回 []。"""
        if not self.is_available():
            return []
        try:
            result = self.xt_trader.query_credit_slo_code(self.account)
            if not result:
                return []
            return result if isinstance(result, list) else list(result)
        except Exception as e:
            logger.warning(f"XtQuantBridge.query_credit_slo_code() failed: {e}")
            return []

    def query_credit_assure(self) -> list[dict]:
        """查询担保证券。失败返回 []。"""
        if not self.is_available():
            return []
        try:
            result = self.xt_trader.query_credit_assure(self.account)
            if not result:
                return []
            return result if isinstance(result, list) else list(result)
        except Exception as e:
            logger.warning(f"XtQuantBridge.query_credit_assure() failed: {e}")
            return []

    def query_com_fund(self) -> dict:
        """查询组合资金。失败返回 {}。"""
        if not self.is_available():
            return {}
        try:
            result = self.xt_trader.query_com_fund(self.account)
            if result is None:
                return {}
            return result if isinstance(result, dict) else {"raw": result}
        except Exception as e:
            logger.warning(f"XtQuantBridge.query_com_fund() failed: {e}")
            return {}

    def query_com_position(self) -> list[dict]:
        """查询组合持仓。失败返回 []。"""
        if not self.is_available():
            return []
        try:
            result = self.xt_trader.query_com_position(self.account)
            if not result:
                return []
            return result if isinstance(result, list) else list(result)
        except Exception as e:
            logger.warning(f"XtQuantBridge.query_com_position() failed: {e}")
            return []

    # ---------------- SMT (securities margin trading) ----------------

    def smt_query_quoter(self, code: str) -> list[dict]:
        """融券报价查询。失败返回 []。"""
        if not self.is_available():
            return []
        try:
            result = self.xt_trader.smt_query_quoter(self.account, code)
            if not result:
                return []
            return result if isinstance(result, list) else list(result)
        except Exception as e:
            logger.warning(f"XtQuantBridge.smt_query_quoter() failed: {e}")
            return []

    def smt_negotiate_order_async(self, code: str, volume: int, price: float) -> int:
        """协议融券异步下单。返回 seq id，-1 表示失败。"""
        if not self.is_available():
            return -1
        try:
            seq = self.xt_trader.smt_negotiate_order_async(
                self.account, code, volume, price
            )
            return seq if seq is not None else -1
        except Exception as e:
            logger.warning(f"XtQuantBridge.smt_negotiate_order_async() failed: {e}")
            return -1

    def smt_query_compact(self) -> list[dict]:
        """融券合约查询。失败返回 []。"""
        if not self.is_available():
            return []
        try:
            result = self.xt_trader.smt_query_compact(self.account)
            if not result:
                return []
            return result if isinstance(result, list) else list(result)
        except Exception as e:
            logger.warning(f"XtQuantBridge.smt_query_compact() failed: {e}")
            return []

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
