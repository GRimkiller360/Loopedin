"""Fetch AI-generated b-roll images for each visual shot via Cloudflare Workers AI (see
ai_broll.py) -- the sole b-roll source as of 2026-08-21, per explicit channel-owner
instruction to drop the Pixabay stock-footage fallback entirely and use AI images only.
Previously this fell back to Pixabay (itself a 2026-08-18 replacement for Pexels, which
started hard-blocking GitHub Actions' shared runner IP range) on any AI failure -- that
fallback is gone now, so a generation failure here fails the whole production run
instead of degrading to stock footage. That's an accepted trade, not an oversight: this
pipeline's other stages (script validation, quality gate) already fail hard the same
way, leaving state for a human to inspect rather than silently shipping something
worse. CLOUDFLARE_ACCOUNT_ID/CLOUDFLARE_API_TOKEN are effectively required secrets now,
not optional ones -- see ai_broll.py's credentials_configured().

Generates one DISTINCT image per visual shot, not one image per beat -- a long beat
gets several separate generations (each beat_role/broll_query the same prompt text,
but a fresh model call, so a fresh random seed produces a genuinely different image
each time, not the same picture panned/cropped differently). This is what actually
delivers the "new visual every ~1.5s" the reference-video breakdown called for
(assemble.py's earlier same-image-different-crop approach was explicitly rejected --
see state/ruleset_changelog.json). Shot counts come from pipeline/shot_planning.py
using each beat's real measured duration (tts.py's synthesize_beats() sidecar);
assemble.py doesn't need that count to match exactly (see its own docstring/comments)
-- it lays out whatever images this stage actually produced for a beat evenly across
that beat's final duration.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import ai_broll, shot_planning

AI_HELD_IMAGE_SECONDS = 3  # arbitrary and short -- assemble.py's _scale_clip already
                           # loops/trims this clip to whatever each sub-shot actually needs


def _image_to_held_clip(image_path, out_path):
    """Turns a single static image into a short, motionless mp4 -- no scale/crop/zoom
    applied here, that's all handled uniformly downstream by assemble.py's _scale_clip.
    Keeps broll.py's output contract simple: every shot gets a valid short mp4."""
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(image_path), "-t", str(AI_HELD_IMAGE_SECONDS),
        "-pix_fmt", "yuv420p", str(out_path),
    ], check=True)


def _fetch_ai_shot(beat, out_path, work_dir, beat_index, shot_index):
    image_path = work_dir / f"beat_{beat_index:02d}_shot_{shot_index:02d}_ai.png"
    ai_broll.generate_image(beat["broll_query"], image_path)
    _image_to_held_clip(image_path, out_path)


def fetch_all(script, narration_path, work_dir):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    clips = []

    # Real, measured per-beat spans from tts.py's synthesize_beats() sidecar -- same
    # source assemble.py uses (see its module docstring), not a text-length guess.
    # Shot counts don't need to match assemble.py's own layout exactly (see this
    # module's docstring), but there's no reason to use a worse estimate for this
    # decision now that the real one is sitting right next to narration_path.
    durations = json.loads(Path(narration_path).with_suffix(".beats.json").read_text(encoding="utf-8"))["beat_spans"]

    for i, (beat, duration) in enumerate(zip(script["beats"], durations)):
        n_shots = shot_planning.shots_for_duration(duration)
        for s in range(n_shots):
            out_path = work_dir / f"beat_{i:02d}_shot_{s:02d}.mp4"
            # No fallback and no try/except here -- see module docstring. AIImageUnavailable
            # (not configured, quota exhausted, or a real API error) or any other failure
            # propagates and fails this step, the same way script_schema.py/quality_gate.py
            # already fail this run outright rather than shipping a degraded video.
            _fetch_ai_shot(beat, out_path, work_dir, i, s)
            clips.append({"path": str(out_path), "source": "ai_image", "beat_index": i, "shot_index": s})

    return clips


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--narration", required=True)
    parser.add_argument("--work-dir", required=True)
    args = parser.parse_args()

    script = json.loads(Path(args.script).read_text(encoding="utf-8"))
    clips = fetch_all(script, args.narration, args.work_dir)
    print(json.dumps(clips, indent=2))
