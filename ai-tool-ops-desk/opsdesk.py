"""AI Tool Ops Desk: triage AI-tool requests and report on adoption.

Rules-first (no API key needed). An optional LLM hook can refine the summary
and category when OPSDESK_LLM_URL is set (any OpenAI-compatible endpoint,
for example a local Ollama server). If the LLM call fails, the rules result stands.
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from pathlib import Path

CATEGORIES = {
    "access_request": [r"\baccess\b", r"\bseat\b", r"\blicen[cs]e\b", r"\bprovision", r"\binvite\b", r"\bcan i get\b", r"\bneed (a|an) (account|login)\b"],
    "issue": [r"\bbroken\b", r"\berror\b", r"\bnot working\b", r"\bfails?\b", r"\bcan'?t log ?in\b", r"\bbug\b", r"\bdown\b", r"\bslow\b"],
    "use_case": [r"\buse case\b", r"\bidea\b", r"\bcould we\b", r"\bautomate\b", r"\bworkflow\b", r"\bprompt\b", r"\bagent\b", r"\btemplate\b"],
    "data_hygiene": [r"\bduplicate\b", r"\boutdated\b", r"\bstale\b", r"\bdataset\b", r"\bdrive\b", r"\bfolder\b", r"\bclean ?up\b", r"\bwrong version\b"],
    "governance": [r"\bis it ok\b", r"\ballowed\b", r"\bapproved\b", r"\bpolicy\b", r"\bcustomer data\b", r"\bexport\b"],
    "how_to": [r"\bhow do i\b", r"\bhow to\b", r"\bwhere (is|do)\b", r"\btraining\b", r"\bguide\b", r"\bwhat is the best\b"],
}
URGENT = [r"\burgent\b", r"\basap\b", r"\bcustomer (call|meeting|demo)\b", r"\btoday\b", r"\bblock(ed|ing)\b", r"\bdeal\b"]
SENSITIVE = [r"\bcustomer data\b", r"\bpii\b", r"\bconfidential\b", r"\bexport\b", r"\bpersonal data\b"]
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


@dataclass
class Ticket:
    id: str
    created: str
    requester_team: str
    text: str
    category: str = "other"
    priority: str = "P3"
    tools: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    summary: str = ""
    needs_review: bool = False
    engine: str = "rules"


def load_inventory(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def redact(text: str) -> str:
    return EMAIL_RE.sub("[email]", text)


def detect_tools(text: str, inventory: list[dict]) -> list[str]:
    low = text.lower()
    found = []
    for row in inventory:
        names = [row["tool"]] + [a for a in row.get("aliases", "").split("|") if a]
        if any(re.search(r"\b" + re.escape(n.lower()) + r"\b", low) for n in names):
            found.append(row["tool"])
    return found


def score(text: str, patterns: list[str]) -> int:
    return sum(1 for p in patterns if re.search(p, text, re.I))


def summarize(text: str, limit: int = 90) -> str:
    first = re.split(r"(?<=[.!?])\s", text.strip())[0]
    return first if len(first) <= limit else first[: limit - 3].rstrip() + "..."


def triage_rules(t: Ticket, inventory: list[dict], today: date) -> Ticket:
    text = t.text
    scores = {c: score(text, pats) for c, pats in CATEGORIES.items()}
    best = max(scores, key=scores.get)
    t.category = best if scores[best] > 0 else "other"
    t.tools = detect_tools(text, inventory)
    urgent = score(text, URGENT) > 0
    age = (today - datetime.fromisoformat(t.created).date()).days
    if t.category == "issue" and urgent:
        t.priority = "P1"
    elif urgent or (t.category == "access_request" and age >= 2) or t.category in ("issue", "governance"):
        t.priority = "P2"
    t.labels = [f"type::{t.category}", f"priority::{t.priority}"] + [f"tool::{x}" for x in t.tools]
    if score(text, SENSITIVE):
        t.labels.append("review::data-governance")
        t.needs_review = True
    if age >= 3:
        t.labels.append("sla::aging")
    if t.category == "other" or not t.tools:
        t.needs_review = True
    t.summary = summarize(redact(text))
    return t


def triage_llm(t: Ticket) -> Ticket:
    """Optional: ask an OpenAI-compatible endpoint to refine category and summary."""
    url = os.environ.get("OPSDESK_LLM_URL")
    if not url:
        return t
    prompt = (
        "Classify this internal AI-tool request into one of "
        f"{list(CATEGORIES) + ['other']} and write a one-line summary under 90 chars. "
        'Reply as JSON: {"category": "...", "summary": "..."}\n\n' + redact(t.text)
    )
    body = json.dumps({
        "model": os.environ.get("OPSDESK_LLM_MODEL", "llama3.2"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if os.environ.get("OPSDESK_LLM_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["OPSDESK_LLM_KEY"]
    try:
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            content = json.load(r)["choices"][0]["message"]["content"]
        data = json.loads(content[content.find("{"): content.rfind("}") + 1])
        if data.get("category") in list(CATEGORIES) + ["other"]:
            t.category = data["category"]
            t.labels = [l for l in t.labels if not l.startswith("type::")] + [f"type::{t.category}"]
        if data.get("summary"):
            t.summary = str(data["summary"])[:90]
        t.engine = "rules+llm"
    except Exception as e:  # fall back quietly to the rules result
        print(f"[llm] {t.id}: falling back to rules ({e.__class__.__name__})", file=sys.stderr)
    return t


def triage_all(tickets_path: Path, inventory: list[dict], today: date) -> list[Ticket]:
    out = []
    for line in tickets_path.read_text().splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        t = Ticket(id=raw["id"], created=raw["created"], requester_team=raw["team"], text=raw["text"])
        out.append(triage_llm(triage_rules(t, inventory, today)))
    return out


def adoption(usage_path: Path, inventory: list[dict]) -> list[dict]:
    seats = {r["tool"]: int(r["seats"]) for r in inventory}
    latest: dict[str, dict] = {}
    history: dict[str, list[int]] = defaultdict(list)
    with open(usage_path, newline="") as f:
        for row in csv.DictReader(f):
            history[row["tool"]].append(int(row["weekly_active_users"]))
            latest[row["tool"]] = row
    rows = []
    for tool, row in latest.items():
        active = int(row["weekly_active_users"])
        s = seats.get(tool, 0) or 1
        h = history[tool]
        trend = h[-1] - h[0] if len(h) > 1 else 0
        rate = active / s
        rows.append({
            "tool": tool, "seats": s, "active": active, "utilization": round(rate, 2),
            "trend": trend, "flag": "underused" if rate < 0.4 else ("watch" if rate < 0.6 else "healthy"),
        })
    return sorted(rows, key=lambda r: r["utilization"])


def overlaps(inventory: list[dict]) -> list[tuple[str, list[str]]]:
    by_cap: dict[str, list[str]] = defaultdict(list)
    for r in inventory:
        by_cap[r["capability"]].append(r["tool"])
    return [(cap, tools) for cap, tools in sorted(by_cap.items()) if len(tools) > 1]


def write_outputs(tickets: list[Ticket], adopt: list[dict], ov: list, outdir: Path, today: date) -> None:
    outdir.mkdir(parents=True, exist_ok=True)
    with open(outdir / "triaged.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "created", "team", "category", "priority", "tools", "labels", "needs_review", "summary", "engine"])
        for t in tickets:
            w.writerow([t.id, t.created, t.requester_team, t.category, t.priority, "|".join(t.tools),
                        " ".join(t.labels), t.needs_review, t.summary, t.engine])
    (outdir / "triaged.json").write_text(json.dumps([asdict(t) for t in tickets], indent=2))

    cats = Counter(t.category for t in tickets)
    pri = Counter(t.priority for t in tickets)
    md = [f"# AI Tool Ops report ({today.isoformat()})", "",
          f"**Queue:** {len(tickets)} requests. P1: {pri.get('P1', 0)}, P2: {pri.get('P2', 0)}, P3: {pri.get('P3', 0)}. "
          f"Needs a human look: {sum(t.needs_review for t in tickets)}.", "",
          "## By type", ""] + [f"- {c}: {n}" for c, n in cats.most_common()] + [
          "", "## Top of queue", "", "| ID | Priority | Type | Tools | Summary |", "|---|---|---|---|---|"]
    order = sorted(tickets, key=lambda t: (t.priority, t.created))
    md += [f"| {t.id} | {t.priority} | {t.category} | {', '.join(t.tools) or '-'} | {t.summary} |" for t in order]
    md += ["", "## Adoption (latest week)", "", "| Tool | Active / seats | Utilization | Trend | Flag |", "|---|---|---|---|---|"]
    md += [f"| {a['tool']} | {a['active']} / {a['seats']} | {int(a['utilization']*100)}% | {a['trend']:+d} | {a['flag']} |" for a in adopt]
    md += ["", "## Consolidation candidates (same capability, more than one tool)", ""]
    md += [f"- {cap}: {', '.join(tools)}" for cap, tools in ov] or ["- none"]
    (outdir / "report.md").write_text("\n".join(md) + "\n")

    def bar(p: float) -> str:
        return f'<div class="bar"><span style="width:{min(p,1)*100:.0f}%"></span></div>'
    rows = "".join(
        f"<tr class='{a['flag']}'><td>{html.escape(a['tool'])}</td><td>{a['active']} / {a['seats']}</td>"
        f"<td>{bar(a['utilization'])} {int(a['utilization']*100)}%</td><td>{a['trend']:+d}</td><td>{a['flag']}</td></tr>" for a in adopt)
    trows = "".join(
        f"<tr><td>{t.id}</td><td class='p{t.priority[-1]}'>{t.priority}</td><td>{t.category}</td>"
        f"<td>{html.escape(', '.join(t.tools) or '-')}</td><td>{html.escape(t.summary)}</td>"
        f"<td>{'yes' if t.needs_review else ''}</td></tr>" for t in order)
    cards = "".join(f"<div class='card'><b>{n}</b><span>{c}</span></div>" for c, n in cats.most_common())
    ovh = "".join(f"<li><b>{html.escape(c)}</b>: {html.escape(', '.join(t))}</li>" for c, t in ov) or "<li>none</li>"
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>AI Tool Ops Desk</title>
<style>body{{font:14px system-ui,sans-serif;margin:32px;color:#1f1f1f;max-width:1000px}}h1{{margin:0 0 4px}}
.sub{{color:#666;margin-bottom:20px}}.cards{{display:flex;gap:12px;flex-wrap:wrap}}.card{{border:1px solid #ddd;border-radius:8px;padding:10px 14px}}
.card b{{display:block;font-size:22px}}table{{border-collapse:collapse;width:100%;margin:12px 0 24px}}td,th{{border-bottom:1px solid #eee;padding:6px 8px;text-align:left}}
.bar{{display:inline-block;width:120px;height:8px;background:#eee;border-radius:4px;vertical-align:middle}}.bar span{{display:block;height:8px;background:#6b4fbb;border-radius:4px}}
.underused td{{background:#fff4f0}}.p1{{color:#c0392b;font-weight:600}}.p2{{color:#b9770e;font-weight:600}}</style></head><body>
<h1>AI Tool Ops Desk</h1><div class="sub">Generated {today.isoformat()} from sample data. {len(tickets)} requests, {sum(t.needs_review for t in tickets)} flagged for a human look.</div>
<div class="cards">{cards}</div><h2>Queue</h2><table><tr><th>ID</th><th>Pri</th><th>Type</th><th>Tools</th><th>Summary</th><th>Review</th></tr>{trows}</table>
<h2>Adoption (latest week)</h2><table><tr><th>Tool</th><th>Active / seats</th><th>Utilization</th><th>Trend</th><th>Flag</th></tr>{rows}</table>
<h2>Consolidation candidates</h2><ul>{ovh}</ul></body></html>"""
    (outdir / "dashboard.html").write_text(page)


def main(argv: list[str] | None = None) -> int:
    import argparse
    here = Path(__file__).parent
    ap = argparse.ArgumentParser(description="Triage AI-tool requests and build an adoption report.")
    ap.add_argument("--tickets", default=here / "data/requests.jsonl", type=Path)
    ap.add_argument("--inventory", default=here / "data/inventory.csv", type=Path)
    ap.add_argument("--usage", default=here / "data/usage.csv", type=Path)
    ap.add_argument("--out", default=here / "out", type=Path)
    ap.add_argument("--today", default=None, help="YYYY-MM-DD, for repeatable runs")
    a = ap.parse_args(argv)
    today = date.fromisoformat(a.today) if a.today else date.today()
    inv = load_inventory(a.inventory)
    tickets = triage_all(a.tickets, inv, today)
    adopt = adoption(a.usage, inv)
    write_outputs(tickets, adopt, overlaps(inv), a.out, today)
    print(f"Triaged {len(tickets)} requests -> {a.out}/report.md, dashboard.html, triaged.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
