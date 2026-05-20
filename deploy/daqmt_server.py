# -*- coding: gbk -*-
"""DaQMT in-strategy Tornado HTTP server.

This module is NOT a standalone script. It must be loaded INSIDE the
DaQMT (��QMT) strategy runtime so that `get_trade_detail_data`,
`passorder`, `cancel`, `can_cancel_order` and `ContextInfo` are
available in the global scope of the strategy file that imports it.

All field accesses use getattr(obj, 'field', default) so a future
DaQMT field rename does NOT crash the HTTP handlers -- the missing
field will just come back as the default value.

Only stdlib + tornado is imported; the DaQMT embedded Python has no
pip, so anything outside the bundled runtime is forbidden.
"""

import json
import locale
import logging
import os

import tornado.ioloop
import tornado.web
from tornado.ioloop import IOLoop

_BRIDGE_SECRET = os.environ.get('DAQMT_BRIDGE_SECRET', '')


# ---------------------------------------------------------------------------
# logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s %(name)s: %(message)s',
)
logger = logging.getLogger('daqmt_server')


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

ACCOUNT_TYPE_MAP = {
    'stock': 'STOCK',
    'STOCK': 'STOCK',
}

# DaQMT order status codes considered "still alive" (cancelable).
# 0=δ�� 1=���� 5=���� (typical DaQMT mapping)
ACTIVE_ORDER_STATUS = (0, 1, 5)

DIRECTION_BUY = 48  # ord('0') -- DaQMT internal buy flag for stocks


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _account_type(account_str):
    """Map account query string to DaQMT account_type literal."""
    return ACCOUNT_TYPE_MAP.get(account_str or 'stock', 'STOCK')


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _safe_int(v, default=0):
    try:
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _infer_direction(o):
    """Best-effort buy/sell inference.

    DaQMT historically uses m_nDirection (48=buy, 49=sell) but some
    builds expose only m_nOffsetFlag. If neither is present we admit
    defeat and return 'unknown' rather than guessing.
    """
    if hasattr(o, 'm_nDirection'):
        return 'buy' if getattr(o, 'm_nDirection', 0) == DIRECTION_BUY else 'sell'
    if hasattr(o, 'm_nOffsetFlag'):
        return 'buy' if getattr(o, 'm_nOffsetFlag', 0) == DIRECTION_BUY else 'sell'
    return 'unknown'


def _position_to_dict(p):
    """Convert a DaQMT position object to a plain dict.

    Every field access is guarded with getattr; a missing field in some
    future DaQMT build degrades to default, never to AttributeError.
    """
    code = '{0}.{1}'.format(
        getattr(p, 'm_strInstrumentID', ''),
        getattr(p, 'm_strExchangeID', ''),
    )
    return {
        'code': code,
        'instrument_id': getattr(p, 'm_strInstrumentID', ''),
        'exchange_id': getattr(p, 'm_strExchangeID', ''),
        'instrument_name': getattr(p, 'm_strInstrumentName', ''),
        'direction': getattr(p, 'm_nDirection', 0),
        'volume': _safe_int(getattr(p, 'm_nVolume', 0)),
        'can_use_volume': _safe_int(getattr(p, 'm_nCanUseVolume', 0)),
        'frozen_volume': _safe_int(getattr(p, 'm_nFrozenVolume', 0)),
        'yesterday_volume': _safe_int(getattr(p, 'm_nYesterdayVolume', 0)),
        'on_road_volume': _safe_int(getattr(p, 'm_nOnRoadVolume', 0)),
        'open_price': _safe_float(getattr(p, 'm_dOpenPrice', 0.0)),
        'last_price': _safe_float(getattr(p, 'm_dLastPrice', 0.0)),
        'market_value': _safe_float(getattr(p, 'm_dMarketValue', 0.0)),
        'float_profit': _safe_float(getattr(p, 'm_dFloatProfit', 0.0)),
        'profit_rate': _safe_float(getattr(p, 'm_dProfitRate', 0.0)),
        'stock_holder': getattr(p, 'm_strStockHolder', ''),
        'future_trade_type': getattr(p, 'm_eFutureTradeType', 0),
        'expire_date': getattr(p, 'm_strExpireDate', ''),
    }


