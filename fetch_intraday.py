#!/usr/bin/env python3
"""
每天收盘后抓一次「日内波动率」数据，写 data/intraday.json，供 intraday.html 排名展示。

思路：
  - 从当天全市场快照 data/<最新交易日>.json 里，按成交额挑最活跃的前 UNIVERSE 只做股票池
    （日内交易者关心的就是流动性好的活跃股；全市场 5600 只分钟数据太重、易被限流）。
  - 每只用 Yahoo 5 分钟 K 线（interval=5m&range=1mo，一次请求覆盖约一个月）。
  - 把分钟 bar 按美东交易日分组，算每天的已实现日内波动率
    RV_d = sqrt(Σ r_i²)，r_i 为日内相邻 5 分钟收盘价的对数收益（不跨隔夜）。
  - iv1d = 最近一天 RV；iv5d = 最近 5 天、iv1m = 最近约 22 天的 RV 均方根（RMS）。
    多日合并用 RMS 而非算术平均：可加的是方差，√(Σ RV²/N) 才是这段时间的整体波动水平。
    均以「日 %」表示（不年化）。

只用 Python 标准库（urllib/json/zoneinfo）。数据源是 Yahoo 非官方免费接口，仅服务端使用。
"""

import json
import math
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DETAIL_DIR = os.path.join(DATA_DIR, "intraday")   # 每股逐 bar 明细（个股页热力图按需取）
DETAIL_DAYS = 22        # 明细保留的交易日数
UNIVERSE = 500          # 股票池大小（按成交额取最活跃前 N）
INTERVAL = "5m"
RANGE = "1mo"
RETRIES = 3
SLEEP_BETWEEN = 0.25    # 礼貌间隔，降低被限流概率
EASTERN = ZoneInfo("America/New_York")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"


def latest_snapshot_path() -> str:
    with open(os.path.join(DATA_DIR, "manifest.json"), encoding="utf-8") as f:
        m = json.load(f)
    dates = m["dates"] if isinstance(m, dict) else list(m)
    return os.path.join(DATA_DIR, f"{sorted(dates)[-1]}.json"), sorted(dates)[-1]


def pick_universe():
    path, latest = latest_snapshot_path()
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    rows = [r for r in rows if r.get("s") and (r.get("dollarVolume") or 0) > 0]
    rows.sort(key=lambda r: r.get("dollarVolume") or 0, reverse=True)
    picked = rows[:UNIVERSE]
    # flt = 流通股，用于把每根 bar 的成交量换算成换手率
    meta = {r["s"]: {"n": r.get("n"), "price": r.get("price"),
                     "flt": (r.get("float") or r.get("sharesOut") or 0)} for r in picked}
    return [r["s"] for r in picked], meta, latest


def fetch_chart(symbol: str):
    # Yahoo 用 '-' 代替 '.'（BRK.B -> BRK-B）
    y = symbol.replace(".", "-")
    url = (f"{YAHOO}{urllib.parse.quote(y)}"
           f"?interval={INTERVAL}&range={RANGE}&includePrePost=false")
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; StockWeb-intraday/1.0)",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
            if attempt == RETRIES:
                print(f"  {symbol} 抓取失败：{err}", file=sys.stderr)
                return None
            time.sleep(attempt * 1.5)
    return None


