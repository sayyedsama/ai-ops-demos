"""Intake webhook flow: form or webhook -> classify -> sheet row -> notification.

The same shape as an n8n workflow (Webhook -> AI/Rules node -> Google Sheets -> Slack),
written as one small Python file using only the standard library.

  GET  /          a simple request form
  POST /intake    JSON or form-encoded {name, team, email, message}
  GET  /health    returns ok

Rows go to out/sheet.csv (Google-Sheets-style columns). Notifications go to
out/notifications.jsonl, and also to a Slack-compatible webhook if NOTIFY_WEBHOOK_URL is set.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import threading
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

OUT = Path(os.environ.get("FLOW_OUT", Path(__file__).parent / "out"))
COLUMNS = ["timestamp", "request_id", "name", "team", "category", "urgency", "confidence", "summary", "status", "engine"]
LOCK = threading.Lock()

RULES = {
    "Access": [r"\baccess\b", r"\bseat\b", r"\blicen[cs]e\b", r"\blogin\b", r"\baccount\b"],
    "Bug": [r"\berror\b", r"\bbroken\b", r"\bnot working\b", r"\bcrash", r"\bfail"],
    "Idea": [r"\bidea\b", r"\bautomate\b", r"\bcould we\b", r"\bwould be great\b", r"\bfeature\b"],
    "Question": [r"\bhow (do|can|to)\b", r"\bwhere\b", r"\bwhat\b", r"\?"],
}
URGENT = [r"\burgent\b", r"\basap\b", r"\btoday\b", r"\bblock", r"\bcustomer\b"]


def classify_rules(message: str) -> dict:
    scores = {c: sum(bool(re.search(p, message, re.I)) for p in pats) for c, pats in RULES.items()}
    total = sum(scores.values())
    cat = max(scores, key=scores.get) if total else "Other"
    conf = round(scores[cat] / total, 2) if total else 0.0
    urgency = "High" if any(re.search(p, message, re.I) for p in URGENT) else "Normal"
    first = re.split(r"(?<=[.!?])\s", message.strip())[0]
    return {"category": cat, "urgency": urgency, "confidence": conf,
            "summary": first[:100], "engine": "rules"}


def classify(message: str) -> dict:
    """Rules first; optional OpenAI-compatible LLM (e.g. local Ollama) when FLOW_LLM_URL is set."""
    result = classify_rules(message)
    url = os.environ.get("FLOW_LLM_URL")
    if not url:
        return result
    prompt = ("Classify this internal request as one of Access, Bug, Idea, Question, Other, with urgency "
              'High or Normal and a summary under 100 chars. Reply JSON: {"category":"","urgency":"","summary":""}\n\n'
              + re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", message))
    body = json.dumps({"model": os.environ.get("FLOW_LLM_MODEL", "llama3.2"),
                       "messages": [{"role": "user", "content": prompt}], "temperature": 0}).encode()
    try:
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            text = json.load(r)["choices"][0]["message"]["content"]
        data = json.loads(text[text.find("{"): text.rfind("}") + 1])
        if data.get("category") in list(RULES) + ["Other"]:
            result.update(category=data["category"], confidence=0.9)
        if data.get("urgency") in ("High", "Normal"):
            result["urgency"] = data["urgency"]
        if data.get("summary"):
            result["summary"] = str(data["summary"])[:100]
        result["engine"] = "rules+llm"
    except Exception as e:
        print(f"[llm] fallback to rules: {e.__class__.__name__}", file=sys.stderr)
    return result


def append_row(row: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "sheet.csv"
    with LOCK:
        new = not path.exists()
        with open(path, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            if new:
                w.writeheader()
            w.writerow({k: row.get(k, "") for k in COLUMNS})


def notify(row: dict) -> dict:
    icon = ":rotating_light:" if row["urgency"] == "High" else ":inbox_tray:"
    msg = {"text": f"{icon} New {row['category']} request from {row['team']} ({row['request_id']}): {row['summary']}"}
    OUT.mkdir(parents=True, exist_ok=True)
    with LOCK, open(OUT / "notifications.jsonl", "a") as f:
        f.write(json.dumps(msg) + "\n")
    hook = os.environ.get("NOTIFY_WEBHOOK_URL")
    if hook:
        try:
            req = urllib.request.Request(hook, data=json.dumps(msg).encode(), headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10).close()
        except Exception as e:
            print(f"[notify] webhook failed: {e.__class__.__name__}", file=sys.stderr)
    return msg


def process(payload: dict) -> dict:
    message = str(payload.get("message", "")).strip()
    if not message:
        raise ValueError("message is required")
    c = classify(message)
    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "request_id": "INT-" + uuid.uuid4().hex[:6].upper(),
        "name": str(payload.get("name", "")).strip()[:80] or "anonymous",
        "team": str(payload.get("team", "")).strip()[:80] or "unknown",
        "status": "needs review" if c["category"] == "Other" or c["confidence"] < 0.5 else "new",
        **c,
    }
    append_row(row)
    notify(row)
    return row


FORM = """<!doctype html><html><head><meta charset="utf-8"><title>Request intake</title>
<style>body{font:15px system-ui,sans-serif;max-width:520px;margin:40px auto}label{display:block;margin-top:12px}
input,textarea,select{width:100%;padding:8px;font:inherit}button{margin-top:16px;padding:10px 18px}</style></head>
<body><h1>Tool request</h1><form method="post" action="/intake">
<label>Name<input name="name"></label><label>Team<input name="team"></label>
<label>What do you need?<textarea name="message" rows="5" required></textarea></label>
<button>Send</button></form></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, ctype: str = "application/json") -> None:
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            self._send(200, FORM, "text/html; charset=utf-8")
        elif self.path == "/health":
            self._send(200, json.dumps({"ok": True}))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path != "/intake":
            return self._send(404, json.dumps({"error": "not found"}))
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0)).decode()
        try:
            if "json" in self.headers.get("Content-Type", ""):
                payload = json.loads(raw or "{}")
            else:
                payload = {k: v[0] for k, v in urllib.parse.parse_qs(raw).items()}
            row = process(payload)
        except (ValueError, json.JSONDecodeError) as e:
            return self._send(400, json.dumps({"error": str(e)}))
        if "json" in self.headers.get("Content-Type", ""):
            self._send(201, json.dumps(row))
        else:
            self._send(201, f"<p>Thanks. Logged as <b>{row['request_id']}</b> ({row['category']}).</p><a href='/'>Send another</a>",
                       "text/html; charset=utf-8")

    def log_message(self, fmt, *args):
        print("[http]", fmt % args, file=sys.stderr)


def serve(port: int = 8787) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8787))
    print(f"Listening on http://127.0.0.1:{port}  (form at /, webhook at POST /intake)")
    serve(port).serve_forever()