def _order_to_dict(o):
    """Convert a DaQMT order object to a plain dict.

    Returns the FULL set of fields downstream callers need
    (order_id/code/direction/price/volume/filled_volume/status/order_time),
    not the abbreviated 4-field shape some online examples use.
    """
    code = '{0}.{1}'.format(
        getattr(o, 'm_strInstrumentID', ''),
        getattr(o, 'm_strExchangeID', ''),
    )
    price = getattr(o, 'm_dLimitPrice', None)
    if price is None:
        price = getattr(o, 'm_dPrice', 0.0)
    volume = getattr(o, 'm_nVolumeTotalOriginal', None)
    if volume is None:
        volume = getattr(o, 'm_nVolume', 0)
    return {
        'order_id': getattr(o, 'm_strOrderSysID', ''),
        'code': code,
        'instrument_id': getattr(o, 'm_strInstrumentID', ''),
        'exchange_id': getattr(o, 'm_strExchangeID', ''),
        'direction': _infer_direction(o),
        'price': _safe_float(price),
        'volume': _safe_int(volume),
        'filled_volume': _safe_int(getattr(o, 'm_nVolumeTraded', 0)),
        'remaining_volume': _safe_int(getattr(o, 'm_nVolumeTotal', 0)),
        'status': _safe_int(getattr(o, 'm_nOrderStatus', 0)),
        'order_time': getattr(o, 'm_strInsertDateTime', None),
    }


# ---------------------------------------------------------------------------
# DaQMT-facing query wrappers (each one try/except'd)
# ---------------------------------------------------------------------------

def _query_positions(accountID, account_type):
    try:
        rows = get_trade_detail_data(accountID, account_type, 'position', 'qmt')
        return rows or []
    except Exception as exc:
        logger.error('get_trade_detail_data(position) failed: %s', exc)
        return []


def _query_account(accountID, account_type):
    try:
        rows = get_trade_detail_data(accountID, account_type, 'account', 'qmt')
        return rows or []
    except Exception as exc:
        logger.error('get_trade_detail_data(account) failed: %s', exc)
        return []


def _query_orders(accountID, account_type):
    try:
        rows = get_trade_detail_data(accountID, account_type, 'order', 'qmt')
        return rows or []
    except Exception as exc:
        logger.error('get_trade_detail_data(order) failed: %s', exc)
        return []


def _query_trades(accountID, account_type):
    try:
        rows = get_trade_detail_data(accountID, account_type, 'trade', 'qmt')
        return rows or []
    except Exception as exc:
        logger.error('_query_trades failed: %s', exc)
        return []


def _trade_to_dict(t) -> dict:
    direction_raw = _safe_int(getattr(t, 'm_nOffsetFlag', getattr(t, 'm_nDirection', 0)))
    direction = 'buy' if direction_raw == 48 else 'sell'
    return {
        'trade_id':   getattr(t, 'm_strTradeID', '') or '',
        'order_id':   getattr(t, 'm_strOrderSysID', '') or '',
        'code':       getattr(t, 'm_strInstrumentID', '') or '',
        'exchange':   getattr(t, 'm_strExchangeID', '') or '',
        'direction':  direction,
        'price':      _safe_float(getattr(t, 'm_dPrice', 0.0)),
        'volume':     _safe_int(getattr(t, 'm_nVolume', 0)),
        'amount':     _safe_float(getattr(t, 'm_dTradeAmount', 0.0)),
        'trade_time': getattr(t, 'm_strTradeTime', '') or '',
    }


# ---------------------------------------------------------------------------
# tornado handlers
# ---------------------------------------------------------------------------

class BaseHandler(tornado.web.RequestHandler):
    """Unifies JSON content-type + error response for every endpoint."""

    def set_default_headers(self):
        self.set_header('Content-Type', 'application/json; charset=utf-8')

    def write_json(self, obj, status=200):
        self.set_status(status)
        # ensure_ascii=False so ���� names survive; UTF-8 via header above.
        self.write(json.dumps(obj, ensure_ascii=False, default=str))

    def write_error(self, status_code, **kwargs):
        self.set_header('Content-Type', 'application/json; charset=utf-8')
        self.write(json.dumps({
            'status': 'error',
            'code': status_code,
            'message': self._reason,
        }, ensure_ascii=False))

    def prepare(self):
        if _BRIDGE_SECRET and self.request.headers.get('X-Bridge-Secret') != _BRIDGE_SECRET:
            self.set_status(403)
            self.finish(json.dumps({'error': 'forbidden'}))

    def _ctx(self):
        return self.application.ContextInfo

    def _account(self):
        return self.application.accountID


class HoldingHandler(BaseHandler):
    """GET /api/holding?account=stock -> dict keyed by code."""

    def get(self):
        account_type = _account_type(self.get_argument('account', 'stock'))
        positions = _query_positions(self._account(), account_type)
        out = {}
        for p in positions:
            try:
                d = _position_to_dict(p)
                out[d['code']] = d
            except Exception as exc:
                logger.error('position serialization failed: %s', exc)
        self.write_json(out)


class TotalMoneyHandler(BaseHandler):
    """GET /api/money/total?account=stock -> {total_money}."""

    def get(self):
        account_type = _account_type(self.get_argument('account', 'stock'))
        accounts = _query_account(self._account(), account_type)
        total = 0.0
        if accounts:
            total = _safe_float(getattr(accounts[0], 'm_dBalance', 0.0))
        self.write_json({'total_money': total})


