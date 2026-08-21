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
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import ai_broll, config

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


AI_HELD_IMAGE_SECONDS = 3  # arbitrary and short -- assemble.py's _scale_clip already
                           # loops/trims any source clip to whatever the beat actually
                           # needs, exactly like it does for short Pixabay clips today


def _image_to_held_clip(image_path, out_path):
    """Turns a single static image into a short, motionless mp4 -- no scale/crop/zoom
    applied here, that's all handled uniformly downstream by assemble.py's _scale_clip,
    same as every Pixabay clip. Keeps broll.py's output contract simple: every beat
    gets a valid short mp4 regardless of which source produced it."""
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(image_path), "-t", str(AI_HELD_IMAGE_SECONDS),
        "-pix_fmt", "yuv420p", str(out_path),
    ], check=True)


def _fetch_ai_beat(beat, out_path, work_dir, index):
    image_path = work_dir / f"beat_{index:02d}_ai.png"
    ai_broll.generate_image(beat["broll_query"], image_path)
    _image_to_held_clip(image_path, out_path)


def fetch_all(script, work_dir, api_key):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    used_ids = set()
    clips = []

    for i, beat in enumerate(script["beats"]):
        out_path = work_dir / f"beat_{i:02d}.mp4"

        # AI-generated image first -- matches the beat's actual content directly
        # instead of searching for a pre-existing clip that may or may not match (see
        # module docstring). Falls back to Pixabay on ANY failure (not configured,
        # today's quota cap reached, a real API error) -- this can never fail a run on
        # its own, ai_broll.AIImageUnavailable covers all of those cases uniformly.
        try:
            _fetch_ai_beat(beat, out_path, work_dir, i)
            clips.append({"path": str(out_path), "source": "ai_image"})
            continue
        except ai_broll.AIImageUnavailable as e:
            print(f"beat {i}: AI image unavailable ({e}), falling back to Pixabay", file=sys.stderr)
        except Exception as e:
            print(f"beat {i}: AI image generation failed ({e}), falling back to Pixabay", file=sys.stderr)

        found = fetch_clip_for_query(beat["broll_query"], out_path, used_ids, api_key)
        if not found:
            found = fetch_clip_for_query(script["topic"], out_path, used_ids, api_key)
        if not found:
            raise RuntimeError(f"no b-roll found for beat {i} (query={beat['broll_query']!r})")
        clips.append({"path": str(out_path), "source": "pixabay"})

    return clips


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--work-dir", required=True)
    args = parser.parse_args()

    script = json.loads(Path(args.script).read_text(encoding="utf-8"))
    clips = fetch_all(script, args.work_dir, config.require("PIXABAY_API_KEY"))
    print(json.dumps(clips, indent=2))
