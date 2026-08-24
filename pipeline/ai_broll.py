"""AI-generated background images via Cloudflare Workers AI (flux-1-schnell) -- the
sole b-roll source (see broll.py) as of 2026-08-21, when the Pixabay stock-footage
fallback that originally backed this up was removed entirely per explicit
channel-owner instruction ("only use AI images"). Generates an image matching each
beat's content directly instead of searching for a pre-existing clip that may or may
not match -- also sidesteps the keyword-OR mismatch problem Pixabay had (documented in
git history/ROUTINE_INSTRUCTIONS.md: a full-sentence query once returned an ocean wave
for a "mask" search), though that's no longer the primary reason to prefer this path
now that it's the only path.

Free budget: Cloudflare gives every account 10,000 free Neurons/day (resets 00:00 UTC).
Uses flux-1-schnell (switched from SDXL 2026-08-21) for its predictable, documented
per-step pricing. NOTE (2026-08-22 fix): the model's real input schema is `prompt`,
`steps`, `seed` only -- no `width`/`height`, confirmed by a hard 400 from every single
production run since the 2026-08-21 switch ("Additional or unevaluated properties
'/width, /height' at '/' not allowed"). This module used to pass them anyway based on
an incorrect assumption that output size (and therefore tile-based cost) was
configurable; it isn't -- output comes back at the model's own fixed size, and the
exact real Neuron cost per image isn't verified here. DAILY_IMAGE_CAP below is
therefore a rough, conservative heuristic, not a precise budget calculation -- the real
protection against actually running out mid-day is the reactive check:
1. DAILY_IMAGE_CAP is a hard, count-based ceiling checked before every generation
   attempt -- see quota_available().
2. If Cloudflare's own API ever rejects a request as a quota/rate-limit error anyway --
   the real, authoritative signal, not an estimate -- that's treated as exhausted for
   the rest of today immediately. Same reactive pattern pipeline/quota_guard.py already
   uses for YouTube's own quota: react to the provider's own rejection, don't just trust
   a self-computed number.
Either trigger raises AIImageUnavailable. With no stock-footage fallback source left,
broll.py no longer catches this -- it propagates and fails that beat's b-roll step
outright, the same way script_schema.py/quality_gate.py already fail a run rather than
silently shipping something degraded. This makes CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN
required secrets in practice, not optional ones -- see credentials_configured().

Fallback Cloudflare account (2026-08-24): an optional second, genuinely separate
Cloudflare account -- CLOUDFLARE_ACCOUNT_ID_FALLBACK/CLOUDFLARE_API_TOKEN_FALLBACK --
generate_image() tries after the primary account reports itself quota-exhausted (local
cap or a real Cloudflare 429/4006 that survived the retry-with-backoff above). Added
after a real, extended (18+ minute) Cloudflare-side quota-enforcement bug: the
dashboard showed 0/10k real usage the whole time, but every request was still rejected
with "daily free allocation" -- a documented, still-open Cloudflare platform bug (see
QUOTA_RETRY_ATTEMPTS's comment), not something retrying the SAME account can reliably
route around. A second account has its own independent 10,000 free Neurons/day
allocation and its own independent backend state -- a second API token on the same
account would NOT help, since the free quota is enforced per-account, not per-token.
Purely additive: with no fallback configured, behavior is identical to before.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config
from pipeline.state_utils import load_json, save_json

MODEL = "@cf/black-forest-labs/flux-1-schnell"

# A fixed style suffix appended to every prompt (broll_query is a scene description --
# see ROUTINE_INSTRUCTIONS.md -- not pre-tuned for image generation specifically) --
# biases the model toward a consistent, higher-quality look without requiring any
# change to how the routine writes broll_query in the first place. "cinematic
# lighting" (the original suffix, written for the SDXL era) measurably biased toward
# moody/dark images -- a real published video measured at 24% mean brightness against
# a healthy reference's 54%, with the top third of frame near-black through most of
# it. First swap (to "bright natural lighting, vivid colors") got a real published
# video from 62/255 to 99.4/255 -- a big move, but still 28% below the reference's
# 138/255 -- so pushed further and more explicitly here (2026-08-24): "overexposed"
# and "high-key" are real photography terms that bias generation harder toward bright
# output than "bright natural lighting" alone did in practice, and "no shadows, no
# dark areas" gives the model an explicit negative to push against rather than only a
# positive description it can still satisfy while keeping a dim background.
STYLE_SUFFIX = ", bright overexposed high-key lighting, no shadows, no dark areas, vivid colors, high detail, vertical portrait photo"

# Conservative round-number ceiling, not a precise budget calculation (see module
# docstring -- real per-image Neuron cost at this model's fixed output size isn't
# verified). broll.py (2026-08-21) generates one DISTINCT image per visual shot, not
# per beat -- real demand at this channel's volume (6 videos/day, ~25 shots/video per
# pipeline/shot_planning.py's constants) is roughly 150/day. With Pixabay removed
# there's no fallback source left at all, so this exists purely to fail fast and
# predictably (AIImageUnavailable) rather than run the free budget to zero mid-run;
# the reactive 429/quota-error check below is what actually protects against
# over-spending if this number is wrong in either direction.
DAILY_IMAGE_CAP = 190

QUOTA_PATH = config.STATE_DIR / "image_quota.json"

# Cloudflare's own daily-quota 429 (error 4006) is unreliable: a well-documented, still
# -open Cloudflare platform bug returns this even when the account's real usage is
# 0/10k for the day -- a backend quota-enforcement/dashboard sync bug, not a real cap.
# See community.cloudflare.com threads "Workers AI daily free Neuron quota did not
# reset at 00:00 UTC" and "error 4006 persists after UTC reset while daily usage is 0"
# (multiple independent reports, Apr-Jul 2026) -- confirmed against this pipeline's own
# 2026-08-24 05:10 UTC run, which hit this error on its very first image request of the
# day. Retried here with backoff before accepting it as real exhaustion; this is
# deliberately local to ai_broll.py rather than folded into
# config.is_retryable_urllib_error -- a 429 from other providers (e.g. tts.py's Google
# Cloud TTS calls) is a real quota signal, not this specific Cloudflare bug, and
# shouldn't get the same treatment. Only the first image request of a run pays this
# cost: once _mark_exhausted() actually fires, quota_available() short-circuits every
# later call in the same run without another network round-trip.
QUOTA_RETRY_ATTEMPTS = 4
QUOTA_RETRY_BACKOFF_SECONDS = 20

# Tried in order for every image request. "fallback" only actually gets used if its two
# env vars are both set -- see _account_configured() -- so an unconfigured fallback is
# silently skipped, not an error.
ACCOUNTS = [
    {"label": "primary", "account_env": "CLOUDFLARE_ACCOUNT_ID", "token_env": "CLOUDFLARE_API_TOKEN"},
    {"label": "fallback", "account_env": "CLOUDFLARE_ACCOUNT_ID_FALLBACK", "token_env": "CLOUDFLARE_API_TOKEN_FALLBACK"},
]


class AIImageUnavailable(Exception):
    """Not configured, quota exhausted on every configured account, or a real API
    failure -- broll.py no longer catches this (no stock-footage fallback source
    exists any more), so it propagates and fails that production run outright."""


class _AccountQuotaExhausted(Exception):
    """Internal signal only: this one account is out for today (local cap, not
    configured, or a confirmed Cloudflare quota rejection). generate_image() catches
    this and tries the next configured account, if any -- a real (non-quota) API
    error raises AIImageUnavailable directly instead, since a different account
    wouldn't fix a malformed request either."""


