#!/usr/bin/env python3
"""
ccusage-py: analyze Claude Code token usage/cost from local JSONL logs.

Reads ~/.claude/projects/**/*.jsonl, aggregates token usage by day/week/month,
and prices it using a locally-cached copy of LiteLLM's model price table.

Usage:
    python ccusage.py daily   [--since YYYYMMDD] [--until YYYYMMDD] [--json]
    python ccusage.py weekly  [--since YYYYMMDD] [--until YYYYMMDD] [--json]
    python ccusage.py monthly [--since YYYYMMDD] [--until YYYYMMDD] [--json]

Options common to all subcommands:
    --path PATH          Override the Claude projects directory
                         (default: ~/.claude/projects)
    --refresh-pricing    Force re-download of the pricing cache
"""

import argparse
import glob
import hashlib
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Pricing cache
# ---------------------------------------------------------------------------

PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
CACHE_DIR = os.path.expanduser(os.path.dirname(__file__) + "/../.cache")
CACHE_FILE = os.path.join(CACHE_DIR, "model_prices.json")
CACHE_MAX_AGE_SECONDS = 24 * 60 * 60  # 24 hours


def _cache_is_fresh(path: str) -> bool:
    if not os.path.exists(path):
        return False
    age = datetime.now(timezone.utc).timestamp() - os.path.getmtime(path)
    return age < CACHE_MAX_AGE_SECONDS


