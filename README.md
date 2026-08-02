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
| `sectors.html` / `fetch_sectors.py` / `data/sectors.json` | 板块/行业相关性 3D 星系图：市值加权组合日收益的 Pearson 相关（1M/3M/6M/1Y），红=正相关、绿=负相关，可拖拽。 |
| `stock.html` | 个股详情页（点击榜单公司名新标签页打开）：12 个月换手衰减筹码分布 + 机构/散户分层推断（量价行为 + 5 分钟大单集中度代理），外加 **5 分钟波动率热力图 + 时段剖面**（交易日 × 时段，可切近5/10/22日与波动率/成交量；点行下钻到当日剖面，含同时段中位与 P25–P75 分位带）。 |
| `reversal.html` | 反转榜（复用 highlow.json）：低位反弹（X 天前处窗口底部 20% 且近 X 日涨 ≥ Y%）/ 高位回调（顶部 20% 且近 X 日跌 ≥ Y%），X/Y 可调，默认 3 天 / 10%。 |

### A 股版（`cn-*` 前缀，数据在 `data/cn/`）
数据源改用**新浪/腾讯**（东财 clist 批量翻页会封 IP）。抓取在每日 08:00 UTC（北京 16:00，A 股收盘后）的 CI 定时段跑，与美股共用一套 Actions + Pages。

| 文件 | 作用 |
|---|---|
| `cn-index.html` / `cn_fetch_snapshot.py` | A股涨跌幅榜：新浪活跃股池约 1000 只，¥ 货币、换手率原生、**涨跌停封板标记**、K 线弹窗用东财日 K。 |
| `cn-highlow.html` / `cn-reversal.html` / `cn_fetch_highlow.py` | 创新高低 + 反转：腾讯前复权日线 498 只 261 交易日 → `data/cn/highlow.json`。 |
| `cn-stock.html` | 个股详情：腾讯前复权日 K + 新浪快照流通市值算流通股，机构/散户推断（可另参考龙虎榜/北向）；同样带 **5 分钟波动率热力图**（午休时段自动压缩并画分隔线；涨跌停封板期呈现为一整片浅色「死区」）。 |
| `cn-intraday.html` / `cn_fetch_intraday.py` | 日内波动率：新浪 5 分钟 K，活跃 500 只，1D/5D/1M 排名。 |

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
- **日内已实现波动率**（`intraday.html` / `stock.html` 热力图）：`RV_d = √(Σ ln(cᵢ/cᵢ₋₁)²)`，5 分钟收盘价、仅日内相邻（不含隔夜跳空）。
  多日合并用**均方根 RMS** `√(Σ RV²/N)` 而非算术平均——可加的是方差。不年化，均以「日 %」表示。
  个股页的热力图用东财 5 分钟 K 浏览器按需直取（CORS 开放，不进管线、不占仓库体积），一行的各格平方和开根即该日 RV。

## 定时任务时间
`daily.yml` 用 `cron: "30 22 * * 1-5"`（UTC）。美股收盘是 20:00（夏令时）/ 21:00（冬令时）UTC，
22:30 UTC 留了 1.5–2.5 小时让「最近交易日」结算。GitHub 定时可能延迟几分钟，不影响。
脚本以数据里的 `priceDate` 为准命名文件，不依赖运行时刻的日期。
