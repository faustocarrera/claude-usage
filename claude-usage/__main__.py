#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
claude-usage: analyze Claude Code token usage/cost from local JSONL logs.

Reads ~/.claude/projects/**/*.jsonl, aggregates token usage by day/week/month,
and prices it using a locally-cached copy of LiteLLM's model price table.

Usage:
    python claude-usage daily   [--since YYYYMMDD] [--until YYYYMMDD] [--json]
    python claude-usage weekly  [--since YYYYMMDD] [--until YYYYMMDD] [--json]
    python claude-usage monthly [--since YYYYMMDD] [--until YYYYMMDD] [--json]

Options common to all subcommands:
    --path PATH          Override the Claude projects directory
                         (default: ~/.claude/projects)
    --refresh-pricing    Force re-download of the pricing cache
"""

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from pricing_cache import PricingCache
from utils import build_parser

class ClaudeUsage:
    DEFAULT_CLAUDE_DIR = Path("~/.claude/projects").expanduser()

    def __init__(self, claude_dir=None):
        self.claude_dir = self.DEFAULT_CLAUDE_DIR if claude_dir is None else Path(claude_dir).expanduser()
        self.pricing_cache = PricingCache()

    def load_pricing(self, force_refresh: bool = False):
        self.pricing_cache.load_pricing(force_refresh=force_refresh)

    @staticmethod
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

    def iter_usage_records(self):
        """Yield dicts: {timestamp (datetime), model, input_tokens, output_tokens,
        cache_creation_tokens, cache_read_tokens} for every assistant message
        with usage data, deduped across files."""
        files = sorted(self.claude_dir.rglob("*.jsonl"))

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

                        key = self._record_key(entry, line)
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

    @staticmethod
    def period_key(ts: datetime, period: str) -> str:
        if period == "daily":
            return ts.strftime("%Y-%m-%d")
        if period == "weekly":
            iso_year, iso_week, _ = ts.isocalendar()
            return f"{iso_year}-W{iso_week:02d}"
        if period == "monthly":
            return ts.strftime("%Y-%m")
        raise ValueError(period)

    def aggregate(self, period: str, since=None, until=None):
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

        for r in self.iter_usage_records():
            day = r["timestamp"].date()
            if since and day < since:
                continue
            if until and day > until:
                continue

            key = self.period_key(r["timestamp"], period)
            g = groups[key]
            g["input_tokens"] += r["input_tokens"]
            g["output_tokens"] += r["output_tokens"]
            g["cache_creation_tokens"] += r["cache_creation_tokens"]
            g["cache_read_tokens"] += r["cache_read_tokens"]
            g["models"].add(r["model"])

            p = self.pricing_cache.price_for_model(r["model"])
            g["cost"] += (
                r["input_tokens"] * p["input_cost_per_token"]
                + r["output_tokens"] * p["output_cost_per_token"]
                + r["cache_creation_tokens"] * p["cache_creation_input_token_cost"]
                + r["cache_read_tokens"] * p["cache_read_input_token_cost"]
            )

        return dict(sorted(groups.items()))

    @staticmethod
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

    @staticmethod
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


def main():
    parser = build_parser(description=__doc__, default_path=ClaudeUsage.DEFAULT_CLAUDE_DIR)
    args = parser.parse_args()

    cusage = ClaudeUsage(claude_dir=args.path)
    cusage.load_pricing(force_refresh=args.refresh_pricing)
    groups = cusage.aggregate(
        args.command, since=getattr(args, "since", None), until=getattr(args, "until", None)
    )

    if args.json:
        print(cusage.groups_to_json(groups))
    else:
        label = {"daily": "Date", "weekly": "Week", "monthly": "Month"}[
            args.command
        ]
        cusage.print_table(groups, label)


if __name__ == "__main__":
    main()
