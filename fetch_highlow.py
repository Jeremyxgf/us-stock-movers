#!/usr/bin/env python3
"""
每天收盘后抓一次「创新高/新低」基础数据，写 data/highlow.json，供 highlow.html 排名展示。

思路：
  - 与日内页同款股票池：按成交额取最活跃前 UNIVERSE 只（全市场逐只拉日线太重）。
  - 每只从 StockAnalysis 的按需接口拉 5Y 日线 OHLC（一次请求），
    用复权收盘 a 校正 h/l/c（避免拆股制造假高低点），保留最近 KEEP_DAYS 个交易日。
  - 网页端根据用户选的窗口 W 周（1-52，交易日 = 5W 天）在浏览器里现算：
      创新高（严格收盘口径）= 今收 == 窗口内最高收盘
      距盘中最高/最低% = 今收 / 窗口内盘中极值 - 1
      区间涨跌% = 今收 / 窗口起点收盘 - 1
    所以本脚本只存原始数组，任意窗口都能秒算。

为什么存 261 天：52 周 × 5 交易日 = 260，区间涨跌还要再往前 1 天作基准。
只用 Python 标准库。
"""

import json
import math
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from collections import Counter

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
UNIVERSE = 500          # 股票池大小（按成交额取最活跃前 N，与日内页一致）
KEEP_DAYS = 261         # 52周×5交易日 + 1 天区间基准
RETRIES = 3
SLEEP_BETWEEN = 0.25
API = "https://stockanalysis.com/api/symbol/s/{sym}/history?range=5Y&period=Daily"


def pick_universe():
    with open(os.path.join(DATA_DIR, "manifest.json"), encoding="utf-8") as f:
        m = json.load(f)
    dates = sorted(m["dates"] if isinstance(m, dict) else list(m))
    latest = dates[-1]
    with open(os.path.join(DATA_DIR, f"{latest}.json"), encoding="utf-8") as f:
        rows = json.load(f)
    rows = [r for r in rows if r.get("s") and (r.get("dollarVolume") or 0) > 0]
    rows.sort(key=lambda r: r.get("dollarVolume") or 0, reverse=True)
    picked = rows[:UNIVERSE]
    meta = {r["s"]: {"n": r.get("n"), "mc": r.get("marketCap")} for r in picked}
    return [r["s"] for r in picked], meta, latest


def fetch_history(symbol: str):
    def hit(s):
        url = API.format(sym=urllib.parse.quote(s))
        for attempt in range(1, RETRIES + 1):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; StockWeb-highlow/1.0)",
                    "Accept": "application/json",
                })
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
                if attempt == RETRIES:
                    print(f"  {symbol} 抓取失败：{err}", file=sys.stderr)
                    return None
                time.sleep(attempt * 1.5)
    payload = hit(symbol)
    if not (payload and (payload.get("data") or [])) and "." in symbol:
        payload = hit(symbol.replace(".", "-"))
    return payload


def extract_series(payload):
    """返回 (last_date, dates[], closes[], highs[], lows[])，升序、复权、保留最近 KEEP_DAYS 天。"""
    rows = (payload or {}).get("data") or []
    rows = sorted(rows, key=lambda r: r.get("t") or "")[-KEEP_DAYS:]
    dates, closes, highs, lows = [], [], [], []
    last_date = None
    for r in rows:
        c, h, l, a = r.get("c"), r.get("h"), r.get("l"), r.get("a")
        if not (isinstance(c, (int, float)) and c > 0):
            continue
        # 复权因子：用复权收盘/未复权收盘，同步校正当天的高低价
        f = (a / c) if (isinstance(a, (int, float)) and a > 0) else 1.0
        closes.append(round(c * f, 4))
        highs.append(round((h if isinstance(h, (int, float)) and h > 0 else c) * f, 4))
        lows.append(round((l if isinstance(l, (int, float)) and l > 0 else c) * f, 4))
        dates.append(r.get("t"))
        last_date = r.get("t")
    return last_date, dates, closes, highs, lows


def build():
    symbols, meta, latest = pick_universe()
    print(f"股票池 {len(symbols)} 只（按成交额），基准交易日 {latest}")
    stocks = {}
    last_dates = Counter()
    calendar = []            # 全局交易日历（取首个满 KEEP_DAYS 天的股票的日期序列）
    for i, sym in enumerate(symbols, 1):
        payload = fetch_history(sym)
        last_date, dts, c, h, l = extract_series(payload)
        if len(c) >= 6:                      # 起码够 1 周窗口
            stocks[sym] = {"n": meta[sym]["n"], "mc": meta[sym]["mc"],
                           "d": last_date, "c": c, "h": h, "l": l}
            last_dates[last_date] += 1
            if not calendar and len(dts) == KEEP_DAYS:
                calendar = dts
        if i % 50 == 0:
            print(f"  进度 {i}/{len(symbols)}，成功 {len(stocks)}")
        time.sleep(SLEEP_BETWEEN)

    # 以众数 last_date 为快照日；剔除日线明显落后的（停牌/退市中）
    as_of = last_dates.most_common(1)[0][0] if last_dates else latest
    stale = [s for s, v in stocks.items() if v["d"] != as_of]
    for s in stale:
        del stocks[s]
    for v in stocks.values():
        del v["d"]

    out = {
        "asOf": as_of,
        "universe": len(stocks),
        "keepDays": KEEP_DAYS,
        "dates": calendar,       # 与各股数组尾部对齐的交易日历（升序），供网页换算窗口起始日
        "stocks": stocks,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = os.path.join(DATA_DIR, "highlow.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(path) / 1024
    print(f"已写入 {path}（{len(stocks)} 只，剔除日线落后 {len(stale)} 只，{size_kb:.0f} KB）")


if __name__ == "__main__":
    build()
