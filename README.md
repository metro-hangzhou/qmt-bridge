# qmt-bridge

QMT broker bridge — 大QMT HTTP + miniQMT xtquant dual-mode.

## 背景

迅投 QMT 有两个客户端版本：
- **大QMT** — 完整客户端，不暴露 xtquant SDK，但可在策略运行器内跑 Python 脚本
- **miniQMT** — 精简版，原生 xtquant SDK 可用

`xtquant` 官方 SDK 架构上只支持 miniQMT。本项目提供统一 bridge，同时支持两路。

## 模式

| 模式 | 数据通道 | 适用场景 |
|------|---------|---------|
| `daqmt` | HTTP → `deploy/daqmt_server.py` (Tornado :9000) | 装的是 大QMT |
| `miniqmt` | xtquant SDK → `XtQuantTrader` | 装的是 miniQMT |
| `auto` | 先 ping `daqmt`，不通则用 `miniqmt` | 默认 |

## 用法

```python
from qmt_bridge import QMTBridge

bridge = QMTBridge({
    "mode": "auto",
    "base_url": "http://127.0.0.1:9000",   # daqmt
    "mini_qmt_path": "C:/path/to/userdata_mini",  # miniqmt
    "account_id": "xxxxxxxx",
})
bridge.connect()
print(bridge.mode_used())  # "daqmt" or "miniqmt"
print(bridge.get_balance())
print(bridge.get_positions())
```

## 部署 大QMT server

把 `deploy/daqmt_server.py` 加到 大QMT 的策略列表，运行即起 Tornado :9000。
