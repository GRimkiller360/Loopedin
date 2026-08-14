"""Shared paths and credential lookup for the pipeline.

All secrets come from environment variables -- the cloud routine's environment injects
them; locally, source a .env file matching secrets.example.env before running any
script directly.
"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = REPO_ROOT / "state"
ASSETS_DIR = REPO_ROOT / "assets"

MAX_SHORT_SECONDS = 58
VARIETY_LOOKBACK = 40
CONSECUTIVE_FAILURES_TO_PAUSE = 3


def require(name):
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value
