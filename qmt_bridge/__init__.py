"""qmt-bridge: QMT broker bridge with 大QMT HTTP + miniQMT xtquant dual-mode support."""

from .bridge import QMTBridge, BridgePool
from .daqmt_bridge import DaQMTBridge
from .xtquant_bridge import XtQuantBridge
from .xtdata_bridge import XtDataBridge
from .mock_bridge import MockBridge
from .constants import (
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    ORDER_TYPE_BEST,
    BridgeCallback,
)

__version__ = "0.2.0"
__all__ = [
    "QMTBridge", "BridgePool",
    "DaQMTBridge", "XtQuantBridge", "XtDataBridge",
    "MockBridge",
    "ORDER_TYPE_LIMIT", "ORDER_TYPE_MARKET", "ORDER_TYPE_BEST",
    "BridgeCallback",
]
