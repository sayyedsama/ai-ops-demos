# AI Tool Ops Desk

A working sample of the day-to-day work behind running a company's AI tools: sort incoming
requests, label them the GitLab way, show which tools people actually use, keep shared prompts
owned, and write it all up as a short async update that reads like a handbook page.

**Live demo:** https://sayyedsama.github.io/ai-ops-demos/ (the same rules running in the browser, see `../site/`).

Built from scratch as a portfolio sample. All data in `data/` is made up. Tool names in the sample
inventory are just realistic examples.

## What it does

1. **Triage requests** from a sample file or straight from a GitLab project's open issues:
   access requests, broken tools, use-case ideas, data clean-up, how-to questions and
   "is this allowed?" governance questions.
   - Scoped labels: `type::access_request`, `priority::P1`, `tool::Glean`, `sla::aging`, `review::data-governance`
   - Priority from urgency words, request type and age
   - One-line summary with email addresses hidden
   - Anything unclear or sensitive is flagged for a human instead of guessed
2. **Track adoption**: weekly active users vs seats, trend, and a flag (healthy, watch, underused).
3. **Find overlap and savings**: tools that do the same job become consolidation proposals, and
   underused seats get a right-size suggestion, both with an annual dollar figure.
4. **Check the prompt and agent registry**: every shared prompt, skill or agent should have an
   owner, a version and a recent review ("prompts are process").
5. **Write it up**:
   - `out/async-update.md`: TL;DR, decisions needed with a DRI, queue, adoption, registry, a rollout announcement draft, next iteration
   - `out/dashboard.html`: one-page dashboard (published by GitLab Pages in CI)
   - `out/report.md`, `out/triaged.csv`: for an issue comment or a spreadsheet

## Run it

Python 3.10+ only. No installs, no API key.

```bash
python3 opsdesk.py --today 2026-10-25
xdg-open out/dashboard.html      # or: open out/dashboard.html on a Mac
python3 -m unittest discover -s tests -v
```

## Against a real GitLab project

Read-only by default. Needs a token with `read_api`.

```bash
export GITLAB_TOKEN=...            # your own token, never committed
python3 opsdesk.py --gitlab-project my-group/ai-tool-requests
# -> out/label-plan.json shows exactly which labels it would add to which issue
python3 opsdesk.py --gitlab-project my-group/ai-tool-requests --apply-labels   # only when you're happy
```

Issue templates for access requests and use-case ideas are in `../.gitlab/issue_templates/`.

## Optional LLM step

Rules run first and always work. To let a model refine the type and summary, point it at any
OpenAI-compatible endpoint, for example a free local [Ollama](https://ollama.com) server:

```bash
export OPSDESK_LLM_URL=http://localhost:11434/v1/chat/completions
export OPSDESK_LLM_MODEL=llama3.2
python3 opsdesk.py
```

If the model is down or replies with something odd, the rules result stays. Emails are redacted
before any text leaves the machine.

## Why it's built this way

- **Boring solution first:** keyword rules are easy to read, test and change. The model is an add-on.
- **People stay the DRI:** governance questions and unclear requests go to a person, not an auto-reply.
- **Dry run before write:** the GitLab mode shows its plan before it touches any issue.
- **Small iterations:** each behavior has a test, so a new rule or category is a small merge request.
