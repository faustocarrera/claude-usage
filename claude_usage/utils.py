import argparse
from datetime import datetime
from pathlib import Path

def parse_date(s: str):
    return datetime.strptime(s, "%Y%m%d").date()


def build_parser(description=None, default_path=None):
    if default_path is None:
        default_path = Path("~/.claude/projects").expanduser()
        
    parser = argparse.ArgumentParser(prog="claude_usage", description=description)
    parser.add_argument(
        "command",
        nargs="?",
        choices=["daily", "weekly", "monthly", "report"],
        default="daily",
        help="usage report type (default: daily); 'report' breaks usage down per session",
    )
    parser.add_argument("--json", action="store_true", help="output JSON")
    parser.add_argument(
        "--markdown", action="store_true", help="output markdown (report only)"
    )
    parser.add_argument("--since", type=parse_date, help="start date (YYYYMMDD)")
    parser.add_argument("--until", type=parse_date, help="end date (YYYYMMDD)")
    parser.add_argument(
        "--path", type=Path, default=default_path, help="Claude projects directory"
    )
    parser.add_argument(
        "--refresh-pricing",
        action="store_true",
        help="force re-download of the LiteLLM pricing cache",
    )

    return parser
