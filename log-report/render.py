"""Build the markdown report from the loaded logs."""

import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import load

NONE = "-"


def cell(value):
    if value is None or value == "":
        return NONE
    return str(value).replace("|", "\\|")


def money(value):
    return NONE if value is None else f"${value:.2f}"


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def lines(add, delete):
    return f"+{add}/-{delete}"


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return "\n".join(out)


def display_path(path, root):
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)


def session_cell(sid, conversations, out_dir, link=None):
    label = f"`{sid[:8]}`"
    if sid not in conversations:
        return label
    return f"[{label}]({link(sid) if link else os.path.relpath(conversations[sid], out_dir)})"


def known(value):
    # session-stats.sh writes null when it never saw the session start;
    # "unknown" is how proof-log-session.sh spells the same gap.
    return None if value in (None, "", "unknown") else value


def build_sessions(logs):
    """One summary per session id seen in either log, oldest first.

    The activity log is the record of edits; sessions.jsonl adds what only the
    statusline knows (model, effort, cost). A session can be missing from
    either, so each field falls back to what the other source can supply.
    """
    edits = defaultdict(list)
    for row in logs.activity:
        edits[row["session"]].append(row)
    # sessions.jsonl is one line per session; if a hook ever writes a second,
    # the later snapshot carries the more complete totals.
    stats = {row["session"]: row for row in logs.sessions}

    sessions = []
    for sid in edits.keys() | stats.keys():
        rows = sorted(edits.get(sid, []), key=lambda r: r["when"])
        snap = stats.get(sid, {})
        proof = snap.get("proof_log") or {}
        first = rows[0]["when"] if rows else snap["when"]
        sessions.append({
            "id": sid,
            "first": first,
            "date": first.date().isoformat(),
            "start": known(proof.get("start")) or (rows[0]["when"].strftime("%H:%M") if rows else None),
            "end": known(proof.get("end")) or (rows[-1]["when"].strftime("%H:%M") if rows else None),
            "minutes": proof.get("duration_min") if isinstance(proof.get("duration_min"), int) else None,
            "model": (snap.get("model") or {}).get("display_name"),
            "effort": (snap.get("effort") or {}).get("level"),
            "cost": (snap.get("cost") or {}).get("total_cost_usd"),
            "edits": len(rows),
            "files": len({r["path"] for r in rows}),
            "add": sum(r["add"] for r in rows),
            "del": sum(r["del"] for r in rows),
        })
    return sorted(sessions, key=lambda s: s["first"])


def within(logs, date_from, date_to, skip_sessions=frozenset(), skip_proof_logs=frozenset()):
    """Keep the sessions that started between the two dates, inclusive; either
    bound may be None. A session is kept or dropped whole, edits made after
    midnight included, so its row in the sessions table matches the totals.
    Sessions whose 8-character id prefix is in skip_sessions, and proof logs
    named in skip_proof_logs, are dropped too."""
    keep = {
        s["id"] for s in build_sessions(logs)
        if (date_from is None or s["first"].date() >= date_from)
        and (date_to is None or s["first"].date() <= date_to)
        and s["id"][:8] not in skip_sessions
    }
    return load.Logs(
        activity=[r for r in logs.activity if r["session"] in keep],
        sessions=[r for r in logs.sessions if r["session"] in keep],
        proof_logs=[
            p for p in logs.proof_logs
            if (date_from is None or p.name[:10] >= date_from.isoformat())
            and (date_to is None or p.name[:10] <= date_to.isoformat())
            and p.name not in skip_proof_logs
        ],
        conversations=logs.conversations,
        bad_lines=logs.bad_lines,
    )


def summary(sessions, logs, first, last):
    costs = [s["cost"] for s in sessions if s["cost"] is not None]
    minutes = [s["minutes"] for s in sessions if s["minutes"] is not None]
    files = {r["path"] for r in logs.activity}
    add = sum(r["add"] for r in logs.activity)
    delete = sum(r["del"] for r in logs.activity)
    text = (
        f"{plural(len(sessions), 'session')} "
        f"{f'on {first}' if first == last else f'between {first} and {last}'}: "
        f"{f'{sum(minutes)} min' if minutes else 'duration unknown'} and "
        f"{f'a total of {money(sum(costs))} of Claude usage' if costs else 'Claude usage of unknown cost'}. "
        f"{plural(len(logs.activity), 'edit')} to {plural(len(files), 'file')} ({lines(add, delete)})."
    )
    gaps = []
    if len(costs) < len(sessions):
        gaps.append(f"cost is known for {len(costs)} of {len(sessions)} sessions")
    if len(minutes) < len(sessions):
        gaps.append(f"duration is known for {len(minutes)} of {len(sessions)} sessions")
    if gaps:
        text += " Totals are partial: " + "; ".join(gaps) + "."
    return text


