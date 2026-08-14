"""Upload the finished short to YouTube.

NOTE: the exact API field name for the "altered/synthetic content" disclosure toggle
below (`containsSyntheticMedia`) has NOT been verified against current YouTube Data API
docs -- confirm the real field name before relying on this for the disclosure safety
rail. If the field is wrong, the upload will likely still succeed but silently without
the disclosure set, which defeats that safety rail without any visible error.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config


def _youtube_client():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = Credentials(
        token=None,
        refresh_token=config.require("YOUTUBE_REFRESH_TOKEN"),
        client_id=config.require("YOUTUBE_CLIENT_ID"),
        client_secret=config.require("YOUTUBE_CLIENT_SECRET"),
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("youtube", "v3", credentials=creds)


def _hashtags(script):
    # #Shorts alone wastes a free discoverability signal -- add one category hashtag
    # too. Deliberately capped at 2 total: YouTube's own guidance is that piling on
    # hashtags reads as spam and can hurt reach rather than help it.
    tags = ["#Shorts"]
    category = (script.get("category") or "").strip()
    if category:
        slug = "".join(ch for ch in category if ch.isalnum())
        if slug:
            tags.append(f"#{slug}")
    return " ".join(tags)


def upload_short(video_path, script, privacy_status="public"):
    from googleapiclient.http import MediaFileUpload

    youtube = _youtube_client()
    body = {
        "snippet": {
            "title": script["title"][:100],
            "description": script["description"] + "\n\n" + _hashtags(script),
            "tags": script.get("tags", []),
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
            "containsSyntheticMedia": True,  # see module docstring -- verify this field name
        },
    }

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()

    return response["id"]


if __name__ == "__main__":
    from googleapiclient.errors import HttpError

    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--script", required=True)
    parser.add_argument("--privacy", default="public", choices=["public", "unlisted", "private"])
    args = parser.parse_args()

    script_data = json.loads(Path(args.script).read_text(encoding="utf-8"))
    try:
        video_id = upload_short(args.video, script_data, args.privacy)
        print(json.dumps({"video_id": video_id}))
    except HttpError as e:
        content = (e.content or b"").decode(errors="replace")
        if e.resp.status == 403 and "quota" in content.lower():
            # Distinct from a real failure: this is the YouTube API's own hard reject
            # once the day's 10,000-unit quota is spent. Exit code 2 lets the workflow
            # tell "expected, stop trying until reset" apart from "something's broken".
            print(json.dumps({"error": "quota_exceeded", "detail": content}))
            sys.exit(2)
        raise