class AvailableMoneyHandler(BaseHandler):
    """GET /api/money/available?account=stock -> {available_money}."""

    def get(self):
        account_type = _account_type(self.get_argument('account', 'stock'))
        accounts = _query_account(self._account(), account_type)
        avail = 0.0
        if accounts:
            avail = _safe_float(getattr(accounts[0], 'm_dAvailable', 0.0))
        self.write_json({'available_money': avail})


class OrderStatusHandler(BaseHandler):
    """GET /api/order/status?account=stock -> {orders:[...]}."""

    def get(self):
        account_type = _account_type(self.get_argument('account', 'stock'))
        orders = _query_orders(self._account(), account_type)
        out = []
        for o in orders:
            try:
                out.append(_order_to_dict(o))
            except Exception as exc:
                logger.error('order serialization failed: %s', exc)
        self.write_json({'orders': out})


class TradeStatusHandler(BaseHandler):
    """GET /api/trade/status?account=stock -> {trades: [...]}"""

    def get(self):
        account_type = _account_type(self.get_argument('account', 'stock'))
        trades = _query_trades(self._account(), account_type)
        out = []
        for t in trades:
            try:
                out.append(_trade_to_dict(t))
            except Exception as exc:
                logger.error('trade serialization failed: %s', exc)
        self.write_json({'trades': out})


def _parse_json_body(handler):
    raw = handler.request.body or b''
    if not raw:
        return {}
    try:
        return json.loads(raw.decode('utf-8'))
    except Exception:
        # GBK runtime; tolerate clients that sent GBK bytes.
        try:
            return json.loads(raw.decode('gbk'))
        except Exception as exc:
            logger.error('json body decode failed: %s', exc)
            raise


class _PlaceOrderMixin(object):
    """Shared body parsing for buy/sell handlers."""

    DIRECTION_CODE = None  # 23=buy, 24=sell

    def _do_order(self):
        try:
            body = _parse_json_body(self)
        except Exception:
            self.write_json({'status': 'error', 'message': 'invalid json'}, status=400)
            return

        stock = body.get('stock')
        price = _safe_float(body.get('price', 0.0))
        volume = _safe_int(body.get('volume', 0))
        prType = _safe_int(body.get('prType', 11))

        if not stock or volume <= 0:
            self.write_json(
                {'status': 'error', 'message': 'stock + volume required'},
                status=400,
            )
            return

        ctx = self._ctx()
        account = self._account()
        try:
            order_ref = passorder(
                self.DIRECTION_CODE, 1101, account, stock,
                prType, price, volume, 'qmt', 2, ctx,
            )
            self.write_json({'status': 'success', 'order_ref': order_ref})
        except Exception as exc:
            logger.error('passorder failed: %s', exc)
            self.write_json({'status': 'error', 'message': str(exc)}, status=500)


class BuyHandler(BaseHandler, _PlaceOrderMixin):
    """POST /api/order/buy."""
    DIRECTION_CODE = 23

    def post(self):
        self._do_order()


class SellHandler(BaseHandler, _PlaceOrderMixin):
    """POST /api/order/sell."""
    DIRECTION_CODE = 24

    def post(self):
        self._do_order()


class CancelAllHandler(BaseHandler):
    """POST /api/order/cancel_all -> cancel every order whose status is in
    ACTIVE_ORDER_STATUS. Returns the list of order_sys_ids we attempted
    to cancel (whether the broker accepted them or not is async)."""

    def post(self):
        account_type = _account_type(self.get_argument('account', 'stock'))
        orders = _query_orders(self._account(), account_type)
        canceled = []
        for o in orders:
            status = _safe_int(getattr(o, 'm_nOrderStatus', 0))
            if status not in ACTIVE_ORDER_STATUS:
                continue
            sys_id = getattr(o, 'm_strOrderSysID', '')
            if not sys_id:
                continue
            try:
                if can_cancel_order(sys_id, self._account(), account_type):
                    cancel(sys_id, self._account(), account_type, self._ctx())
                    canceled.append(sys_id)
            except Exception as exc:
                logger.error('cancel(%s) failed: %s', sys_id, exc)
        self.write_json({'status': 'success', 'canceled_orders': canceled})


