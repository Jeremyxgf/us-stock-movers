#!/usr/bin/env python3
"""
A 股日内波动率：读 data/cn 活跃池，逐股取东财 5 分钟 K，算每交易日已实现日内波动率
RV=√(Σ5分钟对数收益²) 与大单占比 bk，写 data/cn/intraday.json。对应美股 fetch_intraday.py。
多日口径 iv5d/iv1m 用 RV 的均方根（RMS）而非算术平均：可加的是方差。均为「日 %」，不年化。

数据源：新浪 5 分钟 K（getKLineData scale=5，抗封）。只用 Python 标准库。
"""

import http.client
import json
import math
import os
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cn")
UNIVERSE = 500
RETRIES = 4
DATALEN = 1200            # 约一个月的 5 分钟 bar（48/天 × ~25）
KLINE = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/x=/CN_MarketDataService.getKLineData"
         "?symbol={sym}&scale=5&ma=no&datalen={n}")


def latest_snapshot():
    with open(os.path.join(DATA_DIR, "manifest.json"), encoding="utf-8") as f:
        m = json.load(f)
    latest = sorted(m["dates"] if isinstance(m, dict) else list(m))[-1]
    with open(os.path.join(DATA_DIR, f"{latest}.json"), encoding="utf-8") as f:
        return json.load(f), latest


def sina_sym(code, mkt):
    return ("sh" if mkt == "1" else "sz") + code


def fetch_klines(sym):
    url = KLINE.format(sym=sym, n=DATALEN)
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode("utf-8", "ignore")
            s = raw[raw.find("(") + 1:raw.rfind(")")]
            return json.loads(s) if s.strip() else []
        except (urllib.error.URLError, urllib.error.HTTPError, http.client.HTTPException,
                ConnectionError, TimeoutError, OSError, json.JSONDecodeError, ValueError) as err:
            if attempt == RETRIES:
                return None
            time.sleep(attempt * 1.5)
    return None


def daily_rv(klines):
    """新浪 5 分钟每根 {day,open,high,low,close,volume}；按交易日分组算 RV(日%) 与 bk 大单占比。"""
    by_day = defaultdict(list)   # date -> [(close, vol)]
    for bar in klines:
        try:
            dt = bar["day"][:10]; close = float(bar["close"]); vol = float(bar["volume"])
        except (KeyError, ValueError, TypeError):
            continue
        by_day[dt].append((close, vol))
    out = []
    for d in sorted(by_day):
        seq = by_day[d]
        if len(seq) < 3:
            continue
        closes = [c for c, _ in seq]
        rv2 = sum(math.log(closes[i] / closes[i - 1]) ** 2
                  for i in range(1, len(closes))
                  if closes[i - 1] > 0 and closes[i] > 0)
        vols = sorted(v for _, v in seq if v > 0)
        bk = None
        if vols:
            med = vols[len(vols) // 2]; tot = sum(vols)
            big = sum(v for _, v in seq if v >= 3 * med)
            bk = big / tot if tot > 0 else None
        out.append((d, math.sqrt(rv2), bk))
    return out


def rms(vals):
    """多日波动率的合并：波动率不能直接算术平均，可加的是方差 → 先平方取均值再开根。"""
    return round(math.sqrt(sum(v * v for v in vals) / len(vals)), 3)


def build():
    rows, latest = latest_snapshot()
    picked = rows[:UNIVERSE]
    print(f"活跃池 {len(picked)} 只（快照 {latest}）")
    stocks, ok = {}, 0
    for i, r in enumerate(picked, 1):
        kl = fetch_klines(sina_sym(r["s"], r.get("mkt", "0")))
        rv = daily_rv(kl) if kl else []
        if rv:
            series = [round(v * 100, 3) for _, v, _ in rv]
            bks = [round(b, 3) if b is not None else None for _, _, b in rv]
            stocks[r["s"]] = {
                "n": r.get("n"), "price": r.get("price"),
                "iv1d": series[-1],
                "iv5d": rms(series[-5:]),
                "iv1m": rms(series),
                "rv": series[-22:], "bk": bks[-22:],
                "dates": [d for d, _, _ in rv][-22:], "days": len(series),
            }
            ok += 1
        if i % 50 == 0:
            print(f"  进度 {i}/{len(picked)}，成功 {ok}")
        time.sleep(0.12)
    out = {"asOf": latest, "universe": len(stocks), "interval": "5m",
           "stocks": stocks, "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    path = os.path.join(DATA_DIR, "intraday.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"已写入 {path}（{ok}/{len(picked)} 只，{os.path.getsize(path)/1024:.0f} KB）")


if __name__ == "__main__":
    build()
