# 美股涨跌幅动态榜单（每日快照版）

线上地址：<https://jeremyxgf.github.io/us-stock-movers/>

一个纯静态网页 + 每日抓取脚本。每个交易日把 StockAnalysis 全市场快照存成本地 JSON，
网页离线读取、在浏览器里按 Top 数量与最低市值排出涨幅榜 / 跌幅榜。

**无需 API key、无跨域问题、完全免费。**

## 为什么是这个架构
StockAnalysis 的接口只返回「最近交易日」，没法查历史。所以我们换思路：
**每天跑一次脚本把当天快照存下来，自己逐日积累历史。** 历史从开始采集那天起往后攒，
更早的日期无法补采。

## 文件
| 文件 | 作用 |
|---|---|
| `index.html` | 网页。读 `data/` 下的快照，本地过滤排名。 |
| `fetch_snapshot.py` | 抓取脚本。翻完全市场（约 5500+ 只）写入 `data/<日期>.json` 并更新 `data/manifest.json`。仅用 Python 标准库。 |
| `.github/workflows/daily.yml` | GitHub Actions，每个交易日定时跑脚本、提交数据并部署 Pages。 |
| `data/manifest.json` | 可用交易日清单，网页据此限定可选日期。 |
| `data/<日期>.json` | 当天全市场快照（对象数组）。 |
| `data/history.json` | 由脚本预计算：每股近 20 日收盘价（迷你走势图）+ 5/10/30 日年化波动率 + 全市场波动率指数。 |
| `intraday.html` / `fetch_intraday.py` / `data/intraday.json` | 日内波动率排名页：最活跃 500 只的 Yahoo 5 分钟 K 线 → 每日已实现日内波动率，按 1D/5D/1M 平均排名。 |
| `highlow.html` / `fetch_highlow.py` / `data/highlow.json` | 创新高/新低榜：最活跃 500 只的复权日线（261 交易日），窗口 1–52 周、严格收盘口径，按区间幅度排名。 |

## 本地使用
浏览器有同源限制，直接双击打开 `index.html` 读不到本地 JSON，要起一个静态服务器：
```bash
cd StockWeb
python3 -m http.server 8765
# 浏览器打开 http://localhost:8765/
```
手动抓一次当天数据：
```bash
python3 fetch_snapshot.py
```

## 部署到 GitHub（云端自动 + 在线访问）
1. 把本目录推到一个 GitHub 仓库。
2. **Settings → Pages**：Source 选 **`GitHub Actions`**（不是 `Deploy from a branch`）。
   这样每次工作流跑完都会**显式部署**，不再依赖 GitHub 偶发的隐式构建（那会漏部署）。
3. **Settings → Actions → General → Workflow permissions**：选 `Read and write permissions`（让定时任务能提交数据）。
4. 之后 `.github/workflows/daily.yml` 会在每个交易日（22:30 UTC，美股收盘后）自动抓取数据、提交，并**紧接着部署 Pages**；
   也可在 **Actions** 页点 `Daily snapshot & deploy → Run workflow` 手动触发一次。
   （另外，凡是改了 `index.html` 等网页文件并 push 到 `main`，也会自动重新部署，无需重新抓数据。）
5. 访问 `https://<用户名>.github.io/<仓库名>/`（首页就是 `index.html`）。

## 数据列说明
代码、公司名、涨跌幅、收盘价、成交额、**换手率（= 成交量 ÷ 流通股 float）**、市值——
全部来自 StockAnalysis 原生字段，历史每一天都完整。仅供参考，不构成投资建议。

## 迷你走势图 + K 线 + 波动率
- **近20日迷你走势图**：读 `data/history.json` 里每股近 20 交易日收盘价画内嵌 SVG 折线（涨绿跌红），选历史日期时按该日期截断。
- **K 线弹窗**：点击迷你走势图，用 [lightweight-charts](https://github.com/tradingview/lightweight-charts)（CDN）按需从 StockAnalysis 的
  `api/symbol/s/<代码>/history?range=…&period=Daily`（开放 CORS，返回 OHLCV）画完整蜡烛图 + 成交量，可切 1M/6M/1Y/5Y。
- **波动率**：`fetch_snapshot.py` 预计算，列里显示 0–100「波动率指数」，可排序，悬浮看 5/10/30 日年化%。

### 波动率算法
- **历史波动率（默认，本项目采用）**：对数日收益 `r_t = ln(P_t / P_{t-1})`，取窗口样本标准差年化：`σ = std(r, N日) × √252`（N=5/10/30）。
- **波动率指数**：全市场 30 日年化波动率（不足回退 10 日）的横截面百分位（0–100），跨股可比。
- 这是**已实现/历史**波动（向后看）；真正的 VIX 是**隐含**波动（从期权反推、向前看），需期权链数据，本项目不涉及。
- 想更精确可扩展：EWMA/RiskMetrics（λ=0.94）、Parkinson / Garman–Klass / Yang–Zhang（需 OHLC）、ATR%、对 SPY 的 Beta。
- 口径提示：波动率与指数均为**截至最新交易日**，浏览历史排名日时该两列仍显示最新值。

## 定时任务时间
`daily.yml` 用 `cron: "30 22 * * 1-5"`（UTC）。美股收盘是 20:00（夏令时）/ 21:00（冬令时）UTC，
22:30 UTC 留了 1.5–2.5 小时让「最近交易日」结算。GitHub 定时可能延迟几分钟，不影响。
脚本以数据里的 `priceDate` 为准命名文件，不依赖运行时刻的日期。
