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
