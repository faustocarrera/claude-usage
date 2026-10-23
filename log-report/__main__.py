#!/usr/bin/env python3

from datetime import date
from pathlib import Path
import click

import load
import render
import render_html

DEFAULT_LOGS = ".brain/logs"
# The HTML embeds whole conversations, so it stays under the git-ignored logs/
# beside the exports it is built from.
DEFAULT_OUT = ".brain/logs/reports"
# Named to the `reports/` convention in CLAUDE.md's Document naming, so
# reports-locator and /create_report find it alongside hand-requested reports.
DEFAULT_MARKDOWN_OUT = ".brain/reports"
SLUG = "log-summary"


@click.command()
@click.option("--logs", "logs_dir", default=DEFAULT_LOGS, show_default=True, help="Directory the hooks write their logs into.")
@click.option("--out", "out_dir", help=f"Directory the report is written into.  [default: {DEFAULT_OUT}, or {DEFAULT_MARKDOWN_OUT} with --markdown]")
@click.option("--from", "date_from", type=click.DateTime(["%Y-%m-%d"]), help="Only sessions that started on or after this date (YYYY-MM-DD).")
@click.option("--to", "date_to", type=click.DateTime(["%Y-%m-%d"]), help="Only sessions that started on or before this date (YYYY-MM-DD).")
@click.option("--all", "include_reported", is_flag=True, help="Include sessions already in reports from earlier days.")
@click.option("--markdown", "as_markdown", is_flag=True, help="Write a markdown report instead of HTML.")
@click.option("--force", is_flag=True, help="Overwrite the report. Without this, an existing report is left alone.")
def main(logs_dir, out_dir, date_from, date_to, include_reported, as_markdown, force):
    """Summarize the logs under .brain/logs into an HTML report with a
    Summary tab and a Conversations tab, or into markdown with --markdown.

    Without --from or --all, sessions and proof logs already listed in a
    report from an earlier day are left out.

    Exits 1 when there is nothing to report on (no log rows, no session in
    the range, or none that is not already reported) or the report cannot be
    written, 2 on a usage error or when the report already exists and
    --force was not given, 0 otherwise.
    """
    out_dir = out_dir or (DEFAULT_MARKDOWN_OUT if as_markdown else DEFAULT_OUT)
    date_from = date_from and date_from.date()
    date_to = date_to and date_to.date()
    if date_from and date_to and date_from > date_to:
        raise click.UsageError(f"--from {date_from} is after --to {date_to}.")

    logs = load.load_logs(logs_dir)
    for bad_path, lineno, reason in logs.bad_lines:
        click.echo(f"Skipped {bad_path}:{lineno}: {reason}", err=True)
    if not logs.activity and not logs.sessions:
        raise click.ClickException(
            f"No log rows found under {logs_dir}. Expected {load.ACTIVITY} or {load.SESSIONS}."
        )

    today = date.today().isoformat()
    ranged = date_from is not None or date_to is not None
    logs = render.within(logs, date_from, date_to)
    in_range = len(render.build_sessions(logs))
    if in_range == 0:
        raise click.ClickException(
            f"No sessions started between {date_from or 'the first log'} and {date_to or 'the last log'} under {logs_dir}."
        )

    # An explicit --from is a request for that range as it stands, so only an
    # open-ended run picks up where the earlier reports left off.
    if date_from is None and not include_reported:
        try:
            skip_sessions, skip_proof_logs = load.reported(out_dir, today)
        except (OSError, UnicodeDecodeError) as exc:
            raise click.ClickException(f"Could not read the earlier reports in {out_dir}: {exc}")
        logs = render.within(logs, None, None, skip_sessions, skip_proof_logs)
    sessions = render.build_sessions(logs)
    excluded = in_range - len(sessions)
    if not sessions:
        raise click.ClickException(
            f"All {render.plural(in_range, 'session')} under {logs_dir} are already in earlier reports in {out_dir}. "
            "Use --all to report on them again."
        )
    first = date_from.isoformat() if date_from else sessions[0]["date"]
    last = date_to.isoformat() if date_to else sessions[-1]["date"]

    slug = f"{SLUG}-{first}-to-{last}" if ranged else SLUG
    path = Path(out_dir) / f"{today}_report_{slug}.{'md' if as_markdown else 'html'}"
    # A report can collect hand-written notes after it lands, so a rerun on the
    # same day does not clobber it unless asked to.
    if path.exists() and not force:
        click.echo(f"skipped  {path} (exists, use --force to overwrite)")
        raise SystemExit(2)

    renderer = render if as_markdown else render_html
    try:
        report = renderer.render_report(logs, today, first, last, Path.cwd(), logs_dir, out_dir, excluded)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"Could not write {path}: {exc.strerror}")
    click.echo(f"wrote    {path}")


if __name__ == "__main__":
    main()
