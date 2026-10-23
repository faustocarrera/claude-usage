import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path


class SessionReport:
    """Per-session report built straight from the transcripts, with no hooks
    needed: what ran where, for how long, with which tools, touching which
    files, at what cost."""

    # Tools whose successful results change a file on disk.
    EDIT_TOOLS = {"Edit", "MultiEdit", "Write", "NotebookEdit"}
    # Gaps between transcript entries longer than this count as idle time, so
    # a session left open overnight doesn't report a 12-hour duration.
    IDLE_GAP = timedelta(minutes=15)
    TOP_FILES = 20

    def __init__(self, usage):
        self.usage = usage

    @staticmethod
    def _parse_ts(raw):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return None

    @staticmethod
    def _is_prompt(entry: dict) -> bool:
        """A message the person typed, as opposed to a tool result, a
        Claude Code injected meta message, or a compaction summary."""
        if entry.get("isMeta") or entry.get("isCompactSummary") or entry.get("isSidechain"):
            return False
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            return not content.startswith("<local-command-stdout>")
        if isinstance(content, list):
            types = {b.get("type") for b in content if isinstance(b, dict)}
            return "text" in types and "tool_result" not in types
        return False

    @staticmethod
    def _line_counts(result) -> tuple:
        """(added, deleted) lines from an edit tool's toolUseResult."""
        if not isinstance(result, dict):
            return 0, 0
        patch = result.get("structuredPatch") or []
        if patch:
            add = sum(1 for h in patch for l in h.get("lines", []) if l.startswith("+"))
            delete = sum(1 for h in patch for l in h.get("lines", []) if l.startswith("-"))
            return add, delete
        if result.get("type") == "create" and isinstance(result.get("content"), str):
            return len(result["content"].splitlines()), 0
        return 0, 0

    @staticmethod
    def _new_session(sid):
        return {
            "id": sid,
            "project": None,
            "branch": None,
            "times": [],
            "models": set(),
            "prompts": 0,
            "tools": Counter(),
            "edits": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_tokens": 0,
            "cache_read_tokens": 0,
            "cost": 0.0,
        }

    def collect(self):
        """Walk every transcript once and return {session id: raw session}.

        Entries are attributed by their own sessionId rather than by file, and
        usage, tool calls and tool results are each deduplicated, since Claude
        Code writes the same message into more than one file and splits one
        message's content blocks across several lines."""
        sessions = {}
        seen_usage, seen_tools, seen_results = set(), set(), set()
        tool_names = {}
        pending_edits = []

        for path in sorted(self.usage.claude_dir.rglob("*.jsonl")):
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
                        kind = entry.get("type")
                        sid = entry.get("sessionId")
                        ts = self._parse_ts(entry.get("timestamp"))
                        if kind not in ("user", "assistant") or not sid or ts is None:
                            continue

                        s = sessions.get(sid) or sessions.setdefault(sid, self._new_session(sid))
                        s["times"].append(ts)
                        s["project"] = s["project"] or entry.get("cwd")
                        s["branch"] = s["branch"] or entry.get("gitBranch")
                        msg = entry.get("message") or {}
                        content = msg.get("content")
                        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []

                        if kind == "user":
                            if self._is_prompt(entry):
                                s["prompts"] += 1
                            for b in blocks:
                                if b.get("type") != "tool_result" or b.get("is_error"):
                                    continue
                                tid = b.get("tool_use_id")
                                if tid and tid not in seen_results:
                                    seen_results.add(tid)
                                    pending_edits.append((s, tid, entry.get("toolUseResult")))
                            continue

                        for b in blocks:
                            if b.get("type") == "tool_use" and b.get("id") not in seen_tools:
                                seen_tools.add(b.get("id"))
                                inp = b.get("input") or {}
                                tool_names[b.get("id")] = (b.get("name"), inp.get("file_path") or inp.get("notebook_path"))
                                s["tools"][b.get("name") or "unknown"] += 1

                        usage = msg.get("usage")
                        key = self.usage._record_key(entry, line)
                        if not usage or key in seen_usage:
                            continue
                        seen_usage.add(key)
                        model = msg.get("model", "unknown")
                        s["models"].add(model)
                        tokens = {
                            "input_tokens": usage.get("input_tokens", 0) or 0,
                            "output_tokens": usage.get("output_tokens", 0) or 0,
                            "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
                            "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
                        }
                        for k, v in tokens.items():
                            s[k] += v
                        p = self.usage.pricing_cache.price_for_model(model)
                        s["cost"] += (
                            tokens["input_tokens"] * p["input_cost_per_token"]
                            + tokens["output_tokens"] * p["output_cost_per_token"]
                            + tokens["cache_creation_tokens"] * p["cache_creation_input_token_cost"]
                            + tokens["cache_read_tokens"] * p["cache_read_input_token_cost"]
                        )
            except OSError:
                continue

        # Results are matched to their tool call only after every file is read,
        # since a call and its result can land in different files.
        for s, tid, result in pending_edits:
            name, file_path = tool_names.get(tid, (None, None))
            if name in self.EDIT_TOOLS and file_path:
                add, delete = self._line_counts(result)
                s["edits"].append((file_path, add, delete))
        return sessions

    def _active_minutes(self, times):
        times = sorted(times)
        active = sum(
            (b - a for a, b in zip(times, times[1:]) if b - a <= self.IDLE_GAP),
            timedelta(),
        )
        return round(active.total_seconds() / 60)

    @staticmethod
    def _display_path(path):
        if not path:
            return "?"
        home = str(Path.home())
        return "~" + path[len(home):] if path == home or path.startswith(home + "/") else path

    def build(self, since=None, until=None):
        """Summaries of the sessions that started between since and until,
        inclusive, oldest first. A session is kept or dropped whole, so its
        totals match its row even if it ran past midnight."""
        sessions = []
        for s in self.collect().values():
            start, end = min(s["times"]).astimezone(), max(s["times"]).astimezone()
            if since and start.date() < since:
                continue
            if until and start.date() > until:
                continue
            tokens = s["input_tokens"] + s["output_tokens"]
            sessions.append({
                "id": s["id"],
                "project": self._display_path(s["project"]),
                "branch": s["branch"],
                "start": start,
                "end": end,
                "active_min": self._active_minutes(s["times"]),
                "models": sorted(s["models"]),
                "prompts": s["prompts"],
                "tool_calls": sum(s["tools"].values()),
                "tools": s["tools"],
                "edits": s["edits"],
                "files": len({p for p, _, _ in s["edits"]}),
                "add": sum(a for _, a, _ in s["edits"]),
                "del": sum(d for _, _, d in s["edits"]),
                "input_tokens": s["input_tokens"],
                "output_tokens": s["output_tokens"],
                "cache_creation_tokens": s["cache_creation_tokens"],
                "cache_read_tokens": s["cache_read_tokens"],
                "cost": s["cost"],
                "energy_wh": tokens * self.usage.WH_PER_TOKEN,
                "water_l": tokens * self.usage.L_PER_TOKEN,
            })
        return sorted(sessions, key=lambda s: s["start"])

    # -----------------------------------------------------------------------
    # Rendering
    # -----------------------------------------------------------------------

    @staticmethod
    def _plural(n, word):
        return f"{n} {word}" if n == 1 else f"{n} {word}s"

    @staticmethod
    def _lines(add, delete):
        return f"+{add}/-{delete}"

    @staticmethod
    def _table(headers, rows, markdown=False):
        rows = [[str(c) for c in row] for row in rows]
        if markdown:
            out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
            out += ["| " + " | ".join(c.replace("|", "\\|") for c in row) + " |" for row in rows]
            return "\n".join(out)
        widths = [max(len(r[i]) for r in [headers] + rows) for i in range(len(headers))]

        def fmt_row(row):
            return " | ".join(c.ljust(widths[i]) for i, c in enumerate(row)).rstrip()

        return "\n".join([fmt_row(headers), "-+-".join("-" * w for w in widths)] + [fmt_row(r) for r in rows])

    def summary(self, sessions):
        first, last = sessions[0]["start"].date(), sessions[-1]["start"].date()
        projects = {s["project"] for s in sessions}
        files = {p for s in sessions for p, _, _ in s["edits"]}
        span = f"on {first}" if first == last else f"between {first} and {last}"
        return (
            f"{self._plural(len(sessions), 'session')} in {self._plural(len(projects), 'project')} {span}: "
            f"{sum(s['active_min'] for s in sessions)} active min, "
            f"{self._plural(sum(s['prompts'] for s in sessions), 'prompt')}, "
            f"{self._plural(sum(s['tool_calls'] for s in sessions), 'tool call')}, "
            f"${sum(s['cost'] for s in sessions):.2f} of Claude usage. "
            f"{self._plural(sum(len(s['edits']) for s in sessions), 'edit')} to "
            f"{self._plural(len(files), 'file')} "
            f"({self._lines(sum(s['add'] for s in sessions), sum(s['del'] for s in sessions))})."
        )

    def render(self, sessions, markdown=False):
        if not sessions:
            return "No sessions found."

        def section(title, body):
            return f"## {title}\n\n{body}" if markdown else f"{title}\n{'=' * len(title)}\n{body}"

        projects = defaultdict(lambda: {"sessions": 0, "min": 0, "prompts": 0, "edits": 0,
                                        "add": 0, "del": 0, "cost": 0.0})
        days = defaultdict(lambda: {"sessions": 0, "min": 0, "edits": 0, "add": 0, "del": 0, "cost": 0.0})
        tools = Counter()
        files = defaultdict(lambda: {"edits": 0, "add": 0, "del": 0, "sessions": set()})
        for s in sessions:
            for g in (projects[s["project"]], days[s["start"].date().isoformat()]):
                g["sessions"] += 1
                g["min"] += s["active_min"]
                g["edits"] += len(s["edits"])
                g["add"] += s["add"]
                g["del"] += s["del"]
                g["cost"] += s["cost"]
            projects[s["project"]]["prompts"] += s["prompts"]
            tools.update(s["tools"])
            for p, a, d in s["edits"]:
                f = files[p]
                f["edits"] += 1
                f["add"] += a
                f["del"] += d
                f["sessions"].add(s["id"])

        ranked_files = sorted(files.items(), key=lambda kv: (-kv[1]["edits"], kv[0]))
        parts = [
            section("Summary", self.summary(sessions)),
            section("Sessions", self._table(
                ["Session", "Project", "Date", "Time", "Active min", "Models", "Prompts",
                 "Tools", "Edits", "Files", "Lines", "Cost"],
                [[s["id"][:8], s["project"], s["start"].date().isoformat(),
                  f"{s['start']:%H:%M}-{s['end']:%H:%M}", s["active_min"], ", ".join(s["models"]) or "-",
                  s["prompts"], s["tool_calls"], len(s["edits"]), s["files"],
                  self._lines(s["add"], s["del"]), f"${s['cost']:.2f}"]
                 for s in sessions],
                markdown,
            )),
            section("By project", self._table(
                ["Project", "Sessions", "Active min", "Prompts", "Edits", "Lines", "Cost"],
                [[p, g["sessions"], g["min"], g["prompts"], g["edits"],
                  self._lines(g["add"], g["del"]), f"${g['cost']:.2f}"]
                 for p, g in sorted(projects.items(), key=lambda kv: -kv[1]["cost"])],
                markdown,
            )),
            section("By day", self._table(
                ["Date", "Sessions", "Active min", "Edits", "Lines", "Cost"],
                [[d, g["sessions"], g["min"], g["edits"], self._lines(g["add"], g["del"]), f"${g['cost']:.2f}"]
                 for d, g in sorted(days.items())],
                markdown,
            )),
            section("Tool usage", self._table(
                ["Tool", "Calls"],
                sorted(tools.items(), key=lambda kv: (-kv[1], kv[0])),
                markdown,
            ) if tools else "None."),
            section(f"Most edited files (top {self.TOP_FILES})", self._table(
                ["File", "Edits", "Lines", "Sessions"],
                [[self._display_path(p), f["edits"], self._lines(f["add"], f["del"]), len(f["sessions"])]
                 for p, f in ranked_files[:self.TOP_FILES]],
                markdown,
            ) if files else "None."),
        ]
        if markdown:
            parts.insert(0, f"# Claude Code report {datetime.now():%Y-%m-%d}")
        return "\n\n".join(parts)

    @staticmethod
    def to_json(sessions) -> str:
        return json.dumps([
            {
                "id": s["id"],
                "project": s["project"],
                "branch": s["branch"],
                "start": s["start"].isoformat(),
                "end": s["end"].isoformat(),
                "active_min": s["active_min"],
                "models": s["models"],
                "prompts": s["prompts"],
                "tools": dict(s["tools"].most_common()),
                "edits": len(s["edits"]),
                "files": s["files"],
                "lines_added": s["add"],
                "lines_deleted": s["del"],
                "input_tokens": s["input_tokens"],
                "output_tokens": s["output_tokens"],
                "cache_creation_tokens": s["cache_creation_tokens"],
                "cache_read_tokens": s["cache_read_tokens"],
                "cost_usd": round(s["cost"], 4),
                "energy_wh": round(s["energy_wh"], 2),
                "water_l": round(s["water_l"], 4),
            }
            for s in sessions
        ], indent=2)
