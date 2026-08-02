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
    meta = {r["s"]: {"n": r.get("n"), "price": r.get("price")} for r in picked}
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
    """把分钟 bar 按美东交易日分组，返回 [(date, RV_日%, bk大单占比), ...] 升序。

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
    by_day = defaultdict(list)   # date -> [(epoch, close, vol)]
    for t, c, v in zip(ts, closes, vols):
        if c is None:
            continue
        d = datetime.fromtimestamp(t, timezone.utc).astimezone(EASTERN).date()
        by_day[d].append((t, c, v or 0))
    out = []
    for d in sorted(by_day):
        rows = sorted(by_day[d])
        seq = [c for _, c, _ in rows]
        if len(seq) < 3:            # bar 太少不可靠
            continue
        rv2 = 0.0
        for i in range(1, len(seq)):
            if seq[i - 1] > 0 and seq[i] > 0:
                rv2 += math.log(seq[i] / seq[i - 1]) ** 2
        bar_vols = sorted(v for _, _, v in rows if v > 0)
        if bar_vols:
            med = bar_vols[len(bar_vols) // 2]
            tot = sum(bar_vols)
            big = sum(v for _, _, v in rows if v >= 3 * med)
            bk = big / tot if tot > 0 else None
        else:
            bk = None
        out.append((d.isoformat(), math.sqrt(rv2), bk))
    return out


def rms(vals):
    """多日波动率的合并：波动率不能直接算术平均，可加的是方差 → 先平方取均值再开根。"""
    return round(math.sqrt(sum(v * v for v in vals) / len(vals)), 3)


def build():
    symbols, meta, latest = pick_universe()
    print(f"股票池 {len(symbols)} 只（按成交额），基准交易日 {latest}")
    stocks = {}
    ok = 0
    for i, sym in enumerate(symbols, 1):
        payload = fetch_chart(sym)
        rv = daily_realized_vol(payload) if payload else []
        if rv:
            series = [round(v * 100, 3) for _, v, _ in rv]   # 日 %
            bks = [round(b, 3) if b is not None else None for _, _, b in rv]
            iv1d = series[-1]
            iv5d = rms(series[-5:])
            iv1m = rms(series)
            stocks[sym] = {
                "n": meta[sym]["n"], "price": meta[sym]["price"],
                "iv1d": iv1d, "iv5d": iv5d, "iv1m": iv1m,
                "rv": series[-22:],          # 迷你走势用
                "bk": bks[-22:],             # 大 bar 成交占比（机构大单代理），与 rv 同窗
                "dates": [d for d, _, _ in rv][-22:],
                "days": len(series),
            }
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


if __name__ == "__main__":
    build()
