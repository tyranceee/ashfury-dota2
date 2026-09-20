# Ashfury Dota2

Private source repository for the Dota 2 section of `ashfury.cn`.

## Repository layout

- `frontend/` — React/Vite match archive, historical player profile, owner authorization, and review-job UI.
- `backend/` — Python REST API, adaptive match monitor, historical profile model, artifact endpoints, owner sessions, and deep-review job support.
- `valve-monitor/` — experimental Valve/GC monitor and its tests.
- `docs/` — data-boundary, DotaReplayDesk integration, historical-profile, and owner-review design notes.

## Local checks

Frontend:

```bash
cd frontend
npm ci
npm run build
npm run test:sites
```

Backend:

```bash
cd backend
python -m venv .venv
.venv/bin/pip install fastapi httpx uvicorn
.venv/bin/python -m unittest discover -p "test_*.py"
```

## Authorized downloads and DeepSeek preliminary review

The API serves scoped, audited `Bearer` tokens (in addition to the Owner browser
session) so an authorized terminal can download parsed match JSON and generated
preliminary reviews. Preliminary reviews run on DeepSeek **only inside the
off-peak billing window** (weekdays 01:00-04:00 and 06:00-10:00 UTC are peak;
everything else, plus weekends and Chinese public holidays, is off-peak at half
price). Auto review covers only the **three most recently parsed matches**.

DeepSeek's native `chat/completions` and `responses` transports do not execute
search, but its Anthropic-compatible transport does: `POST
/anthropic/v1/messages` with the `web_search_20250305` server tool returns real
`web_search_tool_result` blocks. That hosted search is the default provider and
needs no third-party search account. Providers are swappable
(`deepseek-hosted-search`, `tavily`, `brave`, `searxng`, `custom`), queries are
sent verbatim and rejected if the service rewrites them, and results pass URL
safety, allowed-domain, duplicate, and source-independence checks before use. See [docs/terminal-download-and-deepseek-review.md](docs/terminal-download-and-deepseek-review.md)
and the paste-ready [prompt template](docs/preliminary-review-prompt-template.md).

## Secrets and runtime data

This repository intentionally excludes production credentials, Owner sessions,
signing secrets, Slack tokens, Steam refresh tokens, match caches, parsed match
data, uploaded artifacts, databases, and build output. Configure deployments with
server-side environment files and protected secret files. Never expose those
values to the browser.
