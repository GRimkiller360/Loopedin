"""Shared paths and credential lookup for the pipeline.

All secrets come from environment variables -- the cloud routine's environment injects
them; locally, source a .env file matching secrets.example.env before running any
script directly.
"""
import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state"
ASSETS_DIR = REPO_ROOT / "assets"

MAX_SHORT_SECONDS = 58
VARIETY_LOOKBACK = 40
# Raised from 3 to 6 (2026-08-20) for the 2-week hands-off stretch: there's no
# alerting if this pauses the channel (produce-upload.yml has no way to notify
# anyone), so a couple of one-off transient failures (a Cloudflare hiccup, a TTS
# timeout) auto-pausing the whole channel for the rest of those 2 weeks would be
# far worse than letting it ride out a bad day. At the routine's current 4 fires/day
# (2026-08-25), this still auto-pauses within about 1.5 days of genuinely broken state
# (e.g. an expired credential), not two weeks of silently failing runs -- it's a wider
# tolerance, not a disabled safety rail.
CONSECUTIVE_FAILURES_TO_PAUSE = 6

EMPHASIS_MARKUP_RE = re.compile(r"\*\*(.+?)\*\*")


def strip_emphasis_markup(text):
    """beat text may mark caption-emphasis words as **word** (see script_schema.py) --
    strip the markers for anything that needs the plain spoken/counted text (TTS input,
    beat-duration weighting), keeping just the word itself."""
    return EMPHASIS_MARKUP_RE.sub(r"\1", text)


def retry_transient(fn, attempts=3, backoff_seconds=2, is_retryable=None):
    """Call fn() and retry on transient-looking failures (network errors, 5xx status)
    instead of letting a one-off blip count as a full production failure toward
    auto-pause. is_retryable(exception) -> bool narrows what's worth retrying; without
    it, retries on any exception -- only pass that default when the caller already
    scopes what it catches."""
    import time

    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if is_retryable is not None and not is_retryable(e):
                raise
            if attempt < attempts:
                time.sleep(backoff_seconds * attempt)
    raise last_exc


def is_retryable_urllib_error(e):
    """5xx (server-side, transient) or a connection-level failure (DNS, timeout,
    refused) are worth retrying. 4xx (bad auth, bad request, quota) never is --
    retrying won't fix those and just wastes the attempts budget."""
    import urllib.error

    if isinstance(e, urllib.error.HTTPError):
        return e.code >= 500
    return isinstance(e, urllib.error.URLError)


def require(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def youtube_client():
    """Shared YouTube Data API v3 client, used by anything that needs to read/write
    the channel (upload.py) via the same OAuth refresh token."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=require("YOUTUBE_REFRESH_TOKEN"),
        client_id=require("YOUTUBE_CLIENT_ID"),
        client_secret=require("YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=creds)
