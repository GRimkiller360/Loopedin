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

1. **`.github/workflows/trend-fetch.yml`** — runs ~10x/day on its own schedule, ~10
   minutes before each Claude routine fire. Uses the `YOUTUBE_API_KEY` secret to find a
   trending-Shorts topic seed, writes `state/latest_trend_seed.json`, commits it. Also
   refreshes `state/live_stats.json` (near-real-time view/like/comment counts for
   recent uploads via `videos.list` — unlike Analytics data, this updates fast enough
   that checking it ~10x/day is actually useful, see `pipeline/live_stats.py`).
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
| `DASHBOARD_URL` | The car-loan-dashboard Worker's base URL (same value the `bracketly` repo's `DASHBOARD_URL` secret uses) |
| `LOOPEDIN_INGEST_SECRET` | A long random string — set the identical value as a `wrangler secret put LOOPEDIN_INGEST_SECRET` on the dashboard Worker. Distinct from bracketly's `BRACKETLY_INGEST_SECRET`, so either app's ingest can be rotated independently. |
| `TIKTOK_CLIENT_KEY` | From a TikTok developer app — see below. Optional: `pipeline/tiktok_upload.py`'s step is `continue-on-error`, so production keeps running fine without it, just skips the TikTok draft. |
| `TIKTOK_CLIENT_SECRET` | Same app as above. |
| `TIKTOK_REFRESH_TOKEN` | From the one-time OAuth consent below. |

**Setting up TikTok (optional, one-time, human-only):** every new video's finished
`.mp4` gets pushed into the TikTok account's *inbox as a draft* (a notification, then
tap "Post" from the phone) — not a fully automatic public post. Automatic public
posting needs TikTok's Content Posting *audit*, a manual review that can take weeks and
is built around apps with a real user-facing posting UI, a poor fit for a headless
pipeline; draft mode (`video.upload` scope) skips that entirely. None of this can be
done by the agent — it requires logging into a real TikTok account and clicking
"Allow" on TikTok's own consent screen:
1. Create a developer account at developers.tiktok.com and register an app.
2. Add the **Content Posting API** product, request the **`video.upload`** scope
   (not `video.publish` — that one needs the audit above).
3. Complete OAuth consent for the target TikTok account once, to get a client key/secret
   and a refresh token — add all three as the secrets above.

`pipeline/tiktok_upload.py` was written without access to TikTok's own reference docs
(blocked from the environment that built it) — treat the first real run against actual
credentials as a debugging session against a best-effort implementation, not a
guaranteed-working one; see that file's module docstring for the one field name that's
an unconfirmed guess.

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
