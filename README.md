# AI Shorts channel automation

Fully-automatic pipeline: spot a trending topic, write an original commentary short
(AI voice + stock b-roll + captions + royalty-free music), upload it to YouTube as a
Short. Runs 10x/24h as a Claude Code cloud routine. Full design rationale is in the
plan this was built from; the short version of the constraints:

- No real creator's footage/audio is ever reused — only a topic *seed* (title/category/
  view count) comes from trending videos; the script is always original.
- Runs in the cloud, so it has no access to this machine — all state lives in this repo,
  all secrets come from environment variables provided to the routine.
- Safety rails: topic/script variety enforcement, synthetic-content disclosure on every
  upload, auto-pause after repeated failures (see `state/routine_health.json`).

See `ROUTINE_INSTRUCTIONS.md` for the actual per-run operational runbook the cloud
agent follows.

## Manual one-time setup

These steps need your own browser/accounts — they can't be done for you.

1. **Google Cloud project** with these APIs enabled:
   - YouTube Data API v3
   - Cloud Text-to-Speech API
2. **OAuth consent screen**: configure for `youtube.upload`, `youtube.readonly`,
   `yt-analytics.readonly` scopes, then push to **Production** (not Testing) —
   Testing-status refresh tokens expire after 7 days, which breaks daily automation.
   Test this before relying on it; `youtube.upload` is a sensitive scope and it's not
   fully confirmed the personal-use exemption avoids verification review.
3. **OAuth client** (Desktop app type) → gives you a client_id + client_secret.
   Run the one-time local helper to mint a refresh token:
   ```
   pip install google-auth-oauthlib
   python scripts/get_refresh_token.py <client_id> <client_secret>
   ```
4. **YouTube Data API key** (separate from the OAuth client — used for trend search).
5. **Service account** for Cloud Text-to-Speech → download its JSON key.
6. **Pexels API key** (free) at pexels.com/api.
7. **Request a YouTube API quota increase** (Cloud Console → APIs & Services →
   YouTube Data API v3 → Quotas). `videos.insert` costs 1600 units; 10 uploads/day
   alone is ~16,000 units against a default 10,000/day cap. Until approved, cap real
   runs at ~6/day or run with `--privacy unlisted`.
8. **A private GitHub repo** for this project (push this directory to it) — the cloud
   routine clones it fresh every fire.
9. **Secrets**: before putting any real credential anywhere, check the claude.ai
   environment settings for the routine's environment for an env-var/secrets
   mechanism. If none exists, the fallback is an encrypted secrets file committed to
   the private repo with the decryption key held only in the routine's config — ask
   before doing this, it's a real trade-off, not a default.

## Local dry run (do this before ever letting the routine post publicly)

```
pip install -r requirements.txt
cp secrets.example.env secrets.env   # fill in real values, never commit this file
# on Windows, load it into your shell before running the pipeline steps manually:
#   Get-Content secrets.env | ForEach-Object { if ($_ -match '^(\w+)=(.*)$') { [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2]) } }
```

Then run each `pipeline/*.py` step by hand (see `ROUTINE_INSTRUCTIONS.md` for the exact
commands) and inspect `work/final.mp4` before ever calling `upload.py`. First few real
uploads should use `--privacy unlisted`, confirmed manually on the channel, before
switching to `public`.
