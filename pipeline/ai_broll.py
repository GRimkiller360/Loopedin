"""AI-generated background images via Cloudflare Workers AI (Stable Diffusion XL) --
an alternative b-roll source to Pixabay stock footage (see broll.py), added 2026-08-21
per explicit channel-owner instruction. Generates an image matching each beat's content
directly instead of searching for a pre-existing clip that may or may not match --
sidesteps the keyword-OR mismatch problem documented in broll.py/ROUTINE_INSTRUCTIONS.md
(a full-sentence query once returned an ocean wave for a "mask" search).

Free budget: Cloudflare gives every account 10,000 free Neurons/day (resets 00:00 UTC).
Uses flux-1-schnell (switched from SDXL 2026-08-21) specifically because its pricing is
documented and predictable -- tiles(512x512 blocks, rounded up) x 4.80 + 4 fixed steps x
9.60. At this module's IMAGE_WIDTH/IMAGE_HEIGHT that's 2 tiles = 48 Neurons/image, ~208
images/day at the full free budget. SDXL was dropped because Cloudflare lists it as Beta
with unclear real-world Neuron cost -- flux-1-schnell's cost is knowable in advance, so
the cap below can be set close to the real ceiling instead of guessing conservatively.
Two independent safety nets still keep this from depending on that math being exact:
1. DAILY_IMAGE_CAP is a hard, count-based ceiling with real headroom under the ~208/day
   computed ceiling (see IMAGE_WIDTH/IMAGE_HEIGHT's comment), checked before every
   generation attempt -- see quota_available().
2. If Cloudflare's own API ever rejects a request as a quota/rate-limit error anyway --
   the real, authoritative signal, not an estimate -- that's treated as exhausted for
   the rest of today immediately. Same reactive pattern pipeline/quota_guard.py already
   uses for YouTube's own quota: react to the provider's own rejection, don't just trust
   a self-computed number.
Either trigger raises AIImageUnavailable, which broll.py catches to fall back to
Pixabay for that beat -- this is never allowed to fail a whole production run.

Also treats missing CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN as "not configured yet"
rather than an error -- this module can ship and run before those secrets exist, and
the pipeline just keeps using Pixabay exclusively (today's actual behavior) until
they're added.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config
from pipeline.state_utils import load_json, save_json

MODEL = "@cf/black-forest-labs/flux-1-schnell"

# 512x1024 -- not the exact 1080x1920 (9:16, 0.5625) aspect ratio, but close (0.5),
# so assemble.py's existing scale+crop pipeline only takes a small extra crop versus
# Pixabay's mostly-landscape source footage, which it already crops far more from.
# Chosen over the exact-ratio 576x1024 specifically for tile cost: Cloudflare bills
# flux-1-schnell by 512x512 tiles rounded up, and 576 crosses into a second tile
# column for zero visual benefit over 512 at this width. 512x1024 = 1x2 = 2 tiles;
# 576x1024 = 2x2 = 4 tiles, the same cost as a full 1024x1024 image. Halving the
# tile cost is what pushes the free daily image budget from ~173/day to ~208/day --
# see module docstring -- which matters because upcoming multi-shot-per-beat editing
# needs far more images per video than the current one-per-beat approach.
IMAGE_WIDTH, IMAGE_HEIGHT = 512, 1024

# A fixed style suffix appended to every prompt (broll_query text is written for
# Pixabay's keyword-OR search, not tuned for image generation specifically) -- biases
# SDXL toward a consistent, higher-quality look without requiring any change to how
# the routine writes broll_query in the first place. "cinematic lighting" (the
# original suffix) measurably biased toward moody/dark images -- a real published
# video measured at 24% mean brightness against a healthy reference's 54%, with the
# top third of frame near-black through most of it. Swapped for wording that keeps
# quality/detail but pushes toward a brighter, higher-key look that actually reads on
# a phone screen in daylight.
STYLE_SUFFIX = ", bright natural lighting, vivid colors, high detail, vertical portrait photo"

# ~208/day is the computed ceiling at full free budget (see module docstring) --
# capped here at 150 to leave real headroom for Cloudflare's own pricing/rounding not
# matching this module's math exactly, while still giving 6 videos/day room for ~25
# images each once multi-shot-per-beat editing ships (this channel's current volume:
# 6 videos/day at 6 fires, cron `0 5,7,10,13,16,18 * * *`).
DAILY_IMAGE_CAP = 150

QUOTA_PATH = config.STATE_DIR / "image_quota.json"


class AIImageUnavailable(Exception):
    """Not configured, quota exhausted, or a real API failure -- broll.py treats all
    of these identically: fall back to Pixabay for this beat, never fail the run."""


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _load_quota():
    q = load_json(QUOTA_PATH, {})
    if q.get("date") != _today():
        q = {"date": _today(), "generated": 0, "exhausted": False}
    return q


def _save_quota(q):
    save_json(QUOTA_PATH, q)


def quota_available():
    q = _load_quota()
    return not q["exhausted"] and q["generated"] < DAILY_IMAGE_CAP


def _mark_exhausted():
    q = _load_quota()
    q["exhausted"] = True
    _save_quota(q)


def _record_generated():
    q = _load_quota()
    q["generated"] += 1
    _save_quota(q)


def credentials_configured():
    return bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID")) and bool(os.environ.get("CLOUDFLARE_API_TOKEN"))


def generate_image(prompt, out_path):
    if not credentials_configured():
        raise AIImageUnavailable("CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN not configured")
    if not quota_available():
        raise AIImageUnavailable("today's DAILY_IMAGE_CAP already reached")

    account_id = os.environ["CLOUDFLARE_ACCOUNT_ID"]
    token = os.environ["CLOUDFLARE_API_TOKEN"]
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{MODEL}"
    body = json.dumps({
        "prompt": prompt + STYLE_SUFFIX,
        "width": IMAGE_WIDTH,
        "height": IMAGE_HEIGHT,
    }).encode()

    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )

    def _do_request():
        with urllib.request.urlopen(req) as resp:
            return resp.headers.get("Content-Type", ""), resp.read()

    try:
        content_type, data = config.retry_transient(_do_request, is_retryable=config.is_retryable_urllib_error)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        if e.code == 429 or "quota" in detail.lower() or "rate limit" in detail.lower():
            _mark_exhausted()
            raise AIImageUnavailable(f"Cloudflare rejected as quota/rate-limit ({e.code}): {detail}") from e
        raise AIImageUnavailable(f"Cloudflare Workers AI request failed ({e.code}): {detail}") from e
    except Exception as e:
        # Any other transport/parsing failure -- never let an image-generation problem
        # take down the whole production run, Pixabay is always the safety net.
        raise AIImageUnavailable(f"Cloudflare Workers AI request failed: {e}") from e

    # flux-1-schnell's documented response is a JSON envelope with a base64-encoded
    # image (unlike SDXL, which the REST endpoint returns as raw bytes) -- handling
    # both shapes here rather than assuming means this also still works unmodified if
    # the model is ever switched back or changed again.
    if content_type.startswith("application/json"):
        payload = json.loads(data)
        if not payload.get("success", True):
            errors = payload.get("errors", [])
            detail = json.dumps(errors)
            if any("quota" in str(e).lower() or "rate limit" in str(e).lower() for e in errors):
                _mark_exhausted()
            raise AIImageUnavailable(f"Cloudflare Workers AI returned an error: {detail}")
        result = payload.get("result") or {}
        b64 = result.get("image") or result.get("image_b64")
        if not b64:
            raise AIImageUnavailable(f"unrecognized JSON response shape: {list(payload.keys())}")
        data = base64.b64decode(b64)

    Path(out_path).write_bytes(data)
    _record_generated()
    return out_path