def load_pricing(force_refresh: bool = False) -> dict:
    """Return {model_name: {input_cost_per_token, output_cost_per_token,
    cache_creation_input_token_cost, cache_read_input_token_cost}}.

    Falls back to the stale cache (or an empty dict) if the network is
    unavailable, so the tool still works offline.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)

    if force_refresh or not _cache_is_fresh(CACHE_FILE):
        try:
            req = urllib.request.Request(
                PRICING_URL, headers={"User-Agent": "ccusage-py"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            with open(CACHE_FILE, "wb") as f:
                f.write(data)
        except Exception as e:
            if os.path.exists(CACHE_FILE):
                print(
                    f"[warn] pricing refresh failed ({e}); using cached copy",
                    file=sys.stderr,
                )
            else:
                print(
                    f"[warn] pricing fetch failed ({e}); costs will show as $0",
                    file=sys.stderr,
                )
                return {}

    with open(CACHE_FILE, "r") as f:
        raw = json.load(f)

    # LiteLLM's file is {model_name: {...pricing fields..., "litellm_provider": ...}}
    # plus a "sample_spec" key we don't need.
    pricing = {}
    for model, info in raw.items():
        if not isinstance(info, dict):
            continue
        pricing[model] = {
            "input_cost_per_token": info.get("input_cost_per_token", 0) or 0,
            "output_cost_per_token": info.get("output_cost_per_token", 0) or 0,
            "cache_creation_input_token_cost": info.get(
                "cache_creation_input_token_cost", 0
            )
            or 0,
            "cache_read_input_token_cost": info.get(
                "cache_read_input_token_cost", 0
            )
            or 0,
        }
    return pricing


def price_for_model(pricing: dict, model: str) -> dict:
    """LiteLLM keys models like 'claude-sonnet-4-6' or with provider prefixes
    like 'anthropic/claude-sonnet-4-6'. Try a few lookups before giving up."""
    if model in pricing:
        return pricing[model]
    candidates = [
        model,
        f"anthropic/{model}",
        model.replace("anthropic/", ""),
    ]
    for c in candidates:
        if c in pricing:
            return pricing[c]
    # loose suffix match as a last resort
    for key in pricing:
        if key.endswith(model) or model.endswith(key):
            return pricing[key]
    return {
        "input_cost_per_token": 0,
        "output_cost_per_token": 0,
        "cache_creation_input_token_cost": 0,
        "cache_read_input_token_cost": 0,
    }


# ---------------------------------------------------------------------------
# JSONL parsing
# ---------------------------------------------------------------------------

DEFAULT_CLAUDE_DIR = os.path.expanduser("~/.claude/projects")


def _record_key(entry: dict, line: str) -> str:
    """A stable dedup key. Claude Code sometimes writes the same message
    into more than one file, so we key on message id + requestId when
    present, and fall back to a hash of the line."""
    msg = entry.get("message", {}) or {}
    msg_id = msg.get("id")
    request_id = entry.get("requestId")
    if msg_id or request_id:
        return f"{msg_id}:{request_id}"
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def iter_usage_records(claude_dir: str):
    """Yield dicts: {timestamp (datetime), model, input_tokens, output_tokens,
    cache_creation_tokens, cache_read_tokens} for every assistant message
    with usage data, deduped across files."""
    pattern = os.path.join(claude_dir, "**", "*.jsonl")
    files = sorted(glob.glob(pattern, recursive=True))

    seen = set()
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if entry.get("type") != "assistant":
                        continue
                    msg = entry.get("message", {}) or {}
                    usage = msg.get("usage")
                    if not usage:
                        continue

                    key = _record_key(entry, line)
                    if key in seen:
                        continue
                    seen.add(key)

                    ts_raw = entry.get("timestamp")
                    try:
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    except (AttributeError, ValueError):
                        continue

                    yield {
                        "timestamp": ts,
                        "model": msg.get("model", "unknown"),
                        "input_tokens": usage.get("input_tokens", 0) or 0,
                        "output_tokens": usage.get("output_tokens", 0) or 0,
                        "cache_creation_tokens": usage.get(
                            "cache_creation_input_tokens", 0
                        )
                        or 0,
                        "cache_read_tokens": usage.get(
                            "cache_read_input_tokens", 0
                        )
                        or 0,
                    }
        except OSError:
            continue


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def period_key(ts: datetime, period: str) -> str:
    if period == "daily":
        return ts.strftime("%Y-%m-%d")
    if period == "weekly":
        iso_year, iso_week, _ = ts.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if period == "monthly":
        return ts.strftime("%Y-%m")
    raise ValueError(period)


def aggregate(records, pricing: dict, period: str, since=None, until=None):
    groups = defaultdict(
        lambda: {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "cost": 0.0,
            "models": set(),
        }
    )

    for r in records:
        day = r["timestamp"].date()
        if since and day < since:
            continue
        if until and day > until:
            continue

        key = period_key(r["timestamp"], period)
        g = groups[key]
        g["input_tokens"] += r["input_tokens"]
        g["output_tokens"] += r["output_tokens"]
        g["cache_creation_tokens"] += r["cache_creation_tokens"]
        g["cache_read_tokens"] += r["cache_read_tokens"]
        g["models"].add(r["model"])

        p = price_for_model(pricing, r["model"])
        g["cost"] += (
            r["input_tokens"] * p["input_cost_per_token"]
            + r["output_tokens"] * p["output_cost_per_token"]
            + r["cache_creation_tokens"] * p["cache_creation_input_token_cost"]
            + r["cache_read_tokens"] * p["cache_read_input_token_cost"]
        )

    return dict(sorted(groups.items()))


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def print_table(groups: dict, period_label: str):
    if not groups:
        print("No usage data found.")
        return

    headers = [
        period_label,
        "Input",
        "Output",
        "Cache Create",
        "Cache Read",
        "Cost (USD)",
        "Models",
    ]
    rows = []
    total = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "cost": 0.0,
    }
    for key, g in groups.items():
        rows.append(
            [
                key,
                f"{g['input_tokens']:,}",
                f"{g['output_tokens']:,}",
                f"{g['cache_creation_tokens']:,}",
                f"{g['cache_read_tokens']:,}",
                f"${g['cost']:.2f}",
                ", ".join(sorted(g["models"])),
            ]
        )
        for k in ("input_tokens", "output_tokens", "cache_creation_tokens",
                   "cache_read_tokens", "cost"):
            total[k] += g[k]

    rows.append(
        [
            "TOTAL",
            f"{total['input_tokens']:,}",
            f"{total['output_tokens']:,}",
            f"{total['cache_creation_tokens']:,}",
            f"{total['cache_read_tokens']:,}",
            f"${total['cost']:.2f}",
            "",
        ]
    )

    widths = [
        max(len(str(row[i])) for row in ([headers] + rows))
        for i in range(len(headers))
    ]

    def fmt_row(row):
        return " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(row))

    sep = "-+-".join("-" * w for w in widths)
    print(fmt_row(headers))
    print(sep)
    for i, row in enumerate(rows):
        if row[0] == "TOTAL":
            print(sep)
        print(fmt_row(row))


def groups_to_json(groups: dict) -> str:
    out = {}
    for key, g in groups.items():
        out[key] = {
            "input_tokens": g["input_tokens"],
            "output_tokens": g["output_tokens"],
            "cache_creation_tokens": g["cache_creation_tokens"],
            "cache_read_tokens": g["cache_read_tokens"],
            "cost_usd": round(g["cost"], 4),
            "models": sorted(g["models"]),
        }
    return json.dumps(out, indent=2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_date(s: str):
    return datetime.strptime(s, "%Y%m%d").date()


def build_parser():
    parser = argparse.ArgumentParser(prog="ccusage.py", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("daily", "weekly", "monthly"):
        p = sub.add_parser(name, help=f"{name} usage report")
        p.add_argument("--since", type=parse_date, help="YYYYMMDD, inclusive")
        p.add_argument("--until", type=parse_date, help="YYYYMMDD, inclusive")
        p.add_argument("--json", action="store_true", help="output JSON")
        p.add_argument(
            "--path", default=DEFAULT_CLAUDE_DIR, help="Claude projects directory"
        )
        p.add_argument(
            "--refresh-pricing",
            action="store_true",
            help="force re-download of the LiteLLM pricing cache",
        )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    pricing = load_pricing(force_refresh=args.refresh_pricing)
    records = iter_usage_records(args.path)
    groups = aggregate(
        records, pricing, args.command, since=args.since, until=args.until
    )

    if args.json:
        print(groups_to_json(groups))
    else:
        label = {"daily": "Date", "weekly": "Week", "monthly": "Month"}[
            args.command
        ]
        print_table(groups, label)


if __name__ == "__main__":
    main()
