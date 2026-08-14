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
   trending-Shorts topic seed, writes `state/latest_trend_seed.json`, commits it.
2. **Claude Code cloud routine** — fires shortly after, follows
   `ROUTINE_INSTRUCTIONS.md`: reads the seed + `state/performance_summary.md` +
   `state/used_topics.json`, writes an **original** `state/pending_script.json`,
   commits and pushes it. Needs no credentials at all.
3. **`.github/workflows/produce-upload.yml`** — triggered by that push. Uses
   `GOOGLE_TTS_CREDENTIALS_JSON`, `PEXELS_API_KEY`, `YOUTUBE_CLIENT_ID`,
   `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN` secrets to run narration → b-roll →
   assembly → upload, then records the result and clears the pending script.
4. **`.github/workflows/analytics-feedback.yml`** — runs on the same ~10x/day cadence as
   `trend-fetch.yml` (a few minutes earlier), pulling performance stats
   (`YOUTUBE_CLIENT_ID`/`SECRET`/`REFRESH_TOKEN` again) and writing
   `state/performance_summary.md` fresh before every routine fire, so topic/category
   choice is always steered by up-to-date view/engagement data instead of a once/day
   snapshot.

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
| `PEXELS_API_KEY` | Pexels API key |

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
