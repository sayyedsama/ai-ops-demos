# Intake Webhook Flow

A form or webhook comes in, gets classified, lands as a row in a spreadsheet, and pings a channel.
It's the same shape as a common n8n or Zapier flow, written as one small Python file so every
step is visible and testable.

```
Form / webhook  ->  classify (rules, optional LLM)  ->  sheet.csv row  ->  notification
   POST /intake         Access | Bug | Idea |          Google-Sheets-       out/notifications.jsonl
                        Question | Other + urgency     style columns        (+ Slack webhook if set)
```

Built from scratch as a portfolio sample. No real data.

## Run it

Python 3.10+ only. No installs, no API key.

```bash
python3 flow.py                 # starts on http://127.0.0.1:8787
# open http://127.0.0.1:8787 for the form, or in a second terminal:
python3 send_samples.py         # posts 4 sample requests like a webhook would
cat out/sheet.csv
cat out/notifications.jsonl
python3 -m unittest discover -s tests -v
```

### As a GitLab project webhook

Point a project webhook (Settings > Webhooks, "Issues events") at `/intake`. The flow reads the
`Issue Hook` event, checks `X-Gitlab-Token` against `GITLAB_WEBHOOK_SECRET` if you set one, and
logs the issue with its source (`gitlab:group/project#iid`). Try it locally:

```bash
curl -X POST http://127.0.0.1:8787/intake -H 'Content-Type: application/json' \
  -H 'X-Gitlab-Event: Issue Hook' -d @sample_gitlab_issue_hook.json
```

Each row also gets a `route_to` owner (IT for access, the tool owner for bugs, the function's AI
owner for ideas, champions/docs for questions, a human for anything unclear), the hub-and-spoke
pattern of a central platform team plus owners inside each function.

Webhook call by hand:

```bash
curl -X POST http://127.0.0.1:8787/intake -H 'Content-Type: application/json' \
  -d '{"name":"Avery","team":"Sales","message":"Need a seat for the notes tool today"}'
```

## Options (all off by default)

| Variable | What it does |
|---|---|
| `FLOW_LLM_URL`, `FLOW_LLM_MODEL` | Any OpenAI-compatible endpoint, e.g. free local Ollama at `http://localhost:11434/v1/chat/completions`. Refines category, urgency and summary. Falls back to rules on any error. |
| `GITLAB_WEBHOOK_SECRET` | Rejects GitLab webhook calls whose `X-Gitlab-Token` doesn't match. |
| `NOTIFY_WEBHOOK_URL` | Also posts each notification to a Slack-compatible incoming webhook. |
| `FLOW_OUT` | Where `sheet.csv` and `notifications.jsonl` go (default `out/`). |
| `PORT` | Server port (default 8787). |

## Design notes

- **Low-confidence and "Other" rows are marked `needs review`** so a person checks them.
- **Email addresses are not stored** in the sheet and are redacted before any LLM call.
- **Swapping the CSV for Google Sheets** is one function (`append_row`); the rest stays the same.
