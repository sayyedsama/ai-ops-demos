# AI ops demos

Two small, working samples of AI operations work, built from scratch with AI coding tools.
Standard-library Python, no API keys, tests included. All data is made up.

| Demo | What it shows |
|---|---|
| [ai-tool-ops-desk](ai-tool-ops-desk/) | Triage requests for a company's AI tools (access, issues, ideas, data clean-up, governance), label and prioritize them, track adoption vs seats, spot overlapping tools, and write a report plus an HTML dashboard. |
| [intake-webhook-flow](intake-webhook-flow/) | Form or webhook in, classify, append a spreadsheet row, send a notification. The n8n/Zapier pattern in one readable file. |

Both run on rules alone and can optionally ask a local or hosted LLM to refine the result,
falling back to rules if the model fails.

```bash
cd ai-tool-ops-desk && python3 opsdesk.py --today 2026-10-25 && python3 -m unittest discover -s tests
cd ../intake-webhook-flow && python3 -m unittest discover -s tests
```

MIT licensed.
