#!/usr/bin/env python3
"""
Hyperliquid Top-Trader Consensus Engine
----------------------------------------
Fetches the Hyperliquid leaderboard, ranks traders by PnL or ROI across
several time windows, pulls each top trader's open positions, and aggregates
them into "consensus trades": which coins the top traders hold in common,
the long/short split, the size-weighted average entry price, and total
notional exposure. Also diffs against the previous snapshot.

Output: data.json  (consumed by the dashboard)

Usage:
    python engine.py                # default: top 50, all windows/metrics
    python engine.py --top 100      # change cohort size
"""

import argparse
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
INFO_URL = "https://api.hyperliquid.xyz/info"

WINDOWS = ["day", "week", "month", "allTime"]
METRICS = ["pnl", "roi"]


def http_get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "hl-consensus/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def http_post(url, payload, timeout=30):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json",
                                 "User-Agent": "hl-consensus/1.0"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def perf(row, window, metric):
    """Pull a performance number out of a leaderboard row, safely."""
    for w, d in row.get("windowPerformances", []):
        if w == window:
            try:
                return float(d[metric])
            except (KeyError, TypeError, ValueError):
                return 0.0
    return 0.0


def fetch_leaderboard():
    print("Fetching leaderboard (~31MB)...", flush=True)
    rows = http_get(LEADERBOARD_URL)["leaderboardRows"]
    print(f"  {len(rows)} traders on the leaderboard.", flush=True)
    return rows


def rank(rows, metric, window, top_n):
    """Return the top_n addresses ranked by metric over window."""
    ranked = sorted(rows, key=lambda r: perf(r, window, metric), reverse=True)
    out = []
    for r in ranked[:top_n]:
        out.append({
            "address": r["ethAddress"],
            "accountValue": float(r.get("accountValue", 0) or 0),
            "pnl": perf(r, window, "pnl"),
            "roi": perf(r, window, "roi"),
            "vlm": perf(r, window, "vlm"),
        })
    return out


def fetch_positions(address):
    """Return list of open positions for one trader."""
    try:
        d = http_post(INFO_URL, {"type": "clearinghouseState", "user": address})
    except Exception as e:
        print(f"  ! failed {address}: {e}", flush=True)
        return address, 0.0, []
    acct = float(d.get("marginSummary", {}).get("accountValue", 0) or 0)
    positions = []
    for ap in d.get("assetPositions", []):
        p = ap.get("position", {})
        szi = float(p.get("szi", 0) or 0)
        if szi == 0:
            continue
        positions.append({
            "coin": p.get("coin"),
            "szi": szi,                                   # signed size (+long / -short)
            "entryPx": float(p.get("entryPx", 0) or 0),
            "positionValue": float(p.get("positionValue", 0) or 0),
            "unrealizedPnl": float(p.get("unrealizedPnl", 0) or 0),
            "leverage": (p.get("leverage") or {}).get("value"),
        })
    return address, acct, positions


def fetch_all_positions(addresses, workers=10):
    print(f"Fetching positions for {len(addresses)} traders...", flush=True)
    result = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fetch_positions, a): a for a in addresses}
        for f in as_completed(futs):
            addr, acct, pos = f.result()
            result[addr] = {"accountValue": acct, "positions": pos}
    return result


