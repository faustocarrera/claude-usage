"""Build the HTML report: the markdown summary plus every conversation export,
in one self-contained file with no external assets."""

import html
import re
from string import Template

import markdown

import render

SPEAKERS = ("### You", "### Claude")
# conversation-log.sh writes its metadata as `- **Key**: value` lines.
META = re.compile(r"^- \*\*([^*]+)\*\*: (.*)$")
# ...and a tool call as "- `Name` detail", directly under the reply before it.
TOOL_AFTER_TEXT = re.compile(r"^(?!- )(.+)\n(?=- `)", re.M)


def parse_conversation(text):
    """Split a conversation-log.sh export into its title, metadata and turns.

    A turn runs from one speaker heading to the next, so a reply that itself
    contains a line reading `### You` or `### Claude` is split there.
    """
    lines = text.splitlines()
    title = lines[0][2:].strip() if lines and lines[0].startswith("# ") else ""
    meta, turns, who, body = {}, [], None, []
    for line in lines[1:]:
        if line in SPEAKERS:
            if who:
                turns.append((who, "\n".join(body).strip()))
            who, body = line[4:], []
        elif who:
            body.append(line)
        elif m := META.match(line):
            meta[m[1]] = m[2].strip("`")
    if who:
        turns.append((who, "\n".join(body).strip()))
    return {"title": title, "meta": meta, "turns": turns}


def to_html(text):
    # Conversations quote prompts, tags and pasted pages verbatim, so raw HTML
    # is shown as text rather than passed through into the page.
    md = markdown.Markdown(extensions=["tables", "fenced_code"])
    md.preprocessors.deregister("html_block")
    md.inlinePatterns.deregister("html")
    return md.convert(text)


def turn_html(body):
    # Python-Markdown only starts a list after a blank line, unlike GitHub.
    return to_html(TOOL_AFTER_TEXT.sub(r"\1\n\n", body))


def esc(value):
    return html.escape(render.NONE if value is None or value == "" else str(value))


def tiles(sessions, logs):
    costs = [s["cost"] for s in sessions if s["cost"] is not None]
    minutes = [s["minutes"] for s in sessions if s["minutes"] is not None]
    values = [
        ("Sessions", len(sessions)),
        ("Minutes", sum(minutes) if minutes else None),
        ("Cost", render.money(sum(costs)) if costs else None),
        ("Edits", len(logs.activity)),
    ]
    return "".join(
        f'<div class="tile"><div class="value">{esc(v)}</div><div class="label">{label}</div></div>'
        for label, v in values
    )


def properties(s, meta, files):
    rows = [
        ("Session", s["id"]),
        ("When", meta.get("When")),
        ("Branch", meta.get("Branch")),
        ("Directory", meta.get("Directory")),
        ("Model", s["model"]),
        ("Effort", s["effort"]),
        ("Cost", render.money(s["cost"])),
        ("Minutes", s["minutes"]),
        ("Edits", s["edits"]),
        ("Lines", render.lines(s["add"], s["del"])),
    ]
    out = ['<aside class="properties"><h3>Properties</h3><dl>']
    out += [f"<dt>{label}</dt><dd>{esc(v)}</dd>" for label, v in rows]
    out.append("</dl><h3>Files edited</h3>")
    out.append("<ul>" + "".join(f"<li><code>{esc(f)}</code></li>" for f in files) + "</ul>" if files else "<p>None.</p>")
    out.append("</aside>")
    return "".join(out)


def read(path):
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"Could not read the conversation export {path}: {exc}") from exc


def render_report(logs, today, first, last, root, logs_dir, out_dir, excluded=0):
    sessions = render.build_sessions(logs)
    summary_md = render.render_report(
        logs, today, first, last, root, logs_dir, out_dir, excluded, link=lambda sid: f"#c-{sid}")
    title, summary_md = summary_md.split("\n", 1)

    files = {}
    for r in logs.activity:
        files.setdefault(r["session"], set()).add(render.display_path(r["path"], root))

    items, panels = [], []
    # Newest first, since the list is for finding today's session rather than
    # reading the period in order the way the summary tables do.
    for s in reversed(sessions):
        if s["id"] not in logs.conversations:
            continue
        convo = parse_conversation(read(logs.conversations[s["id"]]))
        name = convo["title"] or f"Session {s['id'][:8]}"
        cid = f"c-{s['id']}"
        items.append(
            f'<a class="session-item" href="#{esc(cid)}"><span class="name">{esc(name)}</span>'
            f'<span class="sub">{esc(s["date"])} {esc(s["start"])} | {esc(s["model"])} | {esc(render.money(s["cost"]))}</span></a>'
        )
        turns = "".join(
            f'<div class="turn {who.lower()}"><div class="speaker">{who}</div><div class="body">{turn_html(body)}</div></div>'
            for who, body in convo["turns"]
        )
        panels.append(
            f'<div class="conversation" id="{esc(cid)}" hidden><article><h2>{esc(name)}</h2>'
            f'<p class="when">{esc(convo["meta"].get("When"))} | {esc(convo["meta"].get("Branch"))}</p>{turns}</article>'
            f'{properties(s, convo["meta"], sorted(files.get(s["id"], ())))}</div>'
        )

    return PAGE.substitute(
        title=esc(title[2:]),
        sessions=esc(" ".join(s["id"][:8] for s in sessions)),
        proof_logs=esc(" ".join(p.name for p in logs.proof_logs)),
        tiles=tiles(sessions, logs),
        summary=to_html(summary_md),
        count=len(items),
        items="".join(items) or '<p class="empty">No conversation exports for these sessions.</p>',
        panels="".join(panels),
    )


PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="log-report-sessions" content="$sessions">
<meta name="log-report-proof-logs" content="$proof_logs">
<title>$title</title>
<style>
:root { --bg: #0f1115; --panel: #16181d; --line: #262a31; --text: #d6d8dc; --muted: #8b9099;
        --you: #4c8dff; --claude: #e8743b; }
* { box-sizing: border-box; }
[hidden] { display: none !important; }
body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.55 system-ui, sans-serif; }
header { display: flex; align-items: center; gap: 32px; padding: 0 24px; border-bottom: 1px solid var(--line); }
header h1 { font-size: 18px; margin: 16px 0; }
nav button { background: none; border: 0; border-bottom: 2px solid transparent; color: var(--muted);
             font: inherit; padding: 18px 4px; margin-right: 20px; cursor: pointer; }
nav button.active { color: var(--text); border-bottom-color: var(--text); }
.count { background: var(--line); border-radius: 10px; padding: 1px 8px; margin-left: 4px; font-size: 12px; }
#summary { padding: 24px 32px; max-width: 1200px; }
.tiles { display: flex; gap: 16px; margin-bottom: 24px; }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 16px 24px; min-width: 140px; }
.tile .value { font-size: 26px; font-weight: 600; }
.tile .label, h2, h3 { color: var(--muted); text-transform: uppercase; letter-spacing: .06em; font-size: 12px; }
table { border-collapse: collapse; margin: 8px 0 24px; }
th, td { border-bottom: 1px solid var(--line); padding: 6px 12px; text-align: left; }
th { color: var(--muted); font-weight: 500; }
a { color: var(--you); }
code { font: 12px ui-monospace, monospace; background: #1d2026; padding: 1px 4px; border-radius: 3px; }
pre { background: #1d2026; padding: 12px; border-radius: 6px; overflow-x: auto; }
pre code { background: none; padding: 0; }
#conversations { display: grid; grid-template-columns: 320px 1fr; height: calc(100vh - 62px); }
.list { border-right: 1px solid var(--line); overflow-y: auto; padding: 16px; }
.list input { width: 100%; background: var(--panel); border: 1px solid var(--line); border-radius: 6px;
              color: var(--text); padding: 8px 10px; margin-bottom: 12px; font: inherit; }
.session-item { display: block; padding: 10px 12px; border-left: 3px solid transparent; color: var(--text);
                text-decoration: none; border-bottom: 1px solid var(--line); }
.session-item.active { border-left-color: var(--claude); background: var(--panel); }
.session-item .sub { display: block; color: var(--muted); font-size: 12px; }
.conversation { display: grid; grid-template-columns: 1fr 300px; overflow: hidden; }
article { overflow-y: auto; padding: 24px 32px; }
article h2 { color: var(--text); text-transform: none; letter-spacing: 0; font-size: 22px; margin: 0; }
.when { color: var(--muted); margin-top: 4px; }
.turn { border-left: 3px solid; border-radius: 6px; padding: 10px 16px; margin: 14px 0; background: var(--panel); }
.turn.you { border-color: var(--you); margin-right: 80px; }
.turn.claude { border-color: var(--claude); margin-left: 80px; }
.speaker { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }
.you .speaker { color: var(--you); }
.claude .speaker { color: var(--claude); }
.properties { border-left: 1px solid var(--line); overflow-y: auto; padding: 24px 20px; }
dl { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px; margin: 0 0 24px; }
dt { color: var(--muted); }
dd { margin: 0; word-break: break-all; }
.properties ul { padding-left: 16px; }
.empty { color: var(--muted); }
</style>
</head>
<body>
<header>
<h1>$title</h1>
<nav>
<button data-tab="summary" class="active">Summary</button>
<button data-tab="conversations">Conversations <span class="count">$count</span></button>
</nav>
</header>
<section id="summary" class="tab-panel">
<div class="tiles">$tiles</div>
$summary
</section>
<section id="conversations" class="tab-panel" hidden>
<div class="list"><input id="filter" placeholder="Filter conversations..." autocomplete="off">$items</div>
$panels
</section>
<script>
var tabs = document.querySelectorAll("[data-tab]");
function showTab(name) {
  tabs.forEach(function (t) { t.classList.toggle("active", t.dataset.tab === name); });
  document.querySelectorAll(".tab-panel").forEach(function (p) { p.hidden = p.id !== name; });
}
function showConversation(id) {
  document.querySelectorAll(".conversation").forEach(function (c) { c.hidden = c.id !== id; });
  document.querySelectorAll(".session-item").forEach(function (i) {
    i.classList.toggle("active", i.getAttribute("href") === "#" + id);
  });
}
function route() {
  var id = decodeURIComponent(location.hash.slice(1));
  if (id.indexOf("c-") === 0 && document.getElementById(id)) { showTab("conversations"); showConversation(id); }
}
tabs.forEach(function (t) { t.addEventListener("click", function () { showTab(t.dataset.tab); }); });
document.getElementById("filter").addEventListener("input", function (e) {
  var q = e.target.value.toLowerCase();
  document.querySelectorAll(".session-item").forEach(function (i) { i.hidden = i.textContent.toLowerCase().indexOf(q) < 0; });
});
window.addEventListener("hashchange", route);
var first = document.querySelector(".session-item");
if (first) { showConversation(first.getAttribute("href").slice(1)); }
route();
</script>
</body>
</html>
""")
