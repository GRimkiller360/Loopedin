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

# --- Archival stills (RENDER_STYLE=typographic, history/space categories) -----------
#
# Real photographs of real people/events are the actual visual differentiator this
# content has over generic stock footage that appears identically on thousands of
# other channels -- see the phase-2 brief. Two sources, in priority order: Wikimedia
# Commons (best coverage, structured license metadata, works for any topic) and NASA's
# image library (space topics only, effectively all public domain as US government
# work). Library of Congress was scoped out for now -- not implemented, flagged
# explicitly rather than silently skipped.
#
# Licence filtering is mandatory, not a nice-to-have: the repo is public and every
# video is unreviewed before publish, so an unlicensed image reaching upload would be
# a real, auditable problem, not just a style miss. Reject anything that isn't
# public domain, CC0, or plain CC-BY -- share-alike/non-commercial/no-derivatives
# variants carry obligations this pipeline has no mechanism to honor (attribution
# text isn't even shown on-screen), so they're excluded even though some of them
# would otherwise be usable with more work.
WIKIMEDIA_SEARCH_URL = "https://commons.wikimedia.org/w/api.php"
NASA_IMAGES_SEARCH_URL = "https://images-api.nasa.gov/search"
ARCHIVAL_API_TIMEOUT = 15


def _license_is_compliant(license_text):
    t = (license_text or "").strip().lower().replace(" ", "").replace("-", "").replace("_", "")
    if not t:
        return False
    if "cc" in t and "sa" in t:
        return False  # share-alike -- different obligations than plain CC-BY
    if "cc" in t and "nc" in t:
        return False  # non-commercial
    if "cc" in t and "nd" in t:
        return False  # no-derivatives -- this pipeline crops/composites, so exclude
    return "publicdomain" in t or "cc0" in t or "ccby" in t or t == "pd"


def _wikimedia_best_image(query, used_ids):
    params = urllib.parse.urlencode({
        "action": "query", "list": "search", "srsearch": query, "srnamespace": 6,
        "format": "json", "srlimit": 8,
    })
    req = urllib.request.Request(f"{WIKIMEDIA_SEARCH_URL}?{params}", headers={"User-Agent": USER_AGENT})

    def _do_search():
        with urllib.request.urlopen(req, timeout=ARCHIVAL_API_TIMEOUT) as resp:
            return json.loads(resp.read())

    try:
        result = config.retry_transient(_do_search, is_retryable=config.is_retryable_urllib_error)
    except Exception:
        return None  # a dead/erroring source must not break production -- caller falls back

    titles = [
        hit["title"] for hit in result.get("query", {}).get("search", [])
        if hit["title"] not in used_ids
    ]
    if not titles:
        return None

    info_params = urllib.parse.urlencode({
        "action": "query", "titles": "|".join(titles), "prop": "imageinfo",
        "iiprop": "url|extmetadata|mime", "format": "json",
    })
    info_req = urllib.request.Request(f"{WIKIMEDIA_SEARCH_URL}?{info_params}", headers={"User-Agent": USER_AGENT})

    def _do_info():
        with urllib.request.urlopen(info_req, timeout=ARCHIVAL_API_TIMEOUT) as resp:
            return json.loads(resp.read())

    try:
        info = config.retry_transient(_do_info, is_retryable=config.is_retryable_urllib_error)
    except Exception:
        return None

    for page in info.get("query", {}).get("pages", {}).values():
        imageinfo = (page.get("imageinfo") or [None])[0]
        if not imageinfo:
            continue
        # File: namespace search also matches PDFs/audio/video -- only an actual raster
        # image is usable as a composited background.
        if imageinfo.get("mime") not in ("image/jpeg", "image/png", "image/tiff", "image/webp"):
            continue
        meta = imageinfo.get("extmetadata", {})
        license_text = meta.get("LicenseShortName", {}).get("value") or meta.get("UsageTerms", {}).get("value")
        if not _license_is_compliant(license_text):
            continue
        return {
            "id": page.get("title"),
            "image_url": imageinfo["url"],
            "source_url": imageinfo.get("descriptionurl", imageinfo["url"]),
            "license": license_text,
            "artist": meta.get("Artist", {}).get("value", "unknown"),
        }
    return None  # results existed but none had a compliant license -- caller falls back