def by_day(sessions):
    days = defaultdict(list)
    for s in sessions:
        days[s["date"]].append(s)
    rows = []
    for day, group in sorted(days.items()):
        costs = [s["cost"] for s in group if s["cost"] is not None]
        rows.append([
            day,
            len(group),
            sum(s["edits"] for s in group),
            lines(sum(s["add"] for s in group), sum(s["del"] for s in group)),
            money(sum(costs)) if costs else NONE,
        ])
    return table(["Date", "Sessions", "Edits", "Lines", "Cost"], rows)


def by_file(logs, root):
    files = defaultdict(lambda: {"edits": 0, "add": 0, "del": 0, "sessions": set()})
    for r in logs.activity:
        f = files[r["path"]]
        f["edits"] += 1
        f["add"] += r["add"]
        f["del"] += r["del"]
        f["sessions"].add(r["session"])
    ranked = sorted(files.items(), key=lambda kv: (-kv[1]["edits"], kv[0]))
    return table(
        ["File", "Edits", "Lines", "Sessions"],
        [[f"`{display_path(p, root)}`", f["edits"], lines(f["add"], f["del"]), len(f["sessions"])]
         for p, f in ranked],
    )


def needs_review(logs, root):
    flagged = Counter(r["path"] for r in logs.activity if r.get("needs_review"))
    if not flagged:
        return "None."
    return table(
        ["File", "Edits"],
        [[f"`{display_path(p, root)}`", n] for p, n in sorted(flagged.items())],
    )


def render_report(logs, today, first, last, root, logs_dir, out_dir, excluded=0, link=None):
    sessions = build_sessions(logs)
    offset = datetime.now().astimezone().isoformat()[-6:]
    sources = [
        f"`{display_path(Path(logs_dir) / load.ACTIVITY, root)}` ({len(logs.activity)} rows)",
        f"`{display_path(Path(logs_dir) / load.SESSIONS, root)}` ({len(logs.sessions)} rows)",
        f"{len(logs.proof_logs)} proof logs",
    ]

    meta = [
        "- **Status**: Generated",
        f"- **Date**: {today}",
        "- **Owner**: `tools/log-report`",
        f"- **Covers**: {first} to {last}",
    ]
    if excluded:
        meta.append(f"- **Excludes**: {plural(excluded, 'session')} already in earlier reports")
    meta += [
        f"- **Sources**: {', '.join(sources)}",
        f"- **Times**: local, UTC{offset}",
    ]

    parts = [
        f"# Log report {today}",
        "\n".join(meta),
        "## Executive summary\n\n" + summary(sessions, logs, first, last),
        "## Sessions\n\n" + table(
            ["Session", "Date", "Time", "Min", "Model", "Effort", "Cost", "Edits", "Files", "Lines"],
            [[session_cell(s["id"], logs.conversations, out_dir, link), s["date"],
              f"{s['start'] or '?'}-{s['end'] or '?'}",
              s["minutes"], s["model"], s["effort"], money(s["cost"]),
              s["edits"], s["files"], lines(s["add"], s["del"])]
             for s in sessions],
        ),
        "## Activity by day\n\n" + by_day(sessions),
        "## Files\n\n" + by_file(logs, root),
        "## Tool usage\n\n" + table(
            ["Tool", "Calls"],
            sorted(Counter(r["tool"] for r in logs.activity).items(), key=lambda kv: (-kv[1], kv[0])),
        ),
        "## Needs review\n\n" + needs_review(logs, root),
    ]

    if logs.bad_lines:
        parts.append("## Skipped log lines\n\n" + table(
            ["File", "Line", "Reason"],
            [[f"`{display_path(p, root)}`", n, why] for p, n, why in logs.bad_lines],
        ))

    refs = [f"- [{p.name}]({os.path.relpath(p, out_dir)})" for p in logs.proof_logs]
    parts.append("## References\n\n" + ("\n".join(refs) if refs else "No proof logs."))

    return "\n\n".join(parts) + "\n"
