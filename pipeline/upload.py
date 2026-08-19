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


def _build_description(script):
    # Sources are real editorial substance for a fully-automated, unreviewed channel
    # -- appended mechanically here (not left to the routine to remember to type into
    # `description` itself) so it's never accidentally dropped. One line per source,
    # in "note -- url" order so the claim being confirmed reads before the link.
    parts = [script["description"]]
    sources = script.get("sources") or []
    if sources:
        source_lines = "\n".join(f"- {s['note']}: {s['url']}" for s in sources if s.get("url"))
        if source_lines:
            parts.append(f"Source{'s' if len(sources) > 1 else ''}:\n{source_lines}")
    parts.append(_hashtags(script))
    return "\n\n".join(parts)


def upload_short(video_path, script, privacy_status="public"):
    from googleapiclient.http import MediaFileUpload

    youtube = config.youtube_client()
    body = {
        "snippet": {
            "title": script["title"][:100],
            "description": _build_description(script),
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