def _nasa_best_image(query, used_ids):
    params = urllib.parse.urlencode({"q": query, "media_type": "image"})
    req = urllib.request.Request(f"{NASA_IMAGES_SEARCH_URL}?{params}", headers={"User-Agent": USER_AGENT})

    def _do_search():
        with urllib.request.urlopen(req, timeout=ARCHIVAL_API_TIMEOUT) as resp:
            return json.loads(resp.read())

    try:
        result = config.retry_transient(_do_search, is_retryable=config.is_retryable_urllib_error)
    except Exception:
        return None

    for item in result.get("collection", {}).get("items", []):
        nasa_id = (item.get("data") or [{}])[0].get("nasa_id")
        if not nasa_id or nasa_id in used_ids:
            continue
        large = next(
            (l["href"] for l in item.get("links", []) if l.get("render") == "image" and "large" in l["href"]),
            None,
        )
        if not large:
            continue
        return {
            "id": nasa_id,
            "image_url": large,
            "source_url": f"https://images.nasa.gov/details-{nasa_id}",
            # NASA media is public domain as US government work (17 U.S.C. SS105) with
            # rare contractor-owned exceptions this doesn't specifically detect --
            # acceptable simplification given NASA's own blanket media usage guidance,
            # but flagged here rather than silently assumed.
            "license": "NASA (US Government Work, presumed public domain)",
            "artist": (item.get("data") or [{}])[0].get("photographer", "NASA"),
        }
    return None


def fetch_archival_still(query, category, out_path, used_ids=None):
    """Tries Wikimedia Commons first (any topic), then NASA's image library (space
    only) as a fallback. Returns a provenance dict on success, None if nothing
    licence-compliant was found anywhere -- callers must fall back to typographic-only
    (no archival image) rather than ever using an unlicensed result."""
    used_ids = used_ids if used_ids is not None else set()

    candidate = _wikimedia_best_image(query, used_ids)
    if not candidate and category == "space":
        candidate = _nasa_best_image(query, used_ids)
    if not candidate:
        return None

    dl_req = urllib.request.Request(candidate["image_url"], headers={"User-Agent": USER_AGENT})
    try:
        def _do_download():
            with urllib.request.urlopen(dl_req, timeout=ARCHIVAL_API_TIMEOUT) as resp, open(out_path, "wb") as f:
                f.write(resp.read())

        config.retry_transient(_do_download, is_retryable=config.is_retryable_urllib_error)
    except Exception:
        return None  # download failed -- treat exactly like "nothing found"

    used_ids.add(candidate["id"])
    return candidate


def _best_video_file(hit):
    # Pixabay has no orientation filter (unlike Pexels) and most stock footage is
    # landscape -- prefer a variant that's already vertical if one exists among the
    # size tiers, but assemble.py's scale+crop step normalizes any aspect ratio to the
    # 1080x1920 canvas regardless, so a landscape fallback still produces a valid video.
    files = [f for f in hit["videos"].values() if f.get("url")]
    verticals = [f for f in files if f["height"] > f["width"]]
    candidates = verticals or files
    return min(candidates, key=lambda f: abs(f.get("width", 0) - 1080))


# Pixabay's keyword-OR search can return a completely unrelated clip when a query's
# only "hit" was a generic word, not the actual subject -- confirmed in production: a
# snake-shedding beat's query ("snake eye scale close-up") returned a lipstick video,
# almost certainly matched on "close-up" alone. These words carry no real subject
# signal on their own and don't count when checking whether a candidate clip's own
# tags actually relate to the query.
GENERIC_QUERY_WORDS = {
    "close-up", "close", "up", "macro", "shot", "video", "footage", "scene",
    "background", "slow", "motion", "view", "angle", "aerial", "wide", "still",
    "the", "a", "an", "of", "in", "on", "with", "and", "or",
}