def daily_realized_vol(payload):
    """把分钟 bar 按美东交易日分组，返回逐日 dict 列表（升序）：

        {"d": "2026-07-31", "rv": 0.0742, "bk": 0.31,
         "bars": [(时段"09:35", 对数收益绝对值|None, 成交量), ...]}

    rv = √(Σ 日内相邻 5 分钟对数收益²)；bars 是构成它的每一根明细（个股页热力图用，
    一天的 bars 各项平方和开根恰好等于当天的 rv）。每天第一根无前收 → r 为 None。
    bk = 当天成交量中，落在「量 ≥ 3×当日 bar 中位量」的 5 分钟 bar 里的比例，
    作为大单/机构活动的代理指标（真实逐笔数据免费拿不到）。
    """
    try:
        res = payload["chart"]["result"][0]
        ts = res.get("timestamp") or []
        quote = res["indicators"]["quote"][0]
        closes = quote.get("close") or []
        vols = quote.get("volume") or []
    except (KeyError, IndexError, TypeError):
        return []
    by_day = defaultdict(list)   # date -> [(epoch, 时段, close, vol)]
    for t, c, v in zip(ts, closes, vols):
        if c is None:
            continue
        et = datetime.fromtimestamp(t, timezone.utc).astimezone(EASTERN)
        by_day[et.date()].append((t, et.strftime("%H:%M"), c, v or 0))
    out = []
    for d in sorted(by_day):
        rows = sorted(by_day[d])
        seq = [c for _, _, c, _ in rows]
        if len(seq) < 3:            # bar 太少不可靠
            continue
        rv2 = 0.0
        bars = [(rows[0][1], None, rows[0][3])]     # 当天第一根无前收，不含隔夜跳空
        for i in range(1, len(seq)):
            r = None
            if seq[i - 1] > 0 and seq[i] > 0:
                r = abs(math.log(seq[i] / seq[i - 1]))
                rv2 += r * r
            bars.append((rows[i][1], r, rows[i][3]))
        bar_vols = sorted(v for _, _, _, v in rows if v > 0)
        if bar_vols:
            med = bar_vols[len(bar_vols) // 2]
            tot = sum(bar_vols)
            big = sum(v for _, _, _, v in rows if v >= 3 * med)
            bk = big / tot if tot > 0 else None
        else:
            bk = None
        out.append({"d": d.isoformat(), "rv": math.sqrt(rv2), "bk": bk, "bars": bars})
    return out


def rms(vals):
    """多日波动率的合并：波动率不能直接算术平均，可加的是方差 → 先平方取均值再开根。"""
    return round(math.sqrt(sum(v * v for v in vals) / len(vals)), 3)


def write_detail(sym, name, rv, latest, flt):
    """每只股票单独写一个逐 bar 明细文件，供个股页热力图按需取（排名页不加载它）。

    bar 已经在内存里（算 rv 时用的就是它），这里只是不再丢弃。
    r 存基点整数（|对数收益|×10000），null = 当天第一根；vt 为当日总成交量。
    t 存「占流通盘的百万分之几」整数（成交量/流通股×1e6），即 5 分钟换手率——
    比直接存成交量省得多（3-4 位 vs 6-8 位），且前端要的就是这个比率。
    注意：5 分钟 bar 只覆盖盘中常规时段，合计约为官方日换手率的 ~78%
    （差额来自盘前盘后与非交易所成交），跨时段比较不受影响。
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
    payload = {
        "s": sym, "n": name, "asOf": latest, "interval": INTERVAL, "tz": str(EASTERN),
        "flt": int(flt),
        "slots": slots, "days": out_days,
    }
    with open(os.path.join(DETAIL_DIR, f"{sym}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))


def build():
    symbols, meta, latest = pick_universe()
    os.makedirs(DETAIL_DIR, exist_ok=True)
    print(f"股票池 {len(symbols)} 只（按成交额），基准交易日 {latest}")
    stocks = {}
    ok = 0
    for i, sym in enumerate(symbols, 1):
        payload = fetch_chart(sym)
        rv = daily_realized_vol(payload) if payload else []
        if rv:
            series = [round(d["rv"] * 100, 3) for d in rv]   # 日 %
            bks = [round(d["bk"], 3) if d["bk"] is not None else None for d in rv]
            iv1d = series[-1]
            iv5d = rms(series[-5:])
            iv1m = rms(series)
            stocks[sym] = {
                "n": meta[sym]["n"], "price": meta[sym]["price"],
                "iv1d": iv1d, "iv5d": iv5d, "iv1m": iv1m,
                "rv": series[-22:],          # 迷你走势用
                "bk": bks[-22:],             # 大 bar 成交占比（机构大单代理），与 rv 同窗
                "dates": [d["d"] for d in rv][-22:],
                "days": len(series),
            }
            write_detail(sym, meta[sym]["n"], rv, latest, meta[sym]["flt"])
            ok += 1
        if i % 50 == 0:
            print(f"  进度 {i}/{len(symbols)}，成功 {ok}")
        time.sleep(SLEEP_BETWEEN)

    out = {
        "asOf": latest,
        "universe": len(stocks),
        "interval": INTERVAL,
        "stocks": stocks,
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = os.path.join(DATA_DIR, "intraday.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(path) / 1024
    print(f"已写入 {path}（成功 {ok}/{len(symbols)} 只，{size_kb:.0f} KB）")

    # 池子会随成交额变动，清掉已不在池内的旧明细文件，避免目录无限膨胀
    stale = 0
    for fn in os.listdir(DETAIL_DIR):
        if fn.endswith(".json") and fn[:-5] not in stocks:
            os.remove(os.path.join(DETAIL_DIR, fn))
            stale += 1
    total_mb = sum(os.path.getsize(os.path.join(DETAIL_DIR, f))
                   for f in os.listdir(DETAIL_DIR)) / 1024 / 1024
    print(f"逐 bar 明细：{DETAIL_DIR} 共 {len(stocks)} 个文件 {total_mb:.1f} MB（清理过期 {stale} 个）")


if __name__ == "__main__":
    build()
