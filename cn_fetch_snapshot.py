#!/usr/bin/env python3
"""
每日抓取 A 股「活跃股池」最近交易日快照，按交易日存 data/cn/<date>.json，
并维护 data/cn/manifest.json + data/cn/history.json（迷你走势图 + 波动率）。
A 股版，对应美股 fetch_snapshot.py。只用 Python 标准库。

数据源：新浪 Market_Center.getHQNodeData —— 沪深A股按成交额降序分页，
一个源即含 代码/名称/价格/涨跌%/开高低/量额/换手率/总市值/流通市值。
（东财 clist 批量翻页会封 IP，故不用；新浪对分页访问容忍度高。）
"""

import bisect
import http.client
import json
import math
import os
import sys
import time
import urllib.request
import urllib.error

SINA = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
PAGE_NUM = 80              # 新浪每页条数
UNIVERSE_MAX = 1000       # 活跃股池上限（按成交额取前 N）
RETRIES = 5

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "cn")
ROW_FIELDS = ["s", "n", "change", "price", "volume", "dollarVolume",
              "turnover", "marketCap", "floatCap", "mkt", "limit"]

SPARK_DAYS = 20
VOL_MAX_DAYS = 31
TRADING_DAYS = 244        # A 股一年约 244 个交易日
VOL_WINDOWS = (5, 10, 30)


def fetch_json(url: str):
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                "Referer": "https://finance.sina.com.cn/",
                "Accept": "*/*",
            })
            with urllib.request.urlopen(req, timeout=25) as resp:
                return json.loads(resp.read().decode("utf-8", "ignore"))
        except (urllib.error.URLError, urllib.error.HTTPError, http.client.HTTPException,
                ConnectionError, TimeoutError, OSError, json.JSONDecodeError) as err:
            last_err = err
            wait = attempt * 3
            print(f"  第 {attempt} 次请求失败：{err}，{wait}s 后重试", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"多次重试仍失败：{url}\n{last_err}")


def numf(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def limit_pct(code: str, name: str) -> float:
    """涨跌停幅度%：ST/*ST 5，科创/创业板 20，北交所 30，其余主板 10。"""
    if "ST" in (name or "").upper():
        return 5.0
    if code.startswith(("688", "300", "301")):
        return 20.0
    if code.startswith(("8", "4", "920")):
        return 30.0
    return 10.0


def fetch_all_rows() -> list:
    rows = []
    seen = set()
    pages = (UNIVERSE_MAX + PAGE_NUM - 1) // PAGE_NUM
    for pg in range(1, pages + 1):
        url = f"{SINA}?page={pg}&num={PAGE_NUM}&sort=amount&asc=0&node=hs_a&_s_r_a=page"
        data = fetch_json(url) or []
        if not data:
            break
        got = 0
        for r in data:
            code = str(r.get("code") or "")
            sym = str(r.get("symbol") or "")
            price = numf(r.get("trade"))
            if not code or code in seen or price is None or price <= 0:
                continue
            seen.add(code)
            got += 1
            name = r.get("name")
            change = numf(r.get("changepercent"))
            lp = limit_pct(code, name)
            lim = 0
            if change is not None:
                if change >= lp - 0.3:
                    lim = 1
                elif change <= -(lp - 0.3):
                    lim = -1
            mc = numf(r.get("mktcap"))
            fc = numf(r.get("nmc"))
            rows.append({
                "s": code,
                "n": name,
                "change": change,
                "price": price,
                "volume": numf(r.get("volume")),            # 股
                "dollarVolume": numf(r.get("amount")),      # 元
                "turnover": numf(r.get("turnoverratio")),   # 换手率 %
                "marketCap": mc * 1e4 if mc is not None else None,   # 万元→元
                "floatCap": fc * 1e4 if fc is not None else None,
                "mkt": "1" if sym.startswith("sh") else "0",
                "limit": lim,
            })
        print(f"  第 {pg} 页：{got} 只，累计 {len(rows)}")
        if len(rows) >= UNIVERSE_MAX:
            break
        time.sleep(0.6)
    return rows[:UNIVERSE_MAX]


def pick_trade_date() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600))  # 北京时间日期


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
    manifest = {"dates": dates, "latest": dates[-1], "count": len(dates),
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    return dates


def slim(rows: list) -> list:
    return [{k: r.get(k) for k in ROW_FIELDS} for r in rows]


def _read_manifest_dates() -> list:
    path = os.path.join(DATA_DIR, "manifest.json")
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    return sorted(set(m.get("dates", []) if isinstance(m, dict) else list(m)))


def _annualized_vol(returns, n):
    r = returns[-n:] if len(returns) >= n else returns
    if len(r) < 2:
        return None
    mean = sum(r) / len(r)
    var = sum((x - mean) ** 2 for x in r) / (len(r) - 1)
    return math.sqrt(var) * math.sqrt(TRADING_DAYS)


def build_history():
    all_dates = _read_manifest_dates()
    if not all_dates:
        return
    window_dates = all_dates[-VOL_MAX_DAYS:]
    spark_dates = all_dates[-SPARK_DAYS:]
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
        day_close[d] = {r["s"]: float(r["price"]) for r in rows
                        if r.get("s") and isinstance(r.get("price"), (int, float)) and r["price"] > 0}
    symbols = set()
    for d in spark_dates:
        symbols.update(day_close.get(d, {}).keys())
    stocks, base_for_index = {}, {}
    for sym in symbols:
        series = [day_close[d][sym] for d in window_dates if sym in day_close.get(d, {})]
        returns = [math.log(series[i] / series[i - 1]) for i in range(1, len(series))]
        vols = {f"vol{n}": _annualized_vol(returns, n) for n in VOL_WINDOWS}
        spark = [round(day_close[d][sym], 3) if sym in day_close.get(d, {}) else None for d in spark_dates]
        if not any(v is not None for v in spark):
            continue
        entry = {"c": spark}
        for k, v in vols.items():
            entry[k] = round(v, 4) if v is not None else None
        stocks[sym] = entry
        base_for_index[sym] = vols["vol30"] if vols["vol30"] is not None else vols["vol10"]
    ordered = sorted(v for v in base_for_index.values() if v is not None)
    n = len(ordered)
    for sym, entry in stocks.items():
        base = base_for_index.get(sym)
        entry["volIndex"] = None if base is None or n <= 1 else round(bisect.bisect_left(ordered, base) / (n - 1) * 100)
    out = {"dates": spark_dates, "stocks": stocks, "asOf": all_dates[-1],
           "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    with open(os.path.join(DATA_DIR, "history.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"history.json：{len(stocks)} 只，指数覆盖 {n} 只")


def main() -> int:
    os.makedirs(DATA_DIR, exist_ok=True)
    print("抓取 A 股活跃股池快照（新浪）…")
    rows = fetch_all_rows()
    if not rows:
        print("没有抓到任何数据，放弃写入。", file=sys.stderr)
        return 1
    date = pick_trade_date()
    out_path = os.path.join(DATA_DIR, f"{date}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(slim(rows), f, ensure_ascii=False, separators=(",", ":"))
    dates = update_manifest(date)
    size_kb = os.path.getsize(out_path) / 1024
    up = sum(1 for r in rows if r.get("limit") == 1)
    dn = sum(1 for r in rows if r.get("limit") == -1)
    print(f"已写入 {out_path}（{len(rows)} 只，{size_kb:.0f} KB，涨停 {up} / 跌停 {dn}）")
    print(f"manifest 现有 {len(dates)} 个交易日，最新 {dates[-1]}")
    build_history()
    return 0


if __name__ == "__main__":
    sys.exit(main())
