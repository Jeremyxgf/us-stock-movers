#!/usr/bin/env python3
"""Cache five-year OHLCV for every listed US stock, for same-origin Pages reads.

Generated data/daily files live in the Actions cache and Pages artifact, not Git.
Only Python's standard library is required. Failed refreshes retain valid history.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

DATA_DIR = Path(__file__).resolve().parent / "data"
FIELDS = ("t", "o", "h", "l", "c", "v", "a")
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9.-]*\Z")
API = "https://stockanalysis.com/api/symbol/s/{sym}/history?range=5Y&period=Daily"


def pack_rows(payload):
    """Keep real OHLCV and adjustment factors; never synthesize daily candles."""
    by_date = {}
    for row in payload.get("data") or []:
        if not isinstance(row, dict) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("t", ""))):
            continue
        values = [row.get(k) for k in FIELDS[1:]]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in values[:5]):
            continue
        o, h, l, c, v, a = values
        if min(o, h, l, c) <= 0 or v < 0 or h < max(o, l, c) or l > min(o, c):
            continue
        if not isinstance(a, (int, float)) or not math.isfinite(a) or a <= 0:
            a = c
        by_date[row["t"]] = [row["t"], o, h, l, c, v, a]
    return [by_date[d] for d in sorted(by_date)][-1260:]


def read_cache(path, sym):
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("symbol") != sym or cached.get("fields") != list(FIELDS):
            return None
        rows = cached.get("data") or []
        unpacked = {"data": [dict(zip(FIELDS, row)) for row in rows]}
        if rows and pack_rows(unpacked) == rows and cached.get("asOf") == rows[-1][0]:
            return cached
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def fetch_history(sym):
    aliases = [sym, sym.replace(".", "-")] if "." in sym else [sym]
    for alias in aliases:
        url = API.format(sym=urllib.parse.quote(alias, safe=""))
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; StockWeb-daily/1.0)",
                    "Accept": "application/json",
                })
                with urllib.request.urlopen(req, timeout=30) as response:
                    rows = pack_rows(json.load(response))
                if rows:
                    return rows
                break
            except (urllib.error.URLError, TimeoutError, OSError, ValueError, TypeError, AttributeError):
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{sym}: no valid OHLCV history returned")


def write_json(path, payload):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def refresh(sym, latest, out_dir):
    if not SYMBOL.fullmatch(sym):
        raise ValueError(f"Invalid symbol: {sym!r}")
    path = out_dir / f"{sym}.json"
    cached = read_cache(path, sym)
    if cached and cached["asOf"] >= latest:
        return "cached", sym
    try:
        rows = fetch_history(sym)
        # An upstream regression must not replace a newer valid cache.
        if cached and rows[-1][0] < cached["asOf"]:
            return "retained", sym
        write_json(path, {"symbol": sym, "asOf": rows[-1][0], "fields": FIELDS, "data": rows})
        time.sleep(0.25)
        return "updated", sym
    except Exception as error:
        print(f"  {error}; {'retaining previous history' if cached else 'unavailable'}", flush=True)
        return ("retained" if cached else "missing"), sym


def build(symbols=None, workers=4, data_dir=DATA_DIR):
    manifest = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
    latest = max(manifest["dates"] if isinstance(manifest, dict) else manifest)
    snapshot = json.loads((data_dir / f"{latest}.json").read_text(encoding="utf-8"))
    universe = sorted({row["s"] for row in snapshot if SYMBOL.fullmatch(row.get("s", ""))})
    selected = sorted(set(symbols)) if symbols else universe
    out_dir = data_dir / "daily"
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {key: 0 for key in ("cached", "updated", "retained", "missing")}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = [pool.submit(refresh, sym, latest, out_dir) for sym in selected]
        for i, future in enumerate(as_completed(pending), 1):
            state, _ = future.result()
            counts[state] += 1
            if i % 100 == 0 or i == len(selected):
                print(f"  {i}/{len(selected)}: {counts}", flush=True)
    usable = sum(counts[k] for k in ("cached", "updated", "retained"))
    if not selected or usable / len(selected) < 0.95:
        raise RuntimeError(f"Only {usable}/{len(selected)} symbols have usable history; refusing to publish below 95% coverage")
    # Only a full-universe run marks a cache ready for subsequent push deployments.
    if symbols is None:
        write_json(out_dir / "manifest.json", {
            "asOf": latest, "universe": len(universe), "counts": counts,
            "updatedAt": datetime.now(timezone.utc).isoformat(),
        })
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", help="Refresh selected symbols for a local smoke check")
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    args = parser.parse_args()
    build(args.symbols, args.workers)
