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
    python claude-usage report  [--since YYYYMMDD] [--until YYYYMMDD] [--json | --markdown]

Options common to all subcommands:
    --path PATH          Override the Claude projects directory
                         (default: ~/.claude/projects)
    --refresh-pricing    Force re-download of the pricing cache
"""

from claude_usage import ClaudeUsage
from session_report import SessionReport
from utils import build_parser


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = build_parser(description=__doc__, default_path=ClaudeUsage.DEFAULT_CLAUDE_DIR)
    args = parser.parse_args()

    cusage = ClaudeUsage(claude_dir=args.path)
    cusage.load_pricing(force_refresh=args.refresh_pricing)

    if args.command == "report":
        report = SessionReport(cusage)
        sessions = report.build(since=args.since, until=args.until)
        print(report.to_json(sessions) if args.json else report.render(sessions, markdown=args.markdown))
        return

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
