# 美股涨跌幅动态榜单（每日快照版）

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
| `.github/workflows/daily.yml` | GitHub Actions，每个交易日定时跑脚本并自动提交数据。 |
| `data/manifest.json` | 可用交易日清单，网页据此限定可选日期。 |
| `data/<日期>.json` | 当天全市场快照（对象数组）。 |

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
2. **Settings → Pages**：Source 选 `Deploy from a branch`，分支选 `main`、目录 `/ (root)`，保存。
3. **Settings → Actions → General → Workflow permissions**：选 `Read and write permissions`（让定时任务能提交数据）。
4. 之后 `.github/workflows/daily.yml` 会在每个交易日（22:30 UTC，美股收盘后）自动抓取并提交；
   也可在 **Actions** 页点 `Daily market snapshot → Run workflow` 手动触发一次。
5. 访问 `https://<用户名>.github.io/<仓库名>/`（首页就是 `index.html`）。

## 数据列说明
代码、公司名、涨跌幅、收盘价、成交额、**换手率（= 成交量 ÷ 流通股 float）**、市值——
全部来自 StockAnalysis 原生字段，历史每一天都完整。仅供参考，不构成投资建议。

## 定时任务时间
`daily.yml` 用 `cron: "30 22 * * 1-5"`（UTC）。美股收盘是 20:00（夏令时）/ 21:00（冬令时）UTC，
22:30 UTC 留了 1.5–2.5 小时让「最近交易日」结算。GitHub 定时可能延迟几分钟，不影响。
脚本以数据里的 `priceDate` 为准命名文件，不依赖运行时刻的日期。