def _today():
    return datetime.now(timezone.utc).date().isoformat()


def _load_quota():
    q = load_json(QUOTA_PATH, {})
    if q.get("date") != _today():
        return {"date": _today(), "generated": 0, "exhausted": {}}
    exhausted = q.get("exhausted", {})
    if isinstance(exhausted, bool):
        # Migrates the old single-account boolean schema (pre-fallback-account
        # support, before 2026-08-24) -- a prior True meant the primary account was
        # exhausted; there was no fallback concept yet for it to apply to.
        exhausted = {"primary": exhausted} if exhausted else {}
    q["exhausted"] = exhausted
    q.setdefault("generated", 0)
    return q


def _save_quota(q):
    save_json(QUOTA_PATH, q)


def quota_available(label="primary"):
    q = _load_quota()
    return not q["exhausted"].get(label, False) and q["generated"] < DAILY_IMAGE_CAP


def _mark_exhausted(label):
    q = _load_quota()
    q["exhausted"][label] = True
    _save_quota(q)


def _record_generated():
    q = _load_quota()
    q["generated"] += 1
    _save_quota(q)


def _account_configured(account):
    return bool(os.environ.get(account["account_env"])) and bool(os.environ.get(account["token_env"]))


def credentials_configured():
    """True if at least one configured Cloudflare account (primary or fallback) has
    both its account ID and API token set."""
    return any(_account_configured(a) for a in ACCOUNTS)


