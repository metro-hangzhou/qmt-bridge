# qmt-bridge

**大QMT / miniQMT 双模 broker bridge — Python 库 + CLI**

[![CI](https://github.com/metro-hangzhou/qmt-bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/metro-hangzhou/qmt-bridge/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://pypi.org/project/qmt-bridge/)
[![License](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)

---

## 是什么

A股量化交易中，QMT 有两种运行形态：

| 形态 | 说明 | SDK |
|---|---|---|
| **大QMT** | 完整客户端，策略在内置 Python 环境运行 | 无外部 SDK，需 HTTP server 桥接 |
| **miniQMT** | 轻量版，支持外部 Python 调用 | `xtquant` |

现有工具要么只支持 miniQMT（xtquant），要么需要窗口截图（easytrader）。**qmt-bridge** 同时覆盖两种形态，自动探测并切换：

```
大QMT 策略环境
  └─ deploy/daqmt_server.py  (Tornado HTTP :9000)
        ↑ HTTP
你的程序
  ├─ DaQMTBridge    -- HTTP client -> 大QMT
  ├─ XtQuantBridge  -- xtquant SDK -> miniQMT
  └─ QMTBridge      -- auto-detect 路由
```

---

## 安装

```bash
pip install qmt-bridge
# 或本地开发
pip install -e ".[dev]"
```

大QMT 服务端（在大QMT策略运行器执行一次）：

```python
# 大QMT 策略文件
from qmt_bridge.deploy import daqmt_server

def init(ContextInfo):
    daqmt_server.init(ContextInfo)   # 启动 Tornado HTTP server :9000
```

---

## CLI 快速上手

```bash
qmt status                          # 连接状态
qmt balance                         # 资金余额
qmt positions                       # 持仓（rich table）
qmt positions --json                # 持仓（JSON，适合脚本）
qmt orders                          # 今日委托
qmt trades                          # 今日成交

qmt buy  600519.SH 1800.00 100      # 限价买入
qmt sell 600519.SH 1820.00 100      # 限价卖出
qmt cancel <ORDER_SYS_ID>           # 撤单
qmt cancel-all                      # 撤全部（有确认提示，-y 跳过）

# 指定模式 / 地址
qmt balance --mode daqmt --url http://192.168.1.100:9000
qmt balance --mode miniqmt --account-id 88888888 --mini-qmt-path D:/miniQMT/userdata_mini

# 认证
export DAQMT_BRIDGE_SECRET=your-secret
qmt balance
```

---

## Python API

```python
from qmt_bridge import QMTBridge

bridge = QMTBridge({"mode": "auto"})
bridge.connect()

print(bridge.mode_used())      # "daqmt" | "miniqmt"
print(bridge.get_balance())
print(bridge.get_positions())

bridge.buy("600519.SH", 1800.0, 100)
bridge.sell("600519.SH", 1820.0, 100)
bridge.cancel_all()
bridge.disconnect()
```

行情数据（同机 xtdata，Polars DataFrame 输出）：

```python
from qmt_bridge import XtDataBridge

data = XtDataBridge()
df = data.get_klines("600519.SH", "1d", count=250)
latest = data.get_latest_quote(["600519.SH", "000001.SZ"])
```

---

## 命令参考

| 命令 | 说明 |
|---|---|
| `qmt status` | 连接状态 + 当前模式 |
| `qmt balance` | 资金余额 |
| `qmt positions` | 当前持仓 |
| `qmt orders` | 今日委托 |
| `qmt trades` | 今日成交 |
| `qmt buy SYMBOL PRICE VOL` | 限价买入 |
| `qmt sell SYMBOL PRICE VOL` | 限价卖出 |
| `qmt cancel ORDER_ID` | 撤单 |
| `qmt cancel-all` | 撤全部挂单 |

所有命令支持 `--json`、`--mode`、`--url`、`--secret`。

---

## API 覆盖度

| 模块 | 覆盖 | 状态 |
|---|---|---|
| XtData 行情 | 49 / 49 | 全覆盖 |
| XtTrade 交易 | 38 / 38 | 全覆盖 |
| 大QMT HTTP server | 10 端点 | 全覆盖 |

---

## 与现有工具对比

| | qmt-bridge | xtquant | easytrader | vnpy |
|---|---|---|---|---|
| 大QMT 支持 | 是 | 否 | 否 | 否 |
| miniQMT 支持 | 是 | 是 | 否 | 否 |
| 自动双模切换 | 是 | 否 | 否 | 否 |
| CLI | 是 | 否 | 否 | 否 |
| 行情 49 方法 | 是 | 是 | 否 | 部分 |
| Polars 输出 | 是 | 否 | 否 | 否 |
| 类型化接口 | 是 | 否 | 否 | 部分 |
| 无窗口依赖 | 是 | 是 | 否 | 是 |

---

## 项目结构

```
qmt_bridge/
├── bridge.py          # QMTBridge 统一路由（auto/daqmt/miniqmt）
├── daqmt_bridge.py    # 大QMT HTTP client
├── xtquant_bridge.py  # miniQMT xtquant SDK client（38 方法）
├── xtdata_bridge.py   # xtdata 行情（49 方法，Polars 输出）
└── cli.py             # CLI（typer + rich）
deploy/
└── daqmt_server.py    # 大QMT 策略环境 Tornado server
tests/
└── test_bridge.py     # 45 tests（mock HTTP + mock xtquant）
```

---

## 许可证

[Apache 2.0](LICENSE) -- 可用于商业闭源项目。
