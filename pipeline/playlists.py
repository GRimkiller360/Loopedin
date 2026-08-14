"""Keep one playlist per script category and add every newly uploaded video to its
category's playlist.

Why: grouping same-category Shorts into a playlist encourages session/binge watch time
(watching several videos back to back), which is a channel-level ranking signal
distinct from any individual video's own performance -- unlike per-video hooks/topics,
this is a lever on the channel as a whole.

state/playlists.json caches category -> playlist_id so this doesn't re-search/re-create
a playlist on every run (playlists.list also costs quota).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config
from pipeline.state_utils import load_json, save_json

PLAYLISTS_STATE_PATH = config.STATE_DIR / "playlists.json"


def _get_or_create_playlist_id(youtube, category, cache_path):
    cache = load_json(cache_path, {})
    if category in cache:
        return cache[category]

    title = f"{category.title()} Shorts"
    response = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": f"Auto-generated playlist grouping all {category} shorts.",
            },
            "status": {"privacyStatus": "public"},
        },
    ).execute()

    cache[category] = response["id"]
    save_json(cache_path, cache)
    return response["id"]


def add_video_to_category_playlist(youtube, category, video_id, cache_path=PLAYLISTS_STATE_PATH):
    if not category:
        return None
    playlist_id = _get_or_create_playlist_id(youtube, category, cache_path)
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {"kind": "youtube#video", "videoId": video_id},
            },
        },
    ).execute()
    return playlist_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", required=True)
    parser.add_argument("--video-id", required=True)
    args = parser.parse_args()

    client = config.youtube_client()
    pid = add_video_to_category_playlist(client, args.category, args.video_id)
    print(pid)
