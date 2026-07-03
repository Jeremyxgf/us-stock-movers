#!/usr/bin/env python3
"""
每日抓取 StockAnalysis 全市场最近交易日快照，按交易日存成 data/<date>.json，
并维护 data/manifest.json（可用日期清单）。供静态网页 index 5.html 离线查询。

只用 Python 标准库，GitHub Actions 无需 pip install。

数据源是 StockAnalysis 的 screener 接口，只返回“最近交易日”——所以本脚本每天跑一次，
把当天快照存下来，历史就从开始采集那天起逐日积累（无法补采过去的日期）。
"""

import bisect
import json
import math
import os
import sys
import time
import urllib.request
import urllib.error
from collections import Counter

API = "https://stockanalysis.com/_api/endpoints/screener/table"
COLUMNS = "no,s,n,change,priceDate,price,volume,dollarVolume,float,sharesOut,marketCap"
PAGE_SIZE = 1000
MAX_PAGES = 12          # 安全上限；当前全市场约 5600 只 ≈ 6 页
RETRIES = 4

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
# 想保留的字段（写进每日 JSON 的每一行）
ROW_FIELDS = ["s", "n", "change", "price", "volume", "dollarVolume",
              "float", "sharesOut", "marketCap"]

# history.json（网页迷你走势图 + 波动率）相关
SPARK_DAYS = 20         # 迷你走势图回看的交易日数
VOL_MAX_DAYS = 31       # 算 30 日波动率需要 31 个收盘价
TRADING_DAYS = 252      # 年化因子
VOL_WINDOWS = (5, 10, 30)


def build_url(page: int) -> str:
    params = (
        f"?type=s&m=change&s=desc&c={COLUMNS}&cn={PAGE_SIZE}"
        f"&f=priceDate-isLastTradingDay&p={page}&i=stock-movers"
    )
    return API + params


def fetch_json(url: str) -> dict:
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; StockWeb-snapshot/1.0)",
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
            last_err = err
            wait = attempt * 2
            print(f"  第 {attempt} 次请求失败：{err}，{wait}s 后重试", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"多次重试仍失败：{url}\n{last_err}")


def fetch_all_rows() -> list:
    rows = []
    seen = set()
    results_count = None
    for page in range(1, MAX_PAGES + 1):
        payload = fetch_json(build_url(page))
        data = (payload or {}).get("data") or {}
        page_rows = data.get("data") or []
        if results_count is None:
            results_count = data.get("resultsCount")
        for r in page_rows:
            sym = r.get("s")
            if sym and sym not in seen:
                seen.add(sym)
                rows.append(r)
        print(f"  第 {page} 页：本页 {len(page_rows)} 行，累计 {len(rows)} / {results_count}")
        if not page_rows:
            break
        if results_count and len(rows) >= results_count:
            break
        time.sleep(0.6)  # 礼貌性间隔，避免给数据源压力
    return rows


def pick_trade_date(rows: list) -> str:
    """以出现最多的 priceDate 作为该快照的交易日（绝大多数行应相同）。"""
    dates = [r.get("priceDate") for r in rows if r.get("priceDate")]
    if not dates:
        raise RuntimeError("返回数据里没有 priceDate，无法确定交易日")
    return Counter(dates).most_common(1)[0][0]


def slim(rows: list) -> list:
    """只保留网页需要的字段，缩小体积。"""
    out = []
    for r in rows:
        out.append({k: r.get(k) for k in ROW_FIELDS})
    return out


