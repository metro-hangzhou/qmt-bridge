"""qmt-bridge: QMT broker bridge with 大QMT HTTP + miniQMT xtquant dual-mode support."""

from .bridge import QMTBridge
from .daqmt_bridge import DaQMTBridge
from .xtquant_bridge import XtQuantBridge
from .xtdata_bridge import XtDataBridge

__version__ = "0.1.0"
__all__ = ["QMTBridge", "DaQMTBridge", "XtQuantBridge", "XtDataBridge"]