def aggregate(addresses, pos_by_addr):
    """Build consensus view for one cohort of addresses."""
    coins = {}
    for addr in addresses:
        rec = pos_by_addr.get(addr)
        if not rec:
            continue
        for p in rec["positions"]:
            coin = p["coin"]
            c = coins.setdefault(coin, {
                "coin": coin,
                "holders": 0, "longs": 0, "shorts": 0,
                "_size_abs": 0.0, "_entry_notional": 0.0,
                "totalNotional": 0.0, "netUnrealizedPnl": 0.0,
                "traders": [],
            })
            c["holders"] += 1
            if p["szi"] > 0:
                c["longs"] += 1
            else:
                c["shorts"] += 1
            abs_sz = abs(p["szi"])
            c["_size_abs"] += abs_sz
            c["_entry_notional"] += abs_sz * p["entryPx"]   # for size-weighted avg entry
            c["totalNotional"] += p["positionValue"]
            c["netUnrealizedPnl"] += p["unrealizedPnl"]
            c["traders"].append({
                "address": addr,
                "side": "long" if p["szi"] > 0 else "short",
                "entryPx": p["entryPx"],
                "szi": p["szi"],
                "notional": p["positionValue"],
                "leverage": p["leverage"],
            })

    out = []
    for c in coins.values():
        avg_entry = c["_entry_notional"] / c["_size_abs"] if c["_size_abs"] else 0.0
        long_notional = sum(t["notional"] for t in c["traders"] if t["side"] == "long")
        short_notional = sum(t["notional"] for t in c["traders"] if t["side"] == "short")
        out.append({
            "coin": c["coin"],
            "holders": c["holders"],
            "longs": c["longs"],
            "shorts": c["shorts"],
            "consensus": "long" if c["longs"] > c["shorts"] else ("short" if c["shorts"] > c["longs"] else "split"),
            "avgEntry": round(avg_entry, 6),
            "totalNotional": round(c["totalNotional"], 2),
            "longNotional": round(long_notional, 2),
            "shortNotional": round(short_notional, 2),
            "netUnrealizedPnl": round(c["netUnrealizedPnl"], 2),
            "traders": sorted(c["traders"], key=lambda t: abs(t["notional"]), reverse=True),
        })
    out.sort(key=lambda x: x["holders"], reverse=True)
    return out


def compute_changes(prev_list, new_list):
    """Diff a cohort's previous consensus against the new one."""
    prev = {c["coin"]: c for c in prev_list}
    new = {c["coin"]: c for c in new_list}
    added, dropped, shifts = [], [], []
    for coin, n in new.items():
        if coin not in prev:
            added.append({"coin": coin, "holders": n["holders"], "consensus": n["consensus"]})
            continue
        p = prev[coin]
        hd = n["holders"] - p["holders"]
        flip = p["consensus"] != n["consensus"]
        drift = ((n["avgEntry"] - p["avgEntry"]) / p["avgEntry"] * 100) if p["avgEntry"] else 0.0
        if hd != 0 or flip:
            shifts.append({
                "coin": coin, "holdersDelta": hd,
                "from": p["consensus"], "to": n["consensus"], "flip": flip,
                "entryDriftPct": round(drift, 2),
            })
    for coin, p in prev.items():
        if coin not in new:
            dropped.append({"coin": coin, "holders": p["holders"], "consensus": p["consensus"]})
    added.sort(key=lambda x: x["holders"], reverse=True)
    dropped.sort(key=lambda x: x["holders"], reverse=True)
    shifts.sort(key=lambda x: abs(x["holdersDelta"]), reverse=True)
    return {"added": added, "dropped": dropped, "shifts": shifts}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=50, help="cohort size (default 50)")
    ap.add_argument("--out", default="data.json")
    args = ap.parse_args()

    rows = fetch_leaderboard()

    # Build ranked cohorts for every metric/window combo.
    cohorts = {}
    all_addresses = set()
    for metric in METRICS:
        for window in WINDOWS:
            ranked = rank(rows, metric, window, args.top)
            cohorts[f"{metric}_{window}"] = ranked
            all_addresses.update(r["address"] for r in ranked)

    # One position fetch per unique address (reused across cohorts).
    pos_by_addr = fetch_all_positions(sorted(all_addresses))

    # Aggregate consensus per cohort.
    consensus = {}
    for key, ranked in cohorts.items():
        addrs = [r["address"] for r in ranked]
        consensus[key] = aggregate(addrs, pos_by_addr)

    # Diff against the previous snapshot (if one exists) for the "what changed" view.
    prev_consensus, prev_at = {}, None
    try:
        with open(args.out) as f:
            prev = json.load(f)
        prev_consensus = prev.get("consensus", {})
        prev_at = prev.get("generatedAt")
    except (FileNotFoundError, ValueError):
        pass
    changes = {key: compute_changes(prev_consensus.get(key, []), consensus[key])
               for key in consensus}

    out = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "previousAt": prev_at,
        "topN": args.top,
        "metrics": METRICS,
        "windows": WINDOWS,
        "cohorts": cohorts,            # ranked trader lists (for live client refresh)
        "consensus": consensus,        # precomputed aggregation snapshot
        "changes": changes,            # diff vs previous snapshot, per cohort
    }
    with open(args.out, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"Wrote {args.out} ({len(json.dumps(out))/1024:.0f} KB)", flush=True)


if __name__ == "__main__":
    main()
