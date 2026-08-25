# Loopedin channel automation

Fully-automatic pipeline: spot a trending topic, write an original commentary short
(AI voice + stock b-roll + captions + royalty-free music), upload it to YouTube as a
Short. Architecture mirrors the `bracketly` project's pattern: a scheduled **GitHub
Actions workflow** does the mechanical, secret-consuming work using GitHub's own
encrypted repo secrets and commits plain result files; a **Claude Code cloud routine**
only reads those files and does the actual reasoning (writing an original script),
then commits back. Neither ever needs to touch the other's territory, and no
credential ever needs to live anywhere but GitHub's secrets store.

- No real creator's footage/audio is ever reused — only a topic *seed* (title/category/
  view count) comes from trending videos; the script is always original.
- Safety rails: topic/script variety enforcement, synthetic-content disclosure on every
  upload, auto-pause after repeated failures (see `state/routine_health.json`).

## How the three pieces fit together

1. **`.github/workflows/trend-fetch.yml`** — runs 4x/day (as of 2026-08-25) on its own
   schedule, ~10 minutes before each Claude routine fire. Uses the `YOUTUBE_API_KEY`
   secret to find a trending-Shorts topic seed, writes `state/latest_trend_seed.json`,
   commits it. Also refreshes `state/live_stats.json` (near-real-time view/like/comment
   counts for recent uploads via `videos.list` — unlike Analytics data, this updates
   fast enough that checking it multiple times a day is actually useful, see
   `pipeline/live_stats.py`).
2. **Claude Code cloud routine** — fires shortly after, follows
   `ROUTINE_INSTRUCTIONS.md`: reads the seed + `state/performance_summary.md` +
   `state/used_topics.json`, writes an **original** `state/pending_script.json`,
   commits and pushes it. Needs no credentials at all.
3. **`.github/workflows/produce-upload.yml`** — triggered by that push. Uses
   `GOOGLE_TTS_CREDENTIALS_JSON`, `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`,
   `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` secrets to run
   narration → b-roll → assembly → upload, then records the result and clears the
   pending script.
4. **`.github/workflows/analytics-feedback.yml`** — once/day, timed to finish just
   before the first `trend-fetch.yml`/routine cycle of the day (23:55 UTC), pulls
   performance stats (`YOUTUBE_CLIENT_ID`/`SECRET`/`REFRESH_TOKEN` again) and writes
   `state/performance_summary.md` so every routine fire that day reads the freshest
   available snapshot. Running more than once/day wouldn't help — YouTube Analytics
   data itself only settles on a ~24-48h cycle. Also pushes a full channel + per-video
   snapshot (including a composite 0-100 `score_pct` per video — see
   `pipeline/analytics_feedback.py`'s `score_video()`) to the same car-loan-dashboard
   app the `bracketly` repo reports into, at `/youtube-status` (same ingest pattern as
   `bracketly`'s `status-check.yml` → `/api/bracketly-status/ingest`).

## One-time setup

### 1. Add GitHub Actions secrets

In the `Loopedin` repo on GitHub: **Settings → Secrets and variables → Actions → New
repository secret**. Add these exact names:

| Secret name | Value |
|---|---|
| `YOUTUBE_API_KEY` | YouTube Data API v3 key (trend search) |
| `YOUTUBE_CLIENT_ID` | OAuth client ID |
| `YOUTUBE_CLIENT_SECRET` | OAuth client secret |
| `YOUTUBE_REFRESH_TOKEN` | from `scripts/get_refresh_token.py` |
| `GOOGLE_TTS_CREDENTIALS_JSON` | Cloud TTS service-account JSON, full contents |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID -- required as of 2026-08-21. AI-generated images (`pipeline/ai_broll.py`, flux-1-schnell) are the sole b-roll source; there is no stock-footage fallback any more, so the B-roll step fails outright without this and the token below. |
| `CLOUDFLARE_API_TOKEN` | Cloudflare API token with Workers AI access -- see `CLOUDFLARE_ACCOUNT_ID` above. |
| `CLOUDFLARE_ACCOUNT_ID_FALLBACK` | Optional. Account ID for a second, genuinely separate Cloudflare account (different sign-up, not a second token on the same account -- the free 10,000 Neurons/day allowance is per-account). `pipeline/ai_broll.py` falls through to this account if the primary reports itself quota-exhausted/at capacity for the day. Safe to leave unset. |
| `CLOUDFLARE_API_TOKEN_FALLBACK` | Optional. API token (Workers AI Read + Edit) for the account above -- see `CLOUDFLARE_ACCOUNT_ID_FALLBACK`. |
| `CLOUDFLARE_ACCOUNT_ID_FALLBACK2` | Optional. Account ID for a third, again genuinely separate Cloudflare account -- tried if both accounts above fail. Added 2026-08-24 after a real capacity outage hit the primary and first fallback accounts on the same day; see `pipeline/ai_broll.py`'s module docstring. Safe to leave unset. |
| `CLOUDFLARE_API_TOKEN_FALLBACK2` | Optional. API token (Workers AI Read + Edit) for the account above -- see `CLOUDFLARE_ACCOUNT_ID_FALLBACK2`. |
| `DASHBOARD_URL` | The car-loan-dashboard Worker's base URL (same value the `bracketly` repo's `DASHBOARD_URL` secret uses) |
| `LOOPEDIN_INGEST_SECRET` | A long random string — set the identical value as a `wrangler secret put LOOPEDIN_INGEST_SECRET` on the dashboard Worker. Distinct from bracketly's `BRACKETLY_INGEST_SECRET`, so either app's ingest can be rotated independently. |

These never touch the repo's file content or git history — GitHub encrypts them and
only exposes them as env vars inside a workflow run.

### 2. Everything else

Already done as part of building this: Google Cloud project + APIs enabled, OAuth
consent screen pushed to Production, YouTube channel created, all credentials minted.
Remaining open item: the YouTube API quota increase request (`videos.insert` costs
1600 units/upload; 10/day exceeds the default 10,000/day cap until Google approves a
increase — `produce-upload.yml`'s manual `workflow_dispatch` trigger defaults to
`unlisted` for exactly this kind of cautious testing).

## Local dry run (optional — GitHub Actions secrets mean you don't have to)

```
pip install -r requirements.txt
cp secrets.example.env secrets.env   # fill in real values, never commit this file
```

Then run each `pipeline/*.py` step by hand and inspect `work/final.mp4` before ever
calling `upload.py --privacy public`. Since the real pipeline now runs through GitHub
Actions with `workflow_dispatch` (manually triggerable, defaults to `unlisted`), this
local dry run is a nice-to-have for debugging, not a required gate.
