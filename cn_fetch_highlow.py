#!/usr/bin/env python3
"""
A 股「创新高/新低 + 反转」基础数据：读 data/cn 最新快照的活跃池，逐股取腾讯前复权日线，
保留最近 KEEP_DAYS 交易日的 c/h/l，写 data/cn/highlow.json。对应美股 fetch_highlow.py。

数据源：腾讯 web.ifzq.gtimg.cn 前复权日 K（抗封，单股一次请求）。
供 cn-highlow.html / cn-reversal.html / cn-sectors.html 使用。只用 Python 标准库。
"""

import http.client
import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import Counter

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cn")
UNIVERSE = 500            # 活跃股池（快照已按成交额降序，取前 N）
KEEP_DAYS = 261
RETRIES = 4
KLINE = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={sym},day,,,300,qfq"


def latest_snapshot():
    with open(os.path.join(DATA_DIR, "manifest.json"), encoding="utf-8") as f:
        m = json.load(f)
    latest = sorted(m["dates"] if isinstance(m, dict) else list(m))[-1]
    with open(os.path.join(DATA_DIR, f"{latest}.json"), encoding="utf-8") as f:
        rows = json.load(f)
    return rows, latest


def tsym(code: str, mkt: str) -> str:
    return ("sh" if mkt == "1" else "sz") + code


def fetch_kline(sym: str):
    url = KLINE.format(sym=sym)
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                d = json.loads(resp.read().decode("utf-8", "ignore"))
            node = (d.get("data") or {}).get(sym) or {}
            kl = node.get("qfqday") or node.get("day") or []
            return kl
        except (urllib.error.URLError, urllib.error.HTTPError, http.client.HTTPException,
                ConnectionError, TimeoutError, OSError, json.JSONDecodeError) as err:
            if attempt == RETRIES:
                print(f"  {sym} 失败：{err}", file=sys.stderr)
                return []
            time.sleep(attempt * 1.5)
    return []


def extract(kl: list):
    """腾讯每根 [日期,开,收,高,低,量]；返回 (dates, closes, highs, lows) 升序、保留最近 KEEP_DAYS。"""
    kl = kl[-KEEP_DAYS:]
    ds, c, h, l = [], [], [], []
    for row in kl:
        try:
            ds.append(row[0]); c.append(round(float(row[2]), 3))
            h.append(round(float(row[3]), 3)); l.append(round(float(row[4]), 3))
        except (IndexError, ValueError, TypeError):
            continue
    return ds, c, h, l


def build():
    rows, latest = latest_snapshot()
    picked = rows[:UNIVERSE]
    print(f"活跃池 {len(picked)} 只（快照 {latest}）")
    stocks, last_dates, calendar = {}, Counter(), []
    for i, r in enumerate(picked, 1):
        code, mkt = r["s"], r.get("mkt", "0")
        ds, c, h, l = extract(fetch_kline(tsym(code, mkt)))
        if len(c) >= 6:
            stocks[code] = {"n": r.get("n"), "mc": r.get("marketCap"),
                            "d": ds[-1], "c": c, "h": h, "l": l}
            last_dates[ds[-1]] += 1
            if not calendar and len(ds) == KEEP_DAYS:
                calendar = ds
        if i % 50 == 0:
            print(f"  进度 {i}/{len(picked)}，成功 {len(stocks)}")
        time.sleep(0.15)

    as_of = last_dates.most_common(1)[0][0] if last_dates else latest
    stale = [s for s, v in stocks.items() if v["d"] != as_of]
    for s in stale:
        del stocks[s]
    for v in stocks.values():
        del v["d"]

    out = {"asOf": as_of, "universe": len(stocks), "keepDays": KEEP_DAYS,
           "dates": calendar, "stocks": stocks,
           "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = os.path.join(DATA_DIR, "highlow.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"已写入 {path}（{len(stocks)} 只，剔除日线落后 {len(stale)}，{os.path.getsize(path)/1024:.0f} KB）")


if __name__ == "__main__":
    build()
