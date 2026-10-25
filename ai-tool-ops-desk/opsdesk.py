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
import urllib.parse
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


def load_requests(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def triage_all(raws: list[dict], inventory: list[dict], today: date) -> list[Ticket]:
    out = []
    for raw in raws:
        t = Ticket(id=raw["id"], created=raw["created"], requester_team=raw["team"], text=raw["text"])
        out.append(triage_llm(triage_rules(t, inventory, today)))
    return out


def fetch_gitlab_issues(project: str, base: str | None = None) -> list[dict]:
    """Read open issues from a GitLab project (read-only). Needs GITLAB_TOKEN with read_api."""
    base = (base or os.environ.get("GITLAB_URL", "https://gitlab.com")).rstrip("/")
    url = f"{base}/api/v4/projects/{urllib.parse.quote(project, safe='')}/issues?state=opened&per_page=100"
    req = urllib.request.Request(url, headers={"PRIVATE-TOKEN": os.environ.get("GITLAB_TOKEN", "")})
    with urllib.request.urlopen(req, timeout=20) as r:
        issues = json.load(r)
    return [{"id": f"#{i['iid']}", "iid": i["iid"], "created": i["created_at"][:10],
             "team": (i.get("author") or {}).get("username", "unknown"),
             "text": f"{i['title']}. {i.get('description') or ''}"} for i in issues]


def label_calls(tickets: list[Ticket], project: str, base: str | None = None) -> list[dict]:
    """The API calls that would apply the suggested labels. Printed in dry-run mode."""
    base = (base or os.environ.get("GITLAB_URL", "https://gitlab.com")).rstrip("/")
    calls = []
    for t in tickets:
        if t.id.startswith("#"):
            calls.append({"method": "PUT",
                          "url": f"{base}/api/v4/projects/{urllib.parse.quote(project, safe='')}/issues/{t.id[1:]}",
                          "body": {"add_labels": ",".join(t.labels)}})
    return calls


def apply_labels(calls: list[dict]) -> None:
    for c in calls:
        req = urllib.request.Request(c["url"], method="PUT", data=json.dumps(c["body"]).encode(),
                                     headers={"PRIVATE-TOKEN": os.environ["GITLAB_TOKEN"], "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).close()


def prompt_health(path: Path, today: date, stale_days: int = 90) -> list[dict]:
    """'Prompts are process': every shared prompt or agent needs an owner, a version and a recent review."""
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            issues = []
            if not r["owner"].strip():
                issues.append("no owner")
            if not r["version"].strip():
                issues.append("unversioned")
            age = (today - date.fromisoformat(r["last_reviewed"])).days if r["last_reviewed"] else None
            if age is None or age > stale_days:
                issues.append("review overdue" if age else "never reviewed")
            rows.append({**r, "status": "ok" if not issues else ", ".join(issues)})
    return rows


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


QUEUES = {
    "access_request": "Access desk",
    "issue": "Tool owner on-call",
    "use_case": "Use-case backlog",
    "data_hygiene": "Data hygiene queue",
    "governance": "Data governance review",
    "how_to": "Champions + prompt library",
    "other": "Ops triage (human)",
}


def route(t: Ticket, inventory: list[dict], adopt: list[dict] | None = None) -> dict:
    """Where a triaged ticket goes, who owns it, and the first reply the requester sees."""
    owners = {r["tool"]: r["owner"] for r in inventory}
    free = {a["tool"]: a["seats"] - a["active"] for a in (adopt or [])}
    tool = t.tools[0] if t.tools else None
    dri = owners.get(tool, "Ops triage") if tool else "Ops triage"
    if t.category == "governance" or "review::data-governance" in t.labels:
        queue, dri = QUEUES["governance"], "Privacy + Enterprise AI"
        reply = "Thanks for checking first. Please hold off until data governance replies on this issue."
    elif t.category == "access_request" and tool:
        queue = QUEUES["access_request"]
        spare = free.get(tool)
        reply = (f"Seat request logged. {tool} has {spare} unused seats, so this is a quick approval from {dri}."
                 if spare and spare > 0 else f"Seat request logged. {tool} is at capacity, so {dri} will check renewal options.")
    elif t.category == "issue":
        queue = QUEUES["issue"]
        reply = f"Logged as {t.priority}. {dri} is on it" + (" now." if t.priority == "P1" else " and will update this issue.")
    elif t.category == "use_case":
        queue, dri = QUEUES["use_case"], "Enablement"
        reply = "Great idea. It is in the use-case backlog; add the prompt or template you would start from."
    elif t.category == "how_to":
        queue, dri = QUEUES["how_to"], "AI champions"
        reply = "Here is the prompt library entry and the champions channel. Someone will reply with an example."
    elif t.category == "data_hygiene":
        queue = QUEUES["data_hygiene"]
        reply = "Thanks. The owner will merge duplicates and mark one source of truth."
    else:
        queue, dri = QUEUES["other"], "Ops triage"
        reply = "Thanks. A person will take a look and ask a follow-up question if needed."
    return {"queue": queue, "dri": dri, "reply": reply}


def savings(inventory: list[dict], adopt: list[dict], headroom: float = 1.25) -> dict:
    """Annual savings from consolidating overlapping tools and right-sizing underused seats."""
    inv = {r["tool"]: r for r in inventory}
    act = {a["tool"]: a for a in adopt}
    cost = lambda tool: float(inv[tool].get("cost_per_seat_month") or 0)  # noqa: E731
    moves, dropped = [], set()
    for cap, tools in overlaps(inventory):
        keep = max(tools, key=lambda x: act.get(x, {}).get("active", 0))
        for d in tools:
            if d == keep:
                continue
            dropped.add(d)
            need = act[keep]["active"] + act[d]["active"]
            extra = max(0, need - int(inv[keep]["seats"]))
            amount = int(inv[d]["seats"]) * cost(d) * 12 - extra * cost(keep) * 12
            moves.append({"kind": "consolidate", "capability": cap, "drop": d, "keep": keep, "annual": round(amount)})
    for a in adopt:
        if a["flag"] == "underused" and a["tool"] not in dropped and a["tool"] not in [m.get("keep") for m in moves]:
            target = max(1, -(-int(a["active"] * headroom * 100) // 100))
            free = a["seats"] - target
            if free > 0:
                moves.append({"kind": "right-size", "tool": a["tool"], "from": a["seats"], "to": target, "active": a["active"],
                              "annual": round(free * cost(a["tool"]) * 12)})
    spend = sum(int(r["seats"]) * cost(r["tool"]) * 12 for r in inventory)
    return {"annual_spend": round(spend), "annual_savings": sum(m["annual"] for m in moves), "moves": moves}


def usage_history(usage_path: Path) -> dict[str, list[dict]]:
    hist: dict[str, list[dict]] = defaultdict(list)
    with open(usage_path, newline="") as f:
        for row in csv.DictReader(f):
            hist[row["tool"]].append({"week": row["week"], "active": int(row["weekly_active_users"])})
    return dict(hist)


def site_data(here: Path | None = None, today: date | None = None) -> dict:
    """Everything the static demo site needs, built from the same sample files and rules as the CLI."""
    here = here or Path(__file__).parent
    today = today or date(2026, 10, 25)
    inv = load_inventory(here / "data/inventory.csv")
    tickets = triage_all(load_requests(here / "data/requests.jsonl"), inv, today)
    adopt = adoption(here / "data/usage.csv", inv)
    ov = overlaps(inv)
    prompts = prompt_health(here / "data/prompts.csv", today)
    with __import__("tempfile").TemporaryDirectory() as d:
        write_async_update(tickets, adopt, ov, prompts, Path(d), today, inv)
        update = (Path(d) / "async-update.md").read_text()
    for p in prompts:
        p["age_days"] = (today - date.fromisoformat(p["last_reviewed"])).days if p["last_reviewed"] else None
    return {
        "today": today.isoformat(),
        "sample": True,
        "rules": {"categories": CATEGORIES, "urgent": URGENT, "sensitive": SENSITIVE, "queues": QUEUES},
        "inventory": inv,
        "requests": [{**asdict(t), **route(t, inv, adopt)} for t in tickets],
        "adoption": adopt,
        "history": usage_history(here / "data/usage.csv"),
        "overlaps": [{"capability": c, "tools": t} for c, t in ov],
        "savings": savings(inv, adopt),
        "prompts": prompts,
        "async_update": update,
    }


def _n(n: int, word: str, plural: str | None = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def write_async_update(tickets, adopt, ov, prompts, outdir: Path, today: date, inventory: list[dict] | None = None) -> None:
    """A weekly update written the way a handbook page reads: short, linkable, decisions up top."""
    p1 = [t for t in tickets if t.priority == "P1"]
    review = [t for t in tickets if t.needs_review]
    under = [a for a in adopt if a["flag"] == "underused"]
    bad_prompts = [p for p in prompts if p["status"] != "ok"]
    owners = {r["tool"]: r["owner"] for r in inventory or []}
    sav = savings(inventory, adopt) if inventory and all("cost_per_seat_month" in r for r in inventory) else None
    L = [f"# AI tooling ops: weekly async update ({today.isoformat()})", "",
         "## TL;DR", "",
         f"- {_n(len(tickets), 'open request')}; {len(p1)} P1, {len(review)} need a human decision.",
         f"- {_n(len(under), 'tool')} under 40% seat use: {', '.join(a['tool'] for a in under) or 'none'}.",
         f"- {_n(len(ov), 'consolidation candidate')}; {_n(len(bad_prompts), 'shared prompt or agent', 'shared prompts or agents')} need an owner or review."]
    if sav and sav["annual_savings"]:
        L.append(f"- About ${sav['annual_savings']:,} a year in seat savings available at renewal, and everyone keeps a tool that does the job.")
    L += ["", "## Decisions needed (async, comment by Friday)", ""]
    n = 1
    moves = {m["capability"]: m for m in (sav or {}).get("moves", []) if m["kind"] == "consolidate"}
    for cap, tools in ov:
        m = moves.get(cap)
        if m:
            L.append(f"{n}. **Consolidate {cap}?** {', '.join(tools)} overlap. Proposal: keep {m['keep']} (more active users, "
                     f"enough spare seats) and move {m['drop']} users over at renewal, saving about ${m['annual']:,} a year. "
                     f"DRI: {owners.get(m['drop'], '[Tool owner]')} ({m['drop']} owner). Two-way door.")
        else:
            L.append(f"{n}. **Consolidate {cap}?** {', '.join(tools)} overlap. Proposal: keep the higher-use tool, "
                     "move seats over at renewal. DRI: [Tool owner]. Two-way door.")
        n += 1
    for t in review:
        if t.category == "governance":
            L.append(f"{n}. **{t.id}: {t.summary}** Needs a data-governance answer before anyone proceeds. DRI: Privacy + Enterprise AI.")
            n += 1
    if n == 1:
        L.append("- None this week.")
    L += ["", "## Queue (top first)", "", "| Issue | Priority | Type | Summary |", "|---|---|---|---|"]
    L += [f"| {t.id} | {t.priority} | {t.category} | {t.summary} |"
          for t in sorted(tickets, key=lambda t: (t.priority, t.created))]
    L += ["", "## Adoption", ""]
    L += [f"- **{a['tool']}**: {a['active']}/{a['seats']} seats active ({int(a['utilization']*100)}%, {a['trend']:+d} over the period), {a['flag']}." for a in adopt]
    L += ["", "## Prompt and agent registry", "", "Prompts are process: shared, versioned, owned.", ""]
    L += [f"- {p['name']} (owner: {p['owner'] or 'none'}, v{p['version'] or '?'}): {p['status']}" for p in prompts]
    grow = sorted([a for a in adopt if a["trend"] > 0], key=lambda a: -a["trend"])
    if grow:
        g = grow[0]
        L += ["", "## Rollout announcement draft (for the team channel)", "",
              f"> {g['tool']} is now used by {g['active']} people each week, up {g['trend']} this month. "
              "If you have not tried it yet, the prompt library has ready-to-use starters for account plans and renewal briefs. "
              "Questions go in the champions channel, and seat requests take one short form."]
    L += ["", "## Next iteration", "",
          "- Smallest next step: auto-comment on aging access requests with what's missing.",
          "- Then: pull seat counts from the license source instead of a CSV.", "",
          "_Generated by opsdesk.py from sample data. Edit freely; everything is in draft._"]
    (outdir / "async-update.md").write_text("\n".join(L) + "\n")


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
    ap.add_argument("--prompts", default=here / "data/prompts.csv", type=Path)
    ap.add_argument("--today", default=None, help="YYYY-MM-DD, for repeatable runs")
    ap.add_argument("--gitlab-project", help="Read open issues from this GitLab project (id or group/name) instead of the sample file")
    ap.add_argument("--apply-labels", action="store_true", help="Actually write labels back to GitLab (default is a dry run)")
    a = ap.parse_args(argv)
    today = date.fromisoformat(a.today) if a.today else date.today()
    inv = load_inventory(a.inventory)
    raws = fetch_gitlab_issues(a.gitlab_project) if a.gitlab_project else load_requests(a.tickets)
    tickets = triage_all(raws, inv, today)
    adopt = adoption(a.usage, inv)
    ov = overlaps(inv)
    write_outputs(tickets, adopt, ov, a.out, today)
    write_async_update(tickets, adopt, ov, prompt_health(a.prompts, today), a.out, today, inv)
    if a.gitlab_project:
        calls = label_calls(tickets, a.gitlab_project)
        if a.apply_labels:
            apply_labels(calls)
            print(f"Applied labels to {len(calls)} issues")
        else:
            (a.out / "label-plan.json").write_text(json.dumps(calls, indent=2))
            print(f"Dry run: {len(calls)} label updates planned -> {a.out}/label-plan.json")
    print(f"Triaged {len(tickets)} requests -> {a.out}/async-update.md, report.md, dashboard.html, triaged.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
