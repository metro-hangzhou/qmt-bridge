"""qmt CLI — broker commands for 大QMT HTTP / miniQMT xtquant."""
from __future__ import annotations

import json
import sys
from typing import Annotated, Optional

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from qmt_bridge import QMTBridge

app = typer.Typer(
    name="qmt",
    help="QMT broker CLI — 大QMT HTTP / miniQMT xtquant dual-mode",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()
err = Console(stderr=True)

# ---------------------------------------------------------------------------
# Shared option types
# ---------------------------------------------------------------------------

_Mode   = Annotated[str,  typer.Option("--mode",  "-m", help="auto | daqmt | miniqmt", show_default=True)]
_Url    = Annotated[str,  typer.Option("--url",   "-u", help="大QMT server URL",        show_default=True)]
_Acct   = Annotated[str,  typer.Option("--account-id",    help="miniQMT account ID")]
_Path   = Annotated[str,  typer.Option("--mini-qmt-path", help="miniQMT userdata_mini path")]
_Secret = Annotated[str,  typer.Option("--secret", envvar="DAQMT_BRIDGE_SECRET", help="Bridge secret", show_default=False)]
_Json   = Annotated[bool, typer.Option("--json",   is_flag=True, help="Output as JSON (stdout)")]


def _connect(
    mode: str,
    url: str,
    account_id: str,
    mini_qmt_path: str,
    secret: str,
) -> QMTBridge:
    bridge = QMTBridge({
        "mode": mode,
        "base_url": url,
        "account_id": account_id,
        "mini_qmt_path": mini_qmt_path,
        "secret": secret,
    })
    if not bridge.connect():
        err.print("[bold red]error:[/] could not connect to broker (tried all modes)")
        raise typer.Exit(1)
    return bridge


def _fmt(v: object, fmt: str = "") -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}" if not fmt else format(v, fmt)
    return str(v)


