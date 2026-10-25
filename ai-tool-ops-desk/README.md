# AI Tool Ops Desk

A small, working sample of the day-to-day work behind running a company's AI tools:
sort incoming requests, spot what needs a human, and show which tools people actually use.

Built from scratch as a portfolio sample. All data in `data/` is made up.

## What it does

1. **Triage requests** (`data/requests.jsonl`): access requests, broken tools, use-case ideas,
   data clean-up, how-to questions and "is this allowed?" governance questions.
   - Labels each one GitLab-style: `type::access_request`, `priority::P1`, `tool::Glean`, `sla::aging`, `review::data-governance`
   - Sets priority from urgency words, request type and age
   - Writes a one-line summary and hides email addresses
   - Flags anything unclear or sensitive for a human instead of guessing
2. **Track adoption** (`data/usage.csv` + `data/inventory.csv`): weekly active users vs seats,
   trend, and a flag (healthy, watch, underused).
3. **Find overlap**: tools that do the same job (consolidation candidates).
4. **Report**: `out/report.md` (paste into an issue), `out/dashboard.html` (open in a browser),
   `out/triaged.csv` (drop into a spreadsheet).

## Run it

Python 3.10+ only. No installs, no API key.

```bash
python3 opsdesk.py --today 2026-10-25
open out/dashboard.html   # or xdg-open on Linux
python3 -m unittest discover -s tests -v
```

## Optional LLM step

Rules run first and always work. To let a model refine the type and summary, point it at any
OpenAI-compatible endpoint, for example a free local [Ollama](https://ollama.com) server:

```bash
export OPSDESK_LLM_URL=http://localhost:11434/v1/chat/completions
export OPSDESK_LLM_MODEL=llama3.2
python3 opsdesk.py
```

If the model is down or replies with something odd, the rules result stays. Emails are
redacted before any text leaves the machine.

## Why it's built this way

- **Boring first:** keyword rules are easy to read, test and change. The model is an add-on, not a dependency.
- **Humans stay in the loop:** governance questions and unclear requests are flagged, not auto-answered.
- **Easy to iterate:** add a rule, a tool or a category in a few lines; tests cover each behavior.
