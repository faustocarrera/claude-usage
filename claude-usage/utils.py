import argparse
from datetime import datetime
from pathlib import Path

def parse_date(s: str):
    return datetime.strptime(s, "%Y%m%d").date()


def build_parser(description=None, default_path=None):
    if default_path is None:
        default_path = Path("~/.claude/projects").expanduser()
        
    parser = argparse.ArgumentParser(prog="claude-usage", description=description)
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("daily", "weekly", "monthly"):
        p = sub.add_parser(name, help=f"{name} usage report")
        p.add_argument("--json", action="store_true", help="output JSON")
        p.add_argument("--since", type=parse_date, help="start date (YYYYMMDD)")
        p.add_argument("--until", type=parse_date, help="end date (YYYYMMDD)")
        p.add_argument(
            "--path", type=Path, default=default_path, help="Claude projects directory"
        )
        p.add_argument(
            "--refresh-pricing",
            action="store_true",
            help="force re-download of the LiteLLM pricing cache",
        )

    return parser
