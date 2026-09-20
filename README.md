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
.venv/bin/python -m unittest \
  test_adaptive_watcher.py \
  test_historical_profile_v01.py \
  test_owner_review.py \
  test_review_trigger.py \
  test_owner_review_api.py
```

## Secrets and runtime data

This repository intentionally excludes production credentials, Owner sessions,
signing secrets, Slack tokens, Steam refresh tokens, match caches, parsed match
data, uploaded artifacts, databases, and build output. Configure deployments with
server-side environment files and protected secret files. Never expose those
values to the browser.
