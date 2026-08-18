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
CONSECUTIVE_FAILURES_TO_PAUSE = 3

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
    the channel (upload.py, playlists.py) via the same OAuth refresh token."""
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