def _generate_from_account(account, prompt, out_path):
    label = account["label"]
    if not _account_configured(account):
        raise _AccountQuotaExhausted(f"{label}: {account['account_env']}/{account['token_env']} not configured")
    if not quota_available(label):
        raise _AccountQuotaExhausted(f"{label}: today's local cap/quota already exhausted")

    account_id = os.environ[account["account_env"]]
    token = os.environ[account["token_env"]]
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{MODEL}"
    # prompt/steps/seed only -- flux-1-schnell has no width/height input (confirmed by
    # a hard 400 from every real production run, see module docstring). Output comes
    # back at the model's own fixed size; assemble.py's scale+crop pipeline handles
    # whatever that turns out to be, same as it already tolerates a near-but-not-exact
    # aspect ratio.
    body = json.dumps({
        "prompt": prompt + STYLE_SUFFIX,
    }).encode()

    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )

    def _do_request():
        with urllib.request.urlopen(req) as resp:
            return resp.headers.get("Content-Type", ""), resp.read()

    content_type = data = None
    for attempt in range(1, QUOTA_RETRY_ATTEMPTS + 1):
        try:
            content_type, data = config.retry_transient(_do_request, is_retryable=config.is_retryable_urllib_error)
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            is_quota = e.code == 429 or "quota" in detail.lower() or "rate limit" in detail.lower()
            if not is_quota:
                raise AIImageUnavailable(f"{label}: Cloudflare Workers AI request failed ({e.code}): {detail}") from e
            if attempt < QUOTA_RETRY_ATTEMPTS:
                time.sleep(QUOTA_RETRY_BACKOFF_SECONDS * attempt)
                continue
            _mark_exhausted(label)
            raise _AccountQuotaExhausted(f"{label}: Cloudflare rejected as quota/rate-limit ({e.code}): {detail}") from e
        except Exception as e:
            # Any other transport/parsing failure -- wrapped as AIImageUnavailable so
            # every failure mode (not configured, quota, a real API error, this) is one
            # distinct, identifiable exception type in the logs, rather than a grab-bag
            # of different underlying exceptions all reaching the same "this run
            # failed" outcome.
            raise AIImageUnavailable(f"{label}: Cloudflare Workers AI request failed: {e}") from e

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
                _mark_exhausted(label)
                raise _AccountQuotaExhausted(f"{label}: Cloudflare Workers AI returned a quota error: {detail}")
            raise AIImageUnavailable(f"{label}: Cloudflare Workers AI returned an error: {detail}")
        result = payload.get("result") or {}
        b64 = result.get("image") or result.get("image_b64")
        if not b64:
            raise AIImageUnavailable(f"{label}: unrecognized JSON response shape: {list(payload.keys())}")
        data = base64.b64decode(b64)

    Path(out_path).write_bytes(data)
    _record_generated()
    return out_path


def generate_image(prompt, out_path):
    errors = []
    for account in ACCOUNTS:
        try:
            return _generate_from_account(account, prompt, out_path)
        except _AccountQuotaExhausted as e:
            errors.append(str(e))
            continue
    raise AIImageUnavailable("every configured Cloudflare account is unconfigured/quota-exhausted today: " + "; ".join(errors))
