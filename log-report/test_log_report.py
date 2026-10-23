"""Run with: python -m unittest discover -s tools/log-report"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

import render_html

# __main__.py cannot be imported by name without colliding with the runner's own
# __main__, so it is loaded from its path.
_spec = importlib.util.spec_from_file_location("log_report_main", Path(__file__).with_name("__main__.py"))
log_report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(log_report)


def activity(session, day, path, add=1, delete=0):
    # Noon UTC keeps the local date equal to `day` in any zone within ±11h.
    return {"ts": f"{day}T12:00:00Z", "session": session, "tool": "Edit",
            "path": path, "add": add, "del": delete}


def snapshot(session, day, cost, minutes):
    return {"ts": f"{day}T12:30:00Z", "session": session,
            "cost": {"total_cost_usd": cost},
            "proof_log": {"duration_min": minutes}}


class LogReportCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.logs = self.tmp / "logs"
        self.out = self.tmp / "reports"
        (self.logs / "sessions").mkdir(parents=True)
        self.write_logs(
            [
                activity("aaaa", "2026-10-01", "/r/a.py", add=4, delete=1),
                activity("bbbb", "2026-10-02", "/r/a.py", add=3),
                activity("bbbb", "2026-10-02", "/r/b.py", add=3, delete=1),
                # Session bbbb started on the 2nd and kept editing on the 3rd.
                activity("bbbb", "2026-10-03", "/r/c.py", add=2),
                activity("cccc", "2026-10-05", "/r/d.py", add=9, delete=9),
            ],
            [
                snapshot("aaaa", "2026-10-01", 0.5, 10),
                snapshot("bbbb", "2026-10-03", 1.0, 20),
                snapshot("cccc", "2026-10-05", 2.0, 40),
            ],
        )

    def write_logs(self, activity_rows, session_rows):
        (self.logs / "activity-log.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in activity_rows), encoding="utf-8")
        (self.logs / "sessions" / "sessions.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in session_rows), encoding="utf-8")

    def run_tool(self, *args):
        return self.run_html("--markdown", *args)

    def run_html(self, *args):
        return CliRunner().invoke(
            log_report.main, ["--logs", str(self.logs), "--out", str(self.out), *args])

    def report(self):
        (path,) = self.out.glob("*.md")
        return path.name, path.read_text(encoding="utf-8")


class LogReportTest(LogReportCase):
    def test_summary_covers_every_session_without_a_range(self):
        result = self.run_tool()

        self.assertEqual(result.exit_code, 0, result.output)
        name, text = self.report()
        self.assertTrue(name.endswith("_report_log-summary.md"), name)
        self.assertIn(
            "3 sessions between 2026-10-01 and 2026-10-05: 70 min and a total of $3.50 "
            "of Claude usage. 5 edits to 4 files (+21/-11).",
            text,
        )

    def test_range_keeps_whole_sessions_that_start_inside_it(self):
        result = self.run_tool("--from", "2026-10-02", "--to", "2026-10-02")

        self.assertEqual(result.exit_code, 0, result.output)
        _, text = self.report()
        # bbbb's edit on the 3rd counts, because the session started on the 2nd.
        self.assertIn(
            "1 session on 2026-10-02: 20 min and a total of $1.00 of Claude usage. "
            "3 edits to 3 files (+8/-1).",
            text,
        )
        self.assertNotIn("`aaaa`", text)
        self.assertNotIn("d.py", text)

    def test_session_links_to_its_conversation_export(self):
        (self.logs / "conversations").mkdir()
        (self.logs / "conversations" / "2026-10-01_aaaa.md").write_text("# Chat\n", encoding="utf-8")

        result = self.run_tool()

        self.assertEqual(result.exit_code, 0, result.output)
        _, text = self.report()
        self.assertIn("| [`aaaa`](../logs/conversations/2026-10-01_aaaa.md) |", text)
        # A session with no export keeps the plain id.
        self.assertIn("| `bbbb` |", text)

    def test_summary_shows_the_requested_range_not_the_data_bounds(self):
        result = self.run_tool("--from", "2026-09-01", "--to", "2026-10-03")

        self.assertEqual(result.exit_code, 0, result.output)
        _, text = self.report()
        self.assertIn("2 sessions between 2026-09-01 and 2026-10-03:", text)
        self.assertIn("- **Covers**: 2026-09-01 to 2026-10-03", text)

    def test_a_missing_bound_falls_back_to_the_data(self):
        result = self.run_tool("--from", "2026-10-02")

        self.assertEqual(result.exit_code, 0, result.output)
        name, text = self.report()
        self.assertTrue(name.endswith("_report_log-summary-2026-10-02-to-2026-10-05.md"), name)
        self.assertIn("2 sessions between 2026-10-02 and 2026-10-05:", text)

    def test_ranged_report_does_not_collide_with_the_full_report(self):
        self.assertEqual(self.run_tool().exit_code, 0)

        result = self.run_tool("--from", "2026-10-01", "--to", "2026-10-02")

        self.assertEqual(result.exit_code, 0, result.output)
        names = sorted(p.name.split("_", 1)[1] for p in self.out.glob("*.md"))
        self.assertEqual(names, ["report_log-summary-2026-10-01-to-2026-10-02.md",
                                 "report_log-summary.md"])

    def test_unknown_cost_and_duration_are_named_in_the_summary(self):
        self.write_logs([activity("aaaa", "2026-10-01", "/r/a.py")], [])

        result = self.run_tool()

        self.assertEqual(result.exit_code, 0, result.output)
        _, text = self.report()
        self.assertIn(
            "1 session on 2026-10-01: duration unknown and Claude usage of unknown cost. "
            "1 edit to 1 file (+1/-0). Totals are partial:",
            text,
        )

    def test_from_after_to_is_a_usage_error(self):
        result = self.run_tool("--from", "2026-10-05", "--to", "2026-10-01")

        self.assertEqual(result.exit_code, 2)
        self.assertIn("--from 2026-10-05 is after --to 2026-10-01", result.output)
        self.assertFalse(self.out.exists())

    def test_malformed_date_is_a_usage_error(self):
        result = self.run_tool("--from", "10/01/2026")

        self.assertEqual(result.exit_code, 2)

    def test_range_with_no_sessions_exits_1(self):
        result = self.run_tool("--from", "2026-11-01", "--to", "2026-11-30")

        self.assertEqual(result.exit_code, 1)
        self.assertIn("No sessions started between 2026-11-01 and 2026-11-30", result.output)
        self.assertFalse(self.out.exists())


class EarlierReportsTest(LogReportCase):
    def earlier_report(self, *args):
        """Write a report with the tool and backdate it, so it reads as one
        from an earlier day."""
        self.assertEqual(self.run_tool(*args).exit_code, 0)
        (path,) = self.out.glob("*.md")
        path.rename(path.with_name("2000-01-01" + path.name[10:]))

    def todays_report(self):
        (path,) = [p for p in self.out.glob("*.md") if not p.name.startswith("2000-01-01")]
        return path.read_text(encoding="utf-8")

    def test_sessions_in_an_earlier_report_are_left_out(self):
        self.earlier_report("--to", "2026-10-02")

        result = self.run_tool()

        self.assertEqual(result.exit_code, 0, result.output)
        text = self.todays_report()
        self.assertIn(
            "1 session on 2026-10-05: 40 min and a total of $2.00 of Claude usage. "
            "1 edit to 1 file (+9/-9).",
            text,
        )
        self.assertIn("- **Covers**: 2026-10-05 to 2026-10-05", text)
        self.assertIn("- **Excludes**: 2 sessions already in earlier reports", text)
        self.assertNotIn("`aaaa`", text)
        self.assertNotIn("`bbbb`", text)

    def test_a_session_after_midnight_is_still_left_out(self):
        # bbbb's last edit is on the 3rd, after the earlier report's last day.
        self.earlier_report("--to", "2026-10-02")

        self.run_tool()

        self.assertNotIn("c.py", self.todays_report())

    def test_all_includes_sessions_in_earlier_reports(self):
        self.earlier_report("--to", "2026-10-02")

        result = self.run_tool("--all")

        self.assertEqual(result.exit_code, 0, result.output)
        text = self.todays_report()
        self.assertIn("3 sessions between 2026-10-01 and 2026-10-05:", text)
        self.assertNotIn("**Excludes**", text)

    def test_from_includes_sessions_in_earlier_reports(self):
        self.earlier_report("--to", "2026-10-02")

        result = self.run_tool("--from", "2026-10-02")

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("2 sessions between 2026-10-02 and 2026-10-05:", self.todays_report())

    def test_a_report_from_today_is_not_an_earlier_report(self):
        self.assertEqual(self.run_tool().exit_code, 0)

        result = self.run_tool("--force")

        self.assertEqual(result.exit_code, 0, result.output)
        _, text = self.report()
        self.assertIn("3 sessions between 2026-10-01 and 2026-10-05:", text)

    def test_proof_logs_in_an_earlier_report_are_left_out(self):
        (self.logs / "2026-10-01_proof-log.md").write_text("# Proof\n", encoding="utf-8")
        self.earlier_report("--to", "2026-10-02")
        (self.logs / "2026-10-05_proof-log.md").write_text("# Proof\n", encoding="utf-8")

        self.run_tool()

        text = self.todays_report()
        self.assertIn("2026-10-05_proof-log.md", text)
        self.assertNotIn("2026-10-01_proof-log.md", text)

    def test_nothing_new_since_the_earlier_reports_exits_1(self):
        self.earlier_report()

        result = self.run_tool()

        self.assertEqual(result.exit_code, 1)
        self.assertIn("already in earlier reports", result.output)
        self.assertIn("--all", result.output)


CONVERSATION = """# Fix the parser

