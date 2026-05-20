"""qmt-bridge shared constants, order type enums, and callback protocol."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

# ── Order price types (prType for 大QMT; price_type for miniQMT) ──────────────
ORDER_TYPE_LIMIT        = 11   # 限价单 (FIX_PRICE)
ORDER_TYPE_MARKET       = 5    # 市价单 (ANY_PRICE)
ORDER_TYPE_BEST         = 4    # 对手方最优价格委托
ORDER_TYPE_CANCEL_REST  = 6    # 最优五档即时成交剩余撤销
ORDER_TYPE_ALL_OR_NONE  = 7    # 最优五档全额成交或撤销
ORDER_TYPE_BEST_5       = 8    # 市价最优五档即时成交剩余转限价

# xtquant xtconstant mirror (avoid importing xtquant at module level)
XT_STOCK_BUY  = 23
XT_STOCK_SELL = 24

# Normalized order status strings
STATUS_PENDING   = "pending"
STATUS_PARTIAL   = "partial"
STATUS_FILLED    = "filled"
STATUS_CANCELED  = "canceled"
STATUS_REJECTED  = "rejected"
STATUS_UNKNOWN   = "unknown"


@runtime_checkable
class BridgeCallback(Protocol):
    """Event callback protocol for all bridge types.

    Implement only the methods you need; leave the rest unimplemented.
    All callbacks are invoked on a background thread — keep them
    non-blocking and thread-safe. Exceptions are caught and logged,
    never propagated to the caller.
    """

    def on_order(self, order: dict) -> None:
        """New order submitted or existing order state changed."""
        ...

    def on_trade(self, trade: dict) -> None:
        """Fill event: full or partial fill of an order."""
        ...

    def on_order_error(self, order: dict, error: str) -> None:
        """Order rejected or errored."""
        ...

    def on_disconnected(self) -> None:
        """Connection to broker lost (fired before reconnect attempt)."""
        ...

    def on_reconnected(self) -> None:
        """Connection successfully restored after a disconnect."""
        ...
