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
DETAIL_DIR = os.path.join(DATA_DIR, "intraday")   # 每股逐 bar 明细（个股页热力图按需取）
DETAIL_DAYS = 22        # 明细保留的交易日数
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
    """新浪 5 分钟每根 {day,open,high,low,close,volume}；按交易日分组，返回逐日 dict：

        {"d": "2026-07-31", "rv": 0.0071, "bk": 0.28,
         "bars": [(时段"09:35", 对数收益绝对值|None, 成交量), ...]}

    bars 是构成 rv 的每一根明细（个股页热力图用，一天的 bars 平方和开根 = 当天 rv）。
    """
    by_day = defaultdict(list)   # date -> [(时段, close, vol)]
    for bar in klines:
        try:
            day = bar["day"]; close = float(bar["close"]); vol = float(bar["volume"])
        except (KeyError, ValueError, TypeError):
            continue
        by_day[day[:10]].append((day[11:16], close, vol))
    out = []
    for d in sorted(by_day):
        seq = by_day[d]
        if len(seq) < 3:
            continue
        closes = [c for _, c, _ in seq]
        rv2 = 0.0
        bars = [(seq[0][0], None, seq[0][2])]      # 当天第一根无前收，不含隔夜跳空
        for i in range(1, len(closes)):
            r = None
            if closes[i - 1] > 0 and closes[i] > 0:
                r = abs(math.log(closes[i] / closes[i - 1]))
                rv2 += r * r
            bars.append((seq[i][0], r, seq[i][2]))
        vols = sorted(v for _, _, v in seq if v > 0)
        bk = None
        if vols:
            med = vols[len(vols) // 2]; tot = sum(vols)
            big = sum(v for _, _, v in seq if v >= 3 * med)
            bk = big / tot if tot > 0 else None
        out.append({"d": d, "rv": math.sqrt(rv2), "bk": bk, "bars": bars})
    return out


def rms(vals):
    """多日波动率的合并：波动率不能直接算术平均，可加的是方差 → 先平方取均值再开根。"""
    return round(math.sqrt(sum(v * v for v in vals) / len(vals)), 3)


def write_detail(code, name, rv, latest, flt):
    """每只股票单独写逐 bar 明细，供个股页热力图按需取（排名页不加载它）。

    bar 算 rv 时已在内存里，这里只是不再丢弃。r 存基点整数（|对数收益|×10000），
    null = 当天第一根；vt 为当日总成交量。
    t 存「占流通盘的百万分之几」整数（成交量/流通股×1e6），即 5 分钟换手率。
    A 股 5 分钟量合计 == 日成交量，故逐格 t 求和 == 当日换手率（已与快照 turnover 字段对账）。A 股午休时段天然不出现在 slots 里，前端据间隔画分隔线。
    """
    days = rv[-DETAIL_DAYS:]
    slots = sorted({s for day in days for s, _, _ in day["bars"]})
    idx = {s: i for i, s in enumerate(slots)}
    out_days = []
    for day in days:
        r = [None] * len(slots)
        t = [None] * len(slots)
        for s, rr, vv in day["bars"]:
            k = idx[s]
            if rr is not None:
                r[k] = round(rr * 10000)
            if flt > 0 and vv > 0:
                t[k] = round(vv / flt * 1e6)
        # 逐 bar 成交量不存：实测会让明细体积涨 3 倍，而热力图只需要波动率。
        # 只留当日总量（tooltip 用），一个整数几乎不占地方。
        out_days.append({"d": day["d"], "rv": round(day["rv"] * 100, 3),
                         "vt": int(sum(v for _, _, v in day["bars"])), "r": r, "t": t})
    payload = {"s": code, "n": name, "asOf": latest, "interval": "5m", "tz": "Asia/Shanghai",
               "flt": int(flt),
               "slots": slots, "days": out_days}
    with open(os.path.join(DETAIL_DIR, f"{code}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def build():
    rows, latest = latest_snapshot()
    os.makedirs(DETAIL_DIR, exist_ok=True)
    picked = rows[:UNIVERSE]
    print(f"活跃池 {len(picked)} 只（快照 {latest}）")
    stocks, ok = {}, 0
    for i, r in enumerate(picked, 1):
        kl = fetch_klines(sina_sym(r["s"], r.get("mkt", "0")))
        rv = daily_rv(kl) if kl else []
        if rv:
            series = [round(d["rv"] * 100, 3) for d in rv]
            bks = [round(d["bk"], 3) if d["bk"] is not None else None for d in rv]
            stocks[r["s"]] = {
                "n": r.get("n"), "price": r.get("price"),
                "iv1d": series[-1],
                "iv5d": rms(series[-5:]),
                "iv1m": rms(series),
                "rv": series[-22:], "bk": bks[-22:],
                "dates": [d["d"] for d in rv][-22:], "days": len(series),
            }
            # A 股流通股数 = 流通市值 / 现价
            flt = (r.get("floatCap") or 0) / r["price"] if r.get("price") else 0
            write_detail(r["s"], r.get("n"), rv, latest, flt)
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
    stale = 0
    for fn in os.listdir(DETAIL_DIR):
        if fn.endswith(".json") and fn[:-5] not in stocks:
            os.remove(os.path.join(DETAIL_DIR, fn)); stale += 1
    mb = sum(os.path.getsize(os.path.join(DETAIL_DIR, f)) for f in os.listdir(DETAIL_DIR)) / 1024 / 1024
    print(f"逐 bar 明细：{DETAIL_DIR} 共 {len(stocks)} 个文件 {mb:.1f} MB（清理过期 {stale} 个）")


if __name__ == "__main__":
    build()