- **Session**: `aaaa`
- **When**: 2026-10-01 09:00 - 09:20
- **Branch**: main
- **Directory**: /r

### You

Why does **this** fail? <script>alert(1)</script>

### Claude

- `Read` /r/a.py

It fails because `x` is None.

### You

thanks
"""


class ParseConversationTest(unittest.TestCase):
    def test_splits_title_metadata_and_turns(self):
        convo = render_html.parse_conversation(CONVERSATION)

        self.assertEqual(convo["title"], "Fix the parser")
        self.assertEqual(convo["meta"]["When"], "2026-10-01 09:00 - 09:20")
        self.assertEqual(convo["meta"]["Branch"], "main")
        self.assertEqual([who for who, _ in convo["turns"]], ["You", "Claude", "You"])
        self.assertEqual(convo["turns"][1][1], "- `Read` /r/a.py\n\nIt fails because `x` is None.")

    def test_a_tool_line_right_after_text_is_its_own_list_item(self):
        # conversation-log.sh puts no blank line between a reply and the tool
        # call that follows it.
        out = render_html.turn_html("Now the tests.\n- `Bash` Run the suite")

        self.assertIn("<p>Now the tests.</p>", out)
        self.assertIn("<li><code>Bash</code> Run the suite</li>", out)


class HtmlReportTest(LogReportCase):
    def setUp(self):
        super().setUp()
        (self.logs / "conversations").mkdir()
        (self.logs / "conversations" / "2026-10-01_aaaa.md").write_text(CONVERSATION, encoding="utf-8")

    def html_report(self):
        (path,) = self.out.glob("*.html")
        return path.name, path.read_text(encoding="utf-8")

    def test_html_is_the_default_format(self):
        result = self.run_html()

        self.assertEqual(result.exit_code, 0, result.output)
        name, text = self.html_report()
        self.assertTrue(name.endswith("_report_log-summary.html"), name)
        self.assertEqual(list(self.out.glob("*.md")), [])
        self.assertIn("3 sessions between 2026-10-01 and 2026-10-05", text)
        self.assertIn('data-tab="summary"', text)
        self.assertIn('data-tab="conversations"', text)

    def test_turns_are_marked_by_speaker_in_order(self):
        self.run_html()

        _, text = self.html_report()
        you = text.index('class="turn you"')
        claude = text.index('class="turn claude"', you)
        self.assertLess(you, claude)
        self.assertIn("<strong>this</strong>", text)
        self.assertIn("<code>x</code> is None", text)

    def test_raw_html_in_a_turn_is_escaped(self):
        self.run_html()

        _, text = self.html_report()
        self.assertNotIn("<script>alert(1)</script>", text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)

    def test_conversation_shows_the_session_properties(self):
        self.write_logs(
            [activity("aaaa", "2026-10-01", "/r/a.py", add=4, delete=1)],
            [{**snapshot("aaaa", "2026-10-01", 0.5, 10), "model": {"display_name": "Opus 5.5"}}],
        )

        self.run_html()

        _, text = self.html_report()
        panel = text[text.index('id="c-aaaa"'):]
        self.assertIn("Opus 5.5", panel)
        self.assertIn("$0.50", panel)
        self.assertIn("/r/a.py", panel)

    def test_session_row_links_to_its_conversation(self):
        self.run_html()

        _, text = self.html_report()
        self.assertIn('href="#c-aaaa"', text)

    def test_sessions_in_an_earlier_html_report_are_left_out(self):
        self.assertEqual(self.run_html("--to", "2026-10-02").exit_code, 0)
        (path,) = self.out.glob("*.html")
        path.rename(path.with_name("2000-01-01" + path.name[10:]))

        result = self.run_html()

        self.assertEqual(result.exit_code, 0, result.output)
        (today,) = [p for p in self.out.glob("*.html") if not p.name.startswith("2000-01-01")]
        text = today.read_text(encoding="utf-8")
        self.assertIn("1 session on 2026-10-05", text)
        self.assertNotIn('id="c-aaaa"', text)

    def test_each_format_has_its_own_default_directory(self):
        runner = CliRunner()
        with runner.isolated_filesystem():
            logs = ["--logs", str(self.logs)]
            self.assertEqual(runner.invoke(log_report.main, logs).exit_code, 0)
            self.assertEqual(runner.invoke(log_report.main, [*logs, "--markdown"]).exit_code, 0)
            self.assertEqual(len(list(Path(".brain/logs/reports").glob("*.html"))), 1)
            self.assertEqual(len(list(Path(".brain/reports").glob("*.md"))), 1)


if __name__ == "__main__":
    unittest.main()
