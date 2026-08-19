"""Fetch stock b-roll clips from Pixabay for each script beat.

Pixabay footage is licensed for this kind of reuse -- this is the deliberate
alternative to clipping any real creator's copyrighted video/audio. Switched from
Pexels (2026-08-18) after Pexels' Cloudflare WAF started hard-blocking GitHub Actions'
shared runner IP range outright (confirmed via 3 failed runs, 3 different runner IPs,
identical Cloudflare block page each time -- not a rate-limit challenge, not fixable by
retrying).
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config

SEARCH_URL = "https://pixabay.com/api/videos/"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def _best_video_file(hit):
    # Pixabay has no orientation filter (unlike Pexels) and most stock footage is
    # landscape -- prefer a variant that's already vertical if one exists among the
    # size tiers, but assemble.py's scale+crop step normalizes any aspect ratio to the
    # 1080x1920 canvas regardless, so a landscape fallback still produces a valid video.
    files = [f for f in hit["videos"].values() if f.get("url")]
    verticals = [f for f in files if f["height"] > f["width"]]
    candidates = verticals or files
    return min(candidates, key=lambda f: abs(f.get("width", 0) - 1080))


def fetch_clip_for_query(query, out_path, used_ids, api_key):
    # Pixabay hard-rejects (400) any q over 100 chars -- the routine writes deliberately
    # descriptive broll_query text (see ROUTINE_INSTRUCTIONS.md's "visually arresting"
    # guidance for beat 0 especially), which routinely exceeds that. Truncate at a word
    # boundary rather than fail the whole beat over a length limit unrelated to intent.
    if len(query) > 100:
        query = query[:100].rsplit(" ", 1)[0]

    params = urllib.parse.urlencode({
        "key": api_key, "q": query, "video_type": "film", "safesearch": "true", "per_page": 5,
    })
    req = urllib.request.Request(f"{SEARCH_URL}?{params}", headers={"User-Agent": USER_AGENT})

    def _do_search():
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    try:
        result = config.retry_transient(_do_search, is_retryable=config.is_retryable_urllib_error)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Pixabay request failed ({e.code}): {e.read().decode()}") from e

    for hit in result.get("hits", []):
        if hit["id"] in used_ids:
            continue
        file_info = _best_video_file(hit)
        dl_req = urllib.request.Request(file_info["url"], headers={"User-Agent": USER_AGENT})

        def _do_download():
            with urllib.request.urlopen(dl_req) as dl_resp, open(out_path, "wb") as f:
                f.write(dl_resp.read())

        config.retry_transient(_do_download, is_retryable=config.is_retryable_urllib_error)
        used_ids.add(hit["id"])
        return True
    return False


# A single unbroken shot holding for a long beat reads as static even with the zoom
# motion applied in assemble.py -- documented pattern-interrupt research says to
# change the actual visual every ~3-5s, not just add motion to one continuous clip.
# Word count is the same proxy assemble.py's _beat_durations already uses for timing
# (no exact per-beat audio duration is available at this stage either), so splitting
# on word count keeps this consistent with how duration is estimated elsewhere.
MAX_WORDS_PER_CLIP = 14
MAX_CLIPS_PER_BEAT = 3


def fetch_all(script, work_dir, api_key):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    used_ids = set()
    beats_clips = []

    for i, beat in enumerate(script["beats"]):
        word_count = len(config.strip_emphasis_markup(beat["text"]).split())
        num_clips = min(max(1, -(-word_count // MAX_WORDS_PER_CLIP)), MAX_CLIPS_PER_BEAT)

        sub_paths = []
        for j in range(num_clips):
            out_path = work_dir / f"beat_{i:02d}_{j:02d}.mp4"
            found = fetch_clip_for_query(beat["broll_query"], out_path, used_ids, api_key)
            if not found:
                found = fetch_clip_for_query(script["topic"], out_path, used_ids, api_key)
            if not found:
                # A later sub-clip failing to find a fresh match isn't fatal -- fall
                # back to fewer cuts for this beat rather than failing the whole video
                # over running out of distinct matches for an already-narrow query.
                if sub_paths:
                    break
                raise RuntimeError(f"no b-roll found for beat {i} (query={beat['broll_query']!r})")
            sub_paths.append(str(out_path))
        beats_clips.append(sub_paths)

    return beats_clips


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--work-dir", required=True)
    args = parser.parse_args()

    script = json.loads(Path(args.script).read_text(encoding="utf-8"))
    beats_clips = fetch_all(script, args.work_dir, config.require("PIXABAY_API_KEY"))
    print(json.dumps({"beats": beats_clips}, indent=2))
