#!/usr/bin/env python3
"""
每天收盘后计算「板块/行业相关性」矩阵，写 data/sectors.json，供 sectors.html 3D 星系图展示。

思路：
  - 从 StockAnalysis screener 拉全市场 sector/industry 映射（与每日快照同源，约 6 页）。
  - 读 data/highlow.json（最活跃 500 只、261 个交易日复权收盘 + 市值），
    每只算日对数收益，按 GICS 板块 / 行业（成分股 >= MIN_GROUP 只）聚合成
    市值加权组合日收益（与板块 ETF 口径一致）。
  - 对 1M/3M/6M/1Y（21/63/126/250 交易日）窗口分别算两两 Pearson 相关系数。

依赖 fetch_highlow.py 的输出，需在其之后运行。只用 Python 标准库。
"""

import json
import math
import os
import sys
import time
import urllib.request
import urllib.error

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
API = "https://stockanalysis.com/_api/endpoints/screener/table"
PAGE_SIZE = 1000
MAX_PAGES = 12
RETRIES = 4
MIN_GROUP = 5            # 行业级：成分股不足此数的行业不单列
WINDOWS = {"1M": 21, "3M": 63, "6M": 126, "1Y": 250}

# GICS 板块英文 -> 中文
SECTOR_CN = {
    "Technology": "科技",
    "Financials": "金融",
    "Healthcare": "医疗健康",
    "Consumer Discretionary": "可选消费",
    "Consumer Staples": "必需消费",
    "Industrials": "工业",
    "Energy": "能源",
    "Utilities": "公用事业",
    "Materials": "原材料",
    "Real Estate": "房地产",
    "Communication Services": "通信服务",
}


def fetch_json(url: str) -> dict:
    last_err = None
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; StockWeb-sectors/1.0)",
                "Accept": "application/json",
            })
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as err:
            last_err = err
            time.sleep(attempt * 2)
    raise RuntimeError(f"多次重试仍失败：{url}\n{last_err}")


def fetch_classification() -> dict:
    """全市场 symbol -> (sector, industry)。"""
    mapping = {}
    for page in range(1, MAX_PAGES + 1):
        url = (f"{API}?type=s&m=change&s=desc&c=s,sector,industry&cn={PAGE_SIZE}"
               f"&f=priceDate-isLastTradingDay&p={page}&i=stock-movers")
        payload = fetch_json(url)
        rows = ((payload or {}).get("data") or {}).get("data") or []
        if not rows:
            break
        for r in rows:
            sym = r.get("s")
            if sym and sym not in mapping:
                mapping[sym] = (r.get("sector"), r.get("industry"))
        print(f"  分类第 {page} 页：累计 {len(mapping)} 只")
        results = ((payload or {}).get("data") or {}).get("resultsCount")
        if results and len(mapping) >= results:
            break
        time.sleep(0.6)
    return mapping


def log_returns(closes: list) -> list:
    return [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes))
            if closes[i - 1] > 0 and closes[i] > 0]


def pearson(x: list, y: list) -> float | None:
    n = min(len(x), len(y))
    if n < 3:
        return None
    x, y = x[-n:], y[-n:]
    mx, my = sum(x) / n, sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


def group_series(members: list, stocks: dict) -> tuple:
    """市值加权组合日收益（尾部对齐）；返回 (收益序列, 成分数, 总市值)。

    注意：不能对齐到最短成分（一只新股会把整组截成几十天，长窗口全部退化）。
    改为逐日聚合：每个交易日（从最新往回数第 k 天）只纳入历史足够长的成分股，
    近期日子全员参与、越久远参与的股票越少。
    """
    rets, weights, total_mc = [], [], 0.0
    for sym in members:
        v = stocks[sym]
        r = log_returns(v["c"])
        if len(r) < 20:
            continue
        w = float(v.get("mc") or 0) or 1.0
        rets.append(r)
        weights.append(w)
        total_mc += float(v.get("mc") or 0)
    if not rets:
        return None, 0, 0.0
    n = max(len(r) for r in rets)          # 尾部对齐到最长
    series_rev = []
    for k in range(1, n + 1):              # k=1 是最新一天
        acc = wsum = 0.0
        for w, r in zip(weights, rets):
            if len(r) >= k:
                acc += w * r[-k]
                wsum += w
        if wsum <= 0:
            break
        series_rev.append(acc / wsum)
    return series_rev[::-1], len(rets), total_mc


def build_level(groups: dict, stocks: dict, cn_map: dict) -> dict:
    """groups: 组名 -> [symbols]。返回 {labels, meta, corr}。"""
    series, meta = {}, {}
    for name in sorted(groups):
        s, count, mc = group_series(groups[name], stocks)
        if s is None or count < 2:
            continue
        series[name] = s
        meta[name] = {"count": count, "mc": mc, "cn": cn_map.get(name, name)}
    labels = list(series)
    corr = {}
    for wname, days in WINDOWS.items():
        m = []
        for a in labels:
            row = []
            for b in labels:
                if a == b:
                    row.append(1.0)
                else:
                    v = pearson(series[a][-days:], series[b][-days:])
                    row.append(round(v, 3) if v is not None else None)
            m.append(row)
        corr[wname] = m
    return {"labels": labels, "meta": meta, "corr": corr}


def build():
    with open(os.path.join(DATA_DIR, "highlow.json"), encoding="utf-8") as f:
        hl = json.load(f)
    stocks = hl["stocks"]
    print(f"收益池 {len(stocks)} 只（来自 highlow.json，基准日 {hl['asOf']}）")

    print("拉取全市场板块/行业分类…")
    cls = fetch_classification()

    sector_groups, industry_groups = {}, {}
    unknown = 0
    for sym in stocks:
        sec, ind = cls.get(sym, (None, None))
        if sec:
            sector_groups.setdefault(sec, []).append(sym)
        else:
            unknown += 1
        if ind:
            industry_groups.setdefault(ind, []).append(sym)
    industry_groups = {k: v for k, v in industry_groups.items() if len(v) >= MIN_GROUP}
    print(f"板块 {len(sector_groups)} 个；行业(≥{MIN_GROUP}只) {len(industry_groups)} 个；无分类 {unknown} 只")

    out = {
        "asOf": hl["asOf"],
        "windows": list(WINDOWS),
        "sector": build_level(sector_groups, stocks, SECTOR_CN),
        "industry": build_level(industry_groups, stocks, {}),
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = os.path.join(DATA_DIR, "sectors.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(path) / 1024
    print(f"已写入 {path}（板块 {len(out['sector']['labels'])} / 行业 {len(out['industry']['labels'])}，{size_kb:.0f} KB）")


if __name__ == "__main__":
    build()