def _out(data: object, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    # non-JSON output is handled by the caller with rich tables


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@app.command()
def status(
    mode:          _Mode   = "auto",
    url:           _Url    = "http://127.0.0.1:9000",
    account_id:    _Acct   = "",
    mini_qmt_path: _Path   = "",
    secret:        _Secret = "",
    as_json:       _Json   = False,
) -> None:
    """Check broker connection status."""
    bridge = QMTBridge({"mode": mode, "base_url": url, "account_id": account_id,
                        "mini_qmt_path": mini_qmt_path, "secret": secret})
    ok = bridge.connect()
    active_mode = bridge.mode_used()
    if as_json:
        print(json.dumps({"connected": ok, "mode": active_mode, "url": url}))
        raise typer.Exit(0 if ok else 1)
    if ok:
        console.print(f"[green]✓[/] connected via [bold]{active_mode}[/]  ({url})")
    else:
        err.print("[bold red]✗[/] not connected")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# balance
# ---------------------------------------------------------------------------

@app.command()
def balance(
    mode:          _Mode   = "auto",
    url:           _Url    = "http://127.0.0.1:9000",
    account_id:    _Acct   = "",
    mini_qmt_path: _Path   = "",
    secret:        _Secret = "",
    as_json:       _Json   = False,
) -> None:
    """Show account balance."""
    bridge = _connect(mode, url, account_id, mini_qmt_path, secret)
    raw = bridge.get_balance()
    if as_json:
        _out(raw, True)
        return
    t = Table(box=box.SIMPLE_HEAD, show_header=False, padding=(0, 2))
    t.add_column("field", style="dim")
    t.add_column("value", justify="right", style="bold cyan")
    t.add_row("Available",    _fmt(raw.get("available")))
    t.add_row("Total Asset",  _fmt(raw.get("total_asset")))
    t.add_row("Frozen Cash",  _fmt(raw.get("frozen_cash")))
    t.add_row("Market Value", _fmt(raw.get("market_value")))
    t.add_row("Mode",         bridge.mode_used())
    console.print(t)


# ---------------------------------------------------------------------------
# positions
# ---------------------------------------------------------------------------

@app.command()
def positions(
    mode:          _Mode   = "auto",
    url:           _Url    = "http://127.0.0.1:9000",
    account_id:    _Acct   = "",
    mini_qmt_path: _Path   = "",
    secret:        _Secret = "",
    as_json:       _Json   = False,
) -> None:
    """Show current positions."""
    bridge = _connect(mode, url, account_id, mini_qmt_path, secret)
    rows = bridge.get_positions()
    if as_json:
        _out(rows, True)
        return
    if not rows:
        console.print("[dim]no positions[/]")
        return
    t = Table(box=box.SIMPLE_HEAD, show_edge=False)
    for col, just in [
        ("Code", "left"), ("Volume", "right"), ("Available", "right"),
        ("Avg Price", "right"), ("Market Value", "right"), ("Frozen", "right"),
    ]:
        t.add_column(col, justify=just)
    for p in rows:
        t.add_row(
            str(p.get("code", "")),
            _fmt(p.get("volume")),
            _fmt(p.get("available")),
            _fmt(p.get("avg_price")),
            _fmt(p.get("market_value")),
            _fmt(p.get("frozen_volume")),
        )
    console.print(t)


# ---------------------------------------------------------------------------
# orders
# ---------------------------------------------------------------------------

@app.command()
def orders(
    mode:          _Mode   = "auto",
    url:           _Url    = "http://127.0.0.1:9000",
    account_id:    _Acct   = "",
    mini_qmt_path: _Path   = "",
    secret:        _Secret = "",
    as_json:       _Json   = False,
) -> None:
    """Show today's orders."""
    bridge = _connect(mode, url, account_id, mini_qmt_path, secret)
    rows = bridge.get_today_orders()
    if as_json:
        _out(rows, True)
        return
    if not rows:
        console.print("[dim]no orders today[/]")
        return
    t = Table(box=box.SIMPLE_HEAD, show_edge=False)
    for col, just in [
        ("Order ID", "left"), ("Code", "left"), ("Dir", "center"),
        ("Price", "right"), ("Volume", "right"), ("Filled", "right"),
        ("Status", "left"), ("Time", "left"),
    ]:
        t.add_column(col, justify=just)
    for o in rows:
        direction = str(o.get("direction", ""))
        dir_fmt = "[green]B[/]" if direction == "buy" else "[red]S[/]" if direction == "sell" else direction
        t.add_row(
            str(o.get("order_id", "")),
            str(o.get("code", "")),
            dir_fmt,
            _fmt(o.get("price")),
            _fmt(o.get("volume")),
            _fmt(o.get("filled_volume")),
            str(o.get("status", "")),
            str(o.get("order_time", "")),
        )
    console.print(t)


# ---------------------------------------------------------------------------
# trades
# ---------------------------------------------------------------------------

@app.command()
def trades(
    mode:          _Mode   = "auto",
    url:           _Url    = "http://127.0.0.1:9000",
    account_id:    _Acct   = "",
    mini_qmt_path: _Path   = "",
    secret:        _Secret = "",
    as_json:       _Json   = False,
) -> None:
    """Show today's trades."""
    bridge = _connect(mode, url, account_id, mini_qmt_path, secret)
    rows = bridge.get_today_trades()
    if as_json:
        _out(rows, True)
        return
    if not rows:
        console.print("[dim]no trades today[/]")
        return
    t = Table(box=box.SIMPLE_HEAD, show_edge=False)
    for col, just in [
        ("Trade ID", "left"), ("Code", "left"), ("Dir", "center"),
        ("Price", "right"), ("Volume", "right"), ("Amount", "right"), ("Time", "left"),
    ]:
        t.add_column(col, justify=just)
    for tr in rows:
        direction = str(tr.get("direction", ""))
        dir_fmt = "[green]B[/]" if direction == "buy" else "[red]S[/]" if direction == "sell" else direction
        t.add_row(
            str(tr.get("trade_id", "")),
            str(tr.get("code", "")),
            dir_fmt,
            _fmt(tr.get("price")),
            _fmt(tr.get("volume")),
            _fmt(tr.get("traded_amount")),
            str(tr.get("trade_time", "")),
        )
    console.print(t)


# ---------------------------------------------------------------------------
# buy / sell
# ---------------------------------------------------------------------------

@app.command()
def buy(
    symbol: Annotated[str,   typer.Argument(help="Stock code, e.g. 600519.SH")],
    price:  Annotated[float, typer.Argument(help="Limit price")],
    volume: Annotated[int,   typer.Argument(help="Number of shares (multiples of 100)")],
    pr_type:       Annotated[int,  typer.Option("--pr-type", help="Price type (11=limit)")] = 11,
    mode:          _Mode   = "auto",
    url:           _Url    = "http://127.0.0.1:9000",
    account_id:    _Acct   = "",
    mini_qmt_path: _Path   = "",
    secret:        _Secret = "",
    as_json:       _Json   = False,
) -> None:
    """Place a buy order."""
    bridge = _connect(mode, url, account_id, mini_qmt_path, secret)
    result = bridge.buy(symbol, price, volume, pr_type=pr_type)
    if as_json:
        _out(result, True)
        return
    if result.get("error"):
        err.print(f"[bold red]error:[/] {result['error']}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] buy order submitted — {symbol}  {price:.2f} × {volume}")
    if result:
        console.print(f"  [dim]{result}[/]")


@app.command()
def sell(
    symbol: Annotated[str,   typer.Argument(help="Stock code, e.g. 600519.SH")],
    price:  Annotated[float, typer.Argument(help="Limit price")],
    volume: Annotated[int,   typer.Argument(help="Number of shares")],
    pr_type:       Annotated[int,  typer.Option("--pr-type", help="Price type (11=limit)")] = 11,
    mode:          _Mode   = "auto",
    url:           _Url    = "http://127.0.0.1:9000",
    account_id:    _Acct   = "",
    mini_qmt_path: _Path   = "",
    secret:        _Secret = "",
    as_json:       _Json   = False,
) -> None:
    """Place a sell order."""
    bridge = _connect(mode, url, account_id, mini_qmt_path, secret)
    result = bridge.sell(symbol, price, volume, pr_type=pr_type)
    if as_json:
        _out(result, True)
        return
    if result.get("error"):
        err.print(f"[bold red]error:[/] {result['error']}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] sell order submitted — {symbol}  {price:.2f} × {volume}")
    if result:
        console.print(f"  [dim]{result}[/]")


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------

@app.command()
def cancel(
    order_sys_id: Annotated[str, typer.Argument(help="Order system ID (daqmt) or integer order_id (miniqmt)")],
    mode:          _Mode   = "auto",
    url:           _Url    = "http://127.0.0.1:9000",
    account_id:    _Acct   = "",
    mini_qmt_path: _Path   = "",
    secret:        _Secret = "",
    as_json:       _Json   = False,
) -> None:
    """Cancel an order by ID."""
    bridge = _connect(mode, url, account_id, mini_qmt_path, secret)
    active = bridge.mode_used()
    if active == "daqmt":
        result = bridge.cancel_by_id(order_sys_id)
    else:
        try:
            result = bridge.cancel(order_id=int(order_sys_id))
        except ValueError:
            err.print("[bold red]error:[/] miniqmt mode requires integer order_id")
            raise typer.Exit(1)
    if as_json:
        _out(result, True)
        return
    if isinstance(result, dict) and result.get("error"):
        err.print(f"[bold red]error:[/] {result['error']}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/] cancel submitted — {order_sys_id}")


@app.command("cancel-all")
def cancel_all(
    mode:          _Mode   = "auto",
    url:           _Url    = "http://127.0.0.1:9000",
    account_id:    _Acct   = "",
    mini_qmt_path: _Path   = "",
    secret:        _Secret = "",
    as_json:       _Json   = False,
    yes:           Annotated[bool, typer.Option("--yes", "-y", help="Skip confirmation")] = False,
) -> None:
    """Cancel ALL open orders."""
    if not yes:
        typer.confirm("Cancel ALL open orders?", abort=True)
    bridge = _connect(mode, url, account_id, mini_qmt_path, secret)
    result = bridge.cancel_all()
    if as_json:
        _out(result, True)
        return
    console.print(f"[green]✓[/] cancel-all submitted")
    if result:
        console.print(f"  [dim]{result}[/]")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main() -> None:
    app()


if __name__ == "__main__":
    main()