class CancelOrderHandler(BaseHandler):
    """POST /api/order/cancel_order.

    Body: {"stock": "000001.SZ", "volume": 1000, "account": "stock"}

    WARNING: matching is by (code, remaining_volume). If you have
    several active orders on the same code with the same remaining
    volume, **all of them will be canceled**. The DaQMT API has no
    user-supplied order_ref we can round-trip, and m_strOrderSysID is
    only known after the fact via get_trade_detail_data -- callers
    that need precise targeting should call /api/order/status first
    and cancel by order_id directly via a future endpoint.
    """

    def post(self):
        try:
            body = _parse_json_body(self)
        except Exception:
            self.write_json({'status': 'error', 'message': 'invalid json'}, status=400)
            return

        stock = body.get('stock')
        volume = _safe_int(body.get('volume', 0))
        account_type = _account_type(body.get('account', 'stock'))

        if not stock or volume <= 0:
            self.write_json(
                {'status': 'error', 'message': 'stock + volume required'},
                status=400,
            )
            return

        orders = _query_orders(self._account(), account_type)
        canceled_sys_ids = []
        for o in orders:
            status = _safe_int(getattr(o, 'm_nOrderStatus', 0))
            if status not in ACTIVE_ORDER_STATUS:
                continue
            code = '{0}.{1}'.format(
                getattr(o, 'm_strInstrumentID', ''),
                getattr(o, 'm_strExchangeID', ''),
            )
            if code != stock:
                continue
            remaining = _safe_int(getattr(o, 'm_nVolumeTotal', 0))
            if remaining != volume:
                continue
            sys_id = getattr(o, 'm_strOrderSysID', '')
            if not sys_id:
                continue
            try:
                if can_cancel_order(sys_id, self._account(), account_type):
                    cancel(sys_id, self._account(), account_type, self._ctx())
                    canceled_sys_ids.append(sys_id)
            except Exception as exc:
                logger.error('cancel(%s) failed: %s', sys_id, exc)

        self.write_json({'status': 'success', 'canceled_sys_ids': canceled_sys_ids})


class CancelByIdHandler(BaseHandler):
    """POST /api/order/cancel_by_id  Body: {"order_sys_id": "xxxx", "account": "stock"}

    Cancels a specific order by its m_strOrderSysID. Unlike cancel_order (which
    matches by code+remaining_volume), this is precise -- no partial-fill ambiguity.
    """

    def post(self):
        try:
            body = _parse_json_body(self)
        except Exception:
            self.set_status(400)
            self.write_json({'error': 'invalid json body'})
            return

        order_sys_id = body.get('order_sys_id', '')
        if not order_sys_id:
            self.set_status(400)
            self.write_json({'error': 'order_sys_id required'})
            return

        account_type = _account_type(body.get('account', self.get_argument('account', 'stock')))
        try:
            cancel(order_sys_id, self._account(), account_type, self.application.ContextInfo)
            self.write_json({'status': 'success', 'order_sys_id': order_sys_id})
        except Exception as exc:
            logger.error('cancel_by_id(%s) failed: %s', order_sys_id, exc)
            self.set_status(500)
            self.write_json({'error': str(exc), 'order_sys_id': order_sys_id})


# ---------------------------------------------------------------------------
# app factory + entrypoint
# ---------------------------------------------------------------------------

def make_app():
    return tornado.web.Application([
        (r'/api/holding', HoldingHandler),
        (r'/api/money/total', TotalMoneyHandler),
        (r'/api/money/available', AvailableMoneyHandler),
        (r'/api/order/status', OrderStatusHandler),
        (r'/api/order/buy', BuyHandler),
        (r'/api/order/sell', SellHandler),
        (r'/api/order/cancel_all', CancelAllHandler),
        (r'/api/order/cancel_order', CancelOrderHandler),
        (r'/api/order/cancel_by_id', CancelByIdHandler),
        (r'/api/trade/status', TradeStatusHandler),
    ])


def init(ContextInfo):
    """Entry point for 大QMT strategy runner.

    Starts the Tornado HTTP server on 127.0.0.1:9000.
    NOTE: This call never returns -- IOLoop.current().start() blocks.
    Any post-startup logic must be scheduled via IOLoop.current().call_later().
    """
    # Try to keep encoding consistent with DaQMT's GBK environment.
    try:
        locale.setlocale(locale.LC_ALL, '')
    except Exception:
        pass

    app = make_app()
    app.ContextInfo = ContextInfo
    app.accountID = ContextInfo.accountID
    app.listen(9000, address='127.0.0.1')
    logger.info('DaQMT HTTP Server: http://127.0.0.1:9000')
    IOLoop.current().start()


# This file is NOT a standalone script -- it relies on DaQMT-injected
# globals (get_trade_detail_data, passorder, cancel, can_cancel_order,
# ContextInfo). Load it from a DaQMT strategy file that does:
#
#     from daqmt_server import init
#     def init(ContextInfo):
#         from daqmt_server import init as _init
#         _init(ContextInfo)
#
# Running `python deploy/daqmt_server.py` directly will NameError on
# the DaQMT globals -- that is intentional.
if __name__ == '__main__':
    pass