def update_manifest(date: str) -> list:
    path = os.path.join(DATA_DIR, "manifest.json")
    dates = []
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                existing = json.load(f)
            dates = existing.get("dates", []) if isinstance(existing, dict) else list(existing)
        except (json.JSONDecodeError, OSError):
            dates = []
    if date not in dates:
        dates.append(date)
    dates = sorted(set(dates))
    manifest = {
        "dates": dates,
        "latest": dates[-1],
        "count": len(dates),
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return dates


def _read_manifest_dates() -> list:
    """读 manifest.json 里升序的交易日清单（供 build_history 独立调用）。"""
    path = os.path.join(DATA_DIR, "manifest.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    dates = m.get("dates", []) if isinstance(m, dict) else list(m)
    return sorted(set(dates))


def _annualized_vol(returns: list, n: int) -> float | None:
    """最近 n 个对数收益的样本标准差，年化。收益不足 2 个则返回 None。"""
    r = returns[-n:] if len(returns) >= n else returns
    if len(r) < 2:
        return None
    mean = sum(r) / len(r)
    var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def build_history() -> None:
    """
    从最近的每日快照预计算 data/history.json：
      - 每只股票近 SPARK_DAYS 个交易日的收盘价（供网页画迷你走势图）
      - 5/10/30 日年化历史波动率（对数收益标准差 × √252）
      - 全市场「波动率指数」：各股 vol30（不足回退 vol10）的横截面百分位（0-100）
    只读磁盘上已有的 data/<date>.json，不联网；stdlib only。
    """
    all_dates = _read_manifest_dates()
    if not all_dates:
        print("没有 manifest，跳过 history.json。", file=sys.stderr)
        return

    window_dates = all_dates[-VOL_MAX_DAYS:]     # 算波动率用（至多 31 天）
    spark_dates = all_dates[-SPARK_DAYS:]        # 迷你走势图用（至多 20 天）

    # 逐日读收盘价：{date: {sym: close}}
    day_close = {}
    for d in window_dates:
        p = os.path.join(DATA_DIR, f"{d}.json")
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                rows = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        close = {}
        for r in rows:
            sym = r.get("s")
            price = r.get("price")
            if sym and isinstance(price, (int, float)) and price > 0:
                close[sym] = float(price)
        day_close[d] = close

    # 只收录近 SPARK_DAYS 内出现过的股票
    symbols = set()
    for d in spark_dates:
        symbols.update(day_close.get(d, {}).keys())

    stocks = {}
    base_for_index = {}   # 排名用的基准波动率（vol30 优先，回退 vol10）
    for sym in symbols:
        # 按日期升序的连续收盘价（去掉缺失日），用于算收益
        series = [day_close[d][sym] for d in window_dates
                  if sym in day_close.get(d, {})]
        returns = [math.log(series[i] / series[i - 1])
                   for i in range(1, len(series))]
        vols = {f"vol{n}": _annualized_vol(returns, n) for n in VOL_WINDOWS}

        # 迷你走势图：与 spark_dates 对齐（缺失填 null）
        spark = [round(day_close[d][sym], 4) if sym in day_close.get(d, {}) else None
                 for d in spark_dates]
        if not any(v is not None for v in spark):
            continue

        entry = {"c": spark}
        for k, v in vols.items():
            entry[k] = round(v, 4) if v is not None else None
        stocks[sym] = entry
        base_for_index[sym] = vols["vol30"] if vols["vol30"] is not None else vols["vol10"]

    # 波动率指数：基准波动率在全市场的百分位（严格小于者的占比 → 0-100）
    ordered = sorted(v for v in base_for_index.values() if v is not None)
    n = len(ordered)
    for sym, entry in stocks.items():
        base = base_for_index.get(sym)
        if base is None or n <= 1:
            entry["volIndex"] = None
        else:
            entry["volIndex"] = round(bisect.bisect_left(ordered, base) / (n - 1) * 100)

    out = {
        "dates": spark_dates,
        "stocks": stocks,
        "asOf": all_dates[-1],
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = os.path.join(DATA_DIR, "history.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(path) / 1024
    print(f"已写入 {path}（{len(stocks)} 只，近 {len(spark_dates)} 日，波动率指数覆盖 {n} 只，{size_kb:.0f} KB）")


def main() -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    print("抓取 StockAnalysis 全市场快照…")
    rows = fetch_all_rows()
    if not rows:
        print("没有抓到任何数据，放弃写入。", file=sys.stderr)
        return 1

    date = pick_trade_date(rows)
    out_path = os.path.join(DATA_DIR, f"{date}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(slim(rows), f, ensure_ascii=False, separators=(",", ":"))

    dates = update_manifest(date)
    size_kb = os.path.getsize(out_path) / 1024
    print(f"已写入 {out_path}（{len(rows)} 行，{size_kb:.0f} KB）")
    print(f"manifest 现有 {len(dates)} 个交易日，最新 {dates[-1]}")

    # 预计算迷你走势图 + 波动率
    build_history()
    return 0


if __name__ == "__main__":
    sys.exit(main())