def _query_content_words(text):
    return {w.strip(".,!?:;\"'-").lower() for w in text.split()} - GENERIC_QUERY_WORDS - {""}


def _tags_relevant(query, tags):
    """True if the candidate clip's own Pixabay tags share at least one real subject
    word with the query -- not proof the footage is actually on-topic (still just tag
    text, not pixels), but catches the clearest spurious keyword-OR matches before a
    completely unrelated clip gets used. If the query has no real content words left
    after stripping generic terms, don't block on an empty check."""
    query_words = _query_content_words(query)
    if not query_words:
        return True
    tag_words = {t.strip().lower() for t in (tags or "").split(",")}
    return bool(query_words & tag_words)


def fetch_clip_for_query(query, out_path, used_ids, api_key):
    # Always returns (found, tags) -- tags is the matched Pixabay hit's own "tags"
    # field, already present in the search response at no extra API cost. This is the
    # only visual-content signal available without real computer vision (nothing in
    # this pipeline analyzes actual pixels) -- a proxy (what the clip's uploader
    # tagged it as), not ground truth about what's visible on screen. fetch_all only
    # persists it for beat 0's first sub-clip (what's on screen during the opening
    # 2-3s), but every call returns it for a uniform signature.
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
        if not _tags_relevant(query, hit.get("tags")):
            # A keyword-OR match with zero real subject overlap in the clip's own
            # tags is almost always spurious -- skip it and try the next hit rather
            # than using a plausible-looking but unrelated clip.
            continue
        file_info = _best_video_file(hit)
        dl_req = urllib.request.Request(file_info["url"], headers={"User-Agent": USER_AGENT})

        def _do_download():
            with urllib.request.urlopen(dl_req) as dl_resp, open(out_path, "wb") as f:
                f.write(dl_resp.read())

        config.retry_transient(_do_download, is_retryable=config.is_retryable_urllib_error)
        used_ids.add(hit["id"])
        return True, hit.get("tags")
    return False, None


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
    # Pixabay's own tags for whatever clip actually plays first -- the closest
    # available proxy for "what kind of visual opens this video" (motion/faces/
    # text-on-screen/etc.) without any real computer vision in this pipeline. Only
    # captured for beat 0's very first sub-clip, since that's what's on screen during
    # the opening 2-3s that most drop-off happens in.
    first_clip_tags = None

    for i, beat in enumerate(script["beats"]):
        word_count = len(config.strip_emphasis_markup(beat["text"]).split())
        num_clips = min(max(1, -(-word_count // MAX_WORDS_PER_CLIP)), MAX_CLIPS_PER_BEAT)

        sub_paths = []
        for j in range(num_clips):
            out_path = work_dir / f"beat_{i:02d}_{j:02d}.mp4"
            want_tags = i == 0 and j == 0
            found, tags = fetch_clip_for_query(beat["broll_query"], out_path, used_ids, api_key)
            if not found:
                found, tags = fetch_clip_for_query(script["topic"], out_path, used_ids, api_key)
            if not found:
                # A later sub-clip failing to find a fresh match isn't fatal -- fall
                # back to fewer cuts for this beat rather than failing the whole video
                # over running out of distinct matches for an already-narrow query.
                if sub_paths:
                    break
                raise RuntimeError(f"no b-roll found for beat {i} (query={beat['broll_query']!r})")
            if want_tags:
                first_clip_tags = tags
            sub_paths.append(str(out_path))
        beats_clips.append(sub_paths)

    return beats_clips, first_clip_tags


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--work-dir", required=True)
    args = parser.parse_args()

    script = json.loads(Path(args.script).read_text(encoding="utf-8"))
    beats_clips, first_clip_tags = fetch_all(script, args.work_dir, config.require("PIXABAY_API_KEY"))
    print(json.dumps({"beats": beats_clips, "first_clip_tags": first_clip_tags}, indent=2))
