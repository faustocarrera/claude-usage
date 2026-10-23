"""Read the hook-written logs under .brain/logs, and the reports already
written from them."""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ACTIVITY = "activity-log.jsonl"
SESSIONS = "sessions/sessions.jsonl"
PROOF_LOG_GLOB = "*_proof-log.md"
# conversation-log.sh names each export YYYY-MM-DD_<session id>.md.
CONVERSATION_GLOB = "conversations/*_*.md"
REPORT_GLOB = "*_report_log-summary*"
# render_html.py records what an HTML report covers in these two tags.
REPORTED_HTML = re.compile(r'<meta name="log-report-(sessions|proof-logs)" content="([^"]*)">')
# The first cell of a Sessions row, linked or not: | `abcd1234` | or | [`abcd1234`](...) |
REPORTED_SESSION = re.compile(r"^\| \[?`([^`]+)`")
REPORTED_PROOF_LOG = re.compile(r"^- \[([^\]]+_proof-log\.md)\]")

ACTIVITY_KEYS = ("ts", "session", "tool", "path", "add", "del")
SESSION_KEYS = ("ts", "session")


@dataclass
class Logs:
    activity: list = field(default_factory=list)
    sessions: list = field(default_factory=list)
    proof_logs: list = field(default_factory=list)
    conversations: dict = field(default_factory=dict)
    bad_lines: list = field(default_factory=list)


def local_time(ts):
    """The hooks stamp `ts` in UTC while the proof logs speak local time, so
    every timestamp is converted to local to keep the report on one clock."""
    return datetime.fromisoformat(ts).astimezone()


def load_jsonl(path, required):
    """Return (rows, bad) where bad is a list of (path, line number, reason).

    A missing file is not an error: a fresh repo ships `logs/` holding only a
    `.gitkeep`. A malformed line is skipped rather than fatal, because these
    files are append-only and one torn write should not hide every other row.
    """
    rows, bad = [], []
    if not path.is_file():
        return rows, bad
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                bad.append((path, lineno, f"invalid JSON: {exc.msg}"))
                continue
            missing = [key for key in required if key not in row]
            if missing:
                bad.append((path, lineno, f"missing {', '.join(missing)}"))
                continue
            try:
                row["when"] = local_time(row["ts"])
            except (TypeError, ValueError):
                bad.append((path, lineno, f"unreadable ts {row['ts']!r}"))
                continue
            rows.append(row)
    return rows, bad


def load_logs(logs_dir):
    logs_dir = Path(logs_dir)
    activity, bad_activity = load_jsonl(logs_dir / ACTIVITY, ACTIVITY_KEYS)
    sessions, bad_sessions = load_jsonl(logs_dir / SESSIONS, SESSION_KEYS)
    return Logs(
        activity=activity,
        sessions=sessions,
        proof_logs=sorted(logs_dir.glob(PROOF_LOG_GLOB)),
        conversations={p.stem.split("_", 1)[1]: p for p in logs_dir.glob(CONVERSATION_GLOB)},
        bad_lines=bad_activity + bad_sessions,
    )


def section(text, heading):
    lines = text.split("\n## " + heading + "\n", 1)
    return lines[1].split("\n## ", 1)[0].splitlines() if len(lines) == 2 else []


def reported(out_dir, today):
    """Return (session id prefixes, proof log names) listed in the reports
    under out_dir that are dated before today.

    Today's own reports are left out so a rerun with --force regenerates the
    same report instead of excluding everything it reported the first time.
    Reports list a session by the first 8 characters of its id, so that
    prefix is all there is to match on.
    """
    sessions, proof_logs = set(), set()
    for path in Path(out_dir).glob(REPORT_GLOB):
        if path.name[:10] >= today or path.suffix not in (".md", ".html"):
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".html":
            found = dict(REPORTED_HTML.findall(text))
            sessions |= set(found.get("sessions", "").split())
            proof_logs |= set(found.get("proof-logs", "").split())
            continue
        sessions |= {m[1] for line in section(text, "Sessions") if (m := REPORTED_SESSION.match(line))}
        proof_logs |= {m[1] for line in section(text, "References") if (m := REPORTED_PROOF_LOG.match(line))}
    return sessions, proof_logs
