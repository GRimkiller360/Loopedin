"""Push the finished short to TikTok as a draft in the creator's inbox (the
`video.upload` scope) -- NOT a direct public post (`video.publish`), which requires
TikTok to audit the app first (a manual review that can take weeks and is built around
apps with a real user-facing posting UI, a poor fit for a headless pipeline). Draft
mode needs no audit: the video lands in the TikTok account's inbox with a notification,
and a human taps "Post" from their phone. See ROUTINE_INSTRUCTIONS.md / this session's
setup notes for the one-time developer-app + OAuth steps only a human can complete.

UNVERIFIED against TikTok's real API responses as of writing -- their reference docs
(developers.tiktok.com) were unreachable from this environment's network policy, so
this is built from third-party-corroborated summaries of the /inbox/video/init/ flow,
not TikTok's own schema. Treat the first real run against actual credentials as a debug
session, not a working integration -- see _init_inbox_upload's post_info comment for
the one field name that's a genuine guess, most likely to need correcting.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config

TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
INIT_UPLOAD_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"


class TikTokUploadError(Exception):
    """Any failure in the token refresh, init, or byte-upload steps -- one distinct,
    identifiable exception type, same pattern as ai_broll.py's AIImageUnavailable."""


def _post_json(url, body, headers):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json; charset=UTF-8", **headers},
        method="POST",
    )

    def _do_request():
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    try:
        return config.retry_transient(_do_request, is_retryable=config.is_retryable_urllib_error)
    except urllib.error.HTTPError as e:
        raise TikTokUploadError(f"POST {url} failed ({e.code}): {e.read().decode(errors='replace')}") from e


def _access_token():
    # TikTok's OAuth2 token endpoint takes form-urlencoded params, not JSON -- unlike
    # every other request this module makes. grant_type=refresh_token exchanges the
    # long-lived TIKTOK_REFRESH_TOKEN (obtained once, by a human, via TikTok's OAuth
    # consent screen -- see module docstring) for a short-lived access token.
    import urllib.parse

    body = urllib.parse.urlencode({
        "client_key": config.require("TIKTOK_CLIENT_KEY"),
        "client_secret": config.require("TIKTOK_CLIENT_SECRET"),
        "grant_type": "refresh_token",
        "refresh_token": config.require("TIKTOK_REFRESH_TOKEN"),
    }).encode()

    req = urllib.request.Request(
        TOKEN_URL, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    def _do_request():
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    try:
        result = config.retry_transient(_do_request, is_retryable=config.is_retryable_urllib_error)
    except urllib.error.HTTPError as e:
        raise TikTokUploadError(f"token refresh failed ({e.code}): {e.read().decode(errors='replace')}") from e

    if "access_token" not in result:
        raise TikTokUploadError(f"token refresh returned no access_token: {result}")
    return result["access_token"]


def _init_inbox_upload(access_token, video_size):
    # source_info is the well-corroborated part of this request (see module docstring).
    # Single PUT, not true chunking -- chunk_size=video_size and total_chunk_count=1
    # is the documented shortcut for "upload the whole file in one request" rather than
    # actually splitting it, valid up to whatever TikTok's own per-request size cap is
    # (not confirmed here; this pipeline's videos are well under a minute and a few MB,
    # comfortably inside any plausible cap).
    #
    # post_info.title below is a GUESS, not confirmed against real docs -- mirrors the
    # Direct Post endpoint's known post_info.title field, on the theory that inbox mode
    # uses a similarly-shaped optional block (a source summary claimed inbox mode
    # "supports sending title and description," but not the literal field name). If the
    # real API rejects or silently ignores this, that's the first thing to check when
    # debugging against a real account -- drop it entirely rather than guess further.
    body = {
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": video_size,
            "chunk_size": video_size,
            "total_chunk_count": 1,
        },
    }
    result = _post_json(INIT_UPLOAD_URL, body, {"Authorization": f"Bearer {access_token}"})
    error = result.get("error") or {}
    if error.get("code") not in (None, "ok"):
        raise TikTokUploadError(f"inbox init rejected: {error}")
    data = result.get("data") or {}
    if not data.get("upload_url") or not data.get("publish_id"):
        raise TikTokUploadError(f"inbox init returned no upload_url/publish_id: {result}")
    return data["publish_id"], data["upload_url"]


def _upload_bytes(upload_url, video_path, video_size):
    video_bytes = Path(video_path).read_bytes()
    req = urllib.request.Request(
        upload_url, data=video_bytes, method="PUT",
        headers={
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
        },
    )

    def _do_request():
        with urllib.request.urlopen(req) as resp:
            return resp.status

    try:
        config.retry_transient(_do_request, is_retryable=config.is_retryable_urllib_error)
    except urllib.error.HTTPError as e:
        raise TikTokUploadError(f"video byte upload failed ({e.code}): {e.read().decode(errors='replace')}") from e


def upload_draft(video_path, script):
    access_token = _access_token()
    video_size = Path(video_path).stat().st_size
    publish_id, upload_url = _init_inbox_upload(access_token, video_size)
    _upload_bytes(upload_url, video_path, video_size)
    return publish_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--script", required=True)
    args = parser.parse_args()

    script_data = json.loads(Path(args.script).read_text(encoding="utf-8"))
    publish_id = upload_draft(args.video, script_data)
    print(json.dumps({"publish_id": publish_id}))
