"""Upload the finished short to YouTube.

`status.containsSyntheticMedia` (added to the Data API 2024-10-30) is confirmed
correct against current docs (developers.google.com/youtube/v3/docs/videos,
2026-08-20) -- this is the real "how this content was made" disclosure field.
"""
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config


# Posted as a top-level comment from the channel right after upload -- NOT pinned,
# the YouTube Data API has no pin endpoint at all (verified against current
# commentThreads docs 2026-08-21, pinning is Studio-UI-only). On a channel this small
# it typically ends up the only/top comment anyway since nothing else has been posted
# yet, but that's incidental, not guaranteed. Reinforces the narration's own closing
# CTA (see ROUTINE_INSTRUCTIONS.md) rather than replacing it. Several templates,
# randomly picked, for the same reason the narration CTA is varied rather than fixed --
# a single verbatim comment repeated on every upload reads as copy-pasted spam.
CTA_COMMENT_TEMPLATES = [
    "If this surprised you, follow for more {category} breakdowns -- there's a new one every few hours.",
    "What's your take? Drop a comment below, and follow if you want the next one.",
    "Didn't already know this one? Follow for more {category} facts like it.",
    "Let me know your reaction in the comments -- following gets you the next one before it's buried in your feed.",
]


def _post_cta_comment(youtube, video_id, channel_id, script):
    text = random.choice(CTA_COMMENT_TEMPLATES).format(category=script.get("category") or "these")
    youtube.commentThreads().insert(
        part="snippet",
        body={
            "snippet": {
                "channelId": channel_id,
                "videoId": video_id,
                "topLevelComment": {"snippet": {"textOriginal": text}},
            }
        },
    ).execute()


def _hashtags(script):
    # #Shorts + category + one topic-specific tag = 3. Capped there deliberately --
    # YouTube's own guidance is that piling on hashtags reads as spam and can hurt
    # reach rather than help it; research on Shorts discovery converges on 2-3 as the
    # useful range, not more.
    tags = ["#Shorts"]
    seen_slugs = {"shorts"}

    category = (script.get("category") or "").strip()
    if category:
        slug = "".join(ch for ch in category if ch.isalnum())
        if slug and slug.lower() not in seen_slugs:
            tags.append(f"#{slug}")
            seen_slugs.add(slug.lower())

    for raw_tag in script.get("tags", []):
        slug = "".join(ch for ch in raw_tag if ch.isalnum())
        if slug and slug.lower() not in seen_slugs:
            tags.append(f"#{slug}")
            seen_slugs.add(slug.lower())
            break

    return " ".join(tags)


def _sources_footer(script):
    # Appended to the description, not narrated -- gives a reviewer (or a curious
    # viewer) something to check on a fully automated, unreviewed channel. Absent
    # entirely if the script has no sources rather than than printing an empty
    # "Sources:" header.
    sources = script.get("sources") or []
    if not sources:
        return ""
    lines = ["", "Sources:"]
    for s in sources:
        url, note = s.get("url", ""), s.get("note", "")
        lines.append(f"- {url}" + (f" ({note})" if note else ""))
    return "\n".join(lines)


def upload_short(video_path, script, privacy_status="public"):
    from googleapiclient.http import MediaFileUpload

    youtube = config.youtube_client()
    body = {
        "snippet": {
            "title": script["title"][:100],
            "description": script["description"] + _sources_footer(script) + "\n\n" + _hashtags(script),
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
        # num_retries: googleapiclient's own built-in exponential backoff for
        # transient chunk-upload failures (5xx, connection resets) -- proven
        # library behavior rather than a hand-rolled retry loop. Doesn't retry on
        # non-transient errors like the 403 quota case, which is caught separately
        # below and must propagate immediately, not get retried into wasted attempts.
        _, response = request.next_chunk(num_retries=3)

    video_id = response["id"]

    # Best-effort, never fatal -- the video already published successfully by this
    # point, and a comment API hiccup (comments disabled, transient error, etc.) must
    # never be reported as an upload failure or count toward auto-pause.
    try:
        _post_cta_comment(youtube, video_id, response["snippet"]["channelId"], script)
    except Exception as e:
        print(f"warning: CTA comment failed (non-fatal): {e}", file=sys.stderr)

    return video_id


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
