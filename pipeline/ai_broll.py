"""AI-generated background images via Cloudflare Workers AI (Stable Diffusion XL) --
an alternative b-roll source to Pixabay stock footage (see broll.py), added 2026-08-21
per explicit channel-owner instruction. Generates an image matching each beat's content
directly instead of searching for a pre-existing clip that may or may not match --
sidesteps the keyword-OR mismatch problem documented in broll.py/ROUTINE_INSTRUCTIONS.md
(a full-sentence query once returned an ocean wave for a "mask" search).

Free budget: Cloudflare gives every account 10,000 free Neurons/day (resets 00:00 UTC),
and SDXL images cost roughly 50-100 Neurons each at the resolution used here -- an
estimate, not a guarantee, so two independent safety nets keep this pipeline from ever
actually depending on that estimate holding exactly:
1. DAILY_IMAGE_CAP is a hard, conservative count-based ceiling well under the estimated
   free budget (60 images/day vs. an estimated ~100-200/day free), checked before every
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

MODEL = "@cf/stabilityai/stable-diffusion-xl-base-1.0"

# 576x1024 -- exactly the final 1080x1920 (9:16) aspect ratio, so assemble.py's
# existing scale+crop pipeline barely has to crop at all, unlike Pixabay's mostly-
# landscape source footage. Also keeps the max dimension (1024) modest, which keeps
# Neuron cost on the lower end of the estimated 50-100/image range.
IMAGE_WIDTH, IMAGE_HEIGHT = 576, 1024

# A fixed style suffix appended to every prompt (broll_query text is written for
# Pixabay's keyword-OR search, not tuned for image generation specifically) -- biases
# SDXL toward a consistent, higher-quality look without requiring any change to how
# the routine writes broll_query in the first place.
STYLE_SUFFIX = ", cinematic lighting, high detail, vertical portrait photo"

# Conservative, well under the ~100-200/day estimated free budget -- see module
# docstring. This channel's actual need is ~30-70 images/day at current volume.
DAILY_IMAGE_CAP = 60

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

    # The REST endpoint for this model is documented to return raw image bytes, but
    # that couldn't be verified against a live account during development -- handle a
    # JSON envelope too (error payload, or a base64-wrapped result) rather than assume.
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
