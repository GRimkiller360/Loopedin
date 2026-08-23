"""Assemble the final vertical short: cut b-roll to beats, burn captions, mix
narration + background music. Uses ffmpeg directly (installed by setup_env.sh) rather
than a Python video library -- fewer deps to reinstall on every fresh cloud checkout.

Per-beat on-screen duration comes from the narration's <name>.beats.json sidecar
(tts.py's synthesize_beats() -- each beat is its own TTS call, so its real audio
duration is measured directly, not guessed). Switched from a proportional text-length
estimate 2026-08-22 after a channel-owner report that captions/cuts didn't line up
with the narration -- that estimate had no actual relationship to how long Chirp3-HD
takes to speak any given beat. Only the WITHIN-beat word-to-word caption split is still
estimated (no real word-level timestamps exist for that finer grain), now anchored to
each beat's real measured duration instead of a whole-video guess.
"""
import argparse
import json
import math
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config

WIDTH, HEIGHT = 1080, 1920

# One accent color per beat (not always gold) for **word**-marked emphasis, ASS inline
# &HBBGGRR& order -- rotates through EMPHASIS_COLORS below, picked per beat rather than
# fixed, so the highlighted keyword actually reads as "the one word that matters in
# this sentence" instead of a static style element the eye tunes out after a few
# repeats. Base caption style is already bold, so emphasis is color + a slight size
# bump rather than bold-on-bold, which wouldn't read as distinct.
# RGB -> BBGGRR: white FFFFFF, yellow FFFF00->00FFFF, green 33CC66->66CC33,
# red FF3B30->303BFF, magenta FF2D95->952DFF, cyan 30D5F2->F2D530.
EMPHASIS_COLORS = ["FFFFFF", "00FFFF", "66CC33", "303BFF", "952DFF", "F2D530"]
EMPHASIS_RESET = r"{\r}"


def _emphasis_override(color_hex):
    return rf"{{\c&H{color_hex}&\fscx115\fscy115}}"

# Every caption chunk pops in (scales up from 70% to 100%) over 120ms instead of just
# appearing statically -- with captions now switching every 1-2 words (WORDS_PER_CAPTION),
# that little burst of motion on each change is what actually keeps the eye locked to a
# constantly-refreshing caption instead of tuning it out, not just the bigger/centered
# static style. If a chunk's first word is also **emphasis**-marked, the emphasis
# override's own hard-set \fscx115 supersedes the pop transform for that word (ASS
# override tags on the same property don't blend) -- a rare, cosmetically minor
# interaction, not a bug, since emphasis marks are sparse (>=1-2 per beat, not per chunk).
POP_IN_DURATION_MS = 120
POP_IN_TAG = rf"{{\fscx70\fscy70\t(0,{POP_IN_DURATION_MS},\fscx100\fscy100)}}"


def _probe_duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(out.strip())


def _format_ass_timestamp(seconds):
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text):
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


# TikTok-style dynamic captions: 1 word on screen at a time instead of the whole beat
# sentence at once -- the eye can't skim ahead of a caption that keeps changing, which
# is exactly this channel's own top lever (avg_view_pct/retention). Dropped from 2->1
# 2026-08-21 after direct measurement against a real published video: 2-word chunks
# averaged 0.76s on screen (47 chunks in 36s) against a reference's ~0.42s (~105
# chunks in 44.65s), and occasionally line-wrapped to two lines at this font size,
# which never happens with true one-word chunks.
WORDS_PER_CAPTION = 1


def _tokenize_beat(text):
    """Splits beat text into (word, is_emphasized) tokens, based on the same **word**
    markers (see script_schema.py) _beat_text_to_ass used to consume whole-beat --
    needed per-word now since captions render 1-2 words at a time instead of a beat's
    full sentence. Splits on whitespace first, then strips ** per token, rather than
    re.split()-ing the whole string on the ** pairs first -- the latter drops trailing
    punctuation stuck directly to an emphasized word's closing ** (e.g. "**1518**,")
    into its own orphaned punctuation-only token with no word attached."""
    tokens = []
    for raw_word in text.split():
        is_emphasized = "**" in raw_word
        tokens.append((raw_word.replace("**", ""), is_emphasized))
    return tokens


def _chunk_to_ass(chunk, emphasis_color):
    words = []
    for word, is_emphasized in chunk:
        escaped = _ass_escape(word)
        words.append(f"{_emphasis_override(emphasis_color)}{escaped}{EMPHASIS_RESET}" if is_emphasized else escaped)
    return POP_IN_TAG + " ".join(words)


ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
; Fontsize 90->130, Outline 6->8, Shadow 0->3, Alignment 2 (bottom-center) -> 5
; (middle-center), MarginV 300->0 -- moved dead-center per explicit request
; (2026-08-21) and sized up now that each caption is only 1-2 words (WORDS_PER_CAPTION)
; instead of a full sentence, so there's plenty of room without wrapping.
Style: Default,Arial,130,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,8,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _write_ass(beats, durations, out_path):
    lines = [ASS_HEADER]
    t = 0.0
    for beat_index, (beat, beat_duration) in enumerate(zip(beats, durations)):
        tokens = _tokenize_beat(beat["text"])
        if not tokens:
            t += beat_duration  # keep later beats' timing aligned even on empty text
            continue

        # One color per beat/sentence, not one fixed color for the whole video --
        # rotates by beat index so consecutive beats don't repeat.
        emphasis_color = EMPHASIS_COLORS[beat_index % len(EMPHASIS_COLORS)]

        chunks = [tokens[i:i + WORDS_PER_CAPTION] for i in range(0, len(tokens), WORDS_PER_CAPTION)]
        # No real per-word timestamps from the TTS step (see module docstring) -- each
        # chunk's on-screen share is estimated proportionally to its word length
        # against beat_duration, which IS now a real measured span (tts.py synthesizes
        # each beat separately and reports its actual audio duration -- see
        # synthesize_beats() there), not a whole-video text-length guess. The
        # within-beat word-to-word split is still an estimate, just a much
        # smaller-scope one now that it's anchored to the beat's real duration instead
        # of one proportionally guessed from the total video length.
        total_weight = sum(max(len(word), 1) for chunk in chunks for word, _ in chunk)
        for chunk in chunks:
            chunk_weight = sum(max(len(word), 1) for word, _ in chunk)
            chunk_duration = beat_duration * chunk_weight / total_weight
            start, end = t, t + chunk_duration
            ass_text = _chunk_to_ass(chunk, emphasis_color)
            lines.append(f"Dialogue: 0,{_format_ass_timestamp(start)},{_format_ass_timestamp(end)},Default,,0,0,0,,{ass_text}")
            t = end
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")


CLIP_DURATION_BUFFER = 0.35  # each segment overshoots slightly so frame-rounding
                              # across N clips can never leave the concatenated
                              # video shorter than the narration audio

# Transition type as punctuation, not decoration -- a whip-blur transition marks a
# structurally important cut (into/out of a joke, into the hedge, into the ending),
# while an ordinary claim/evidence beat gets a near-instant hard cut. Using the same
# transition everywhere (what this pipeline did before) makes every cut feel equally
# weighted; varying it signals which moments actually matter. Both still go through the
# same xfade chain in _build_video_track -- WHIP_TRANSITION_DURATION is also the
# padding basis for every clip (see clip_targets in assemble()), a safe upper bound
# since HARD_CUT_DURATION is always smaller.
WHIP_TRANSITION_DURATION = 0.16
HARD_CUT_DURATION = 0.06
TRANSITION_DURATION = WHIP_TRANSITION_DURATION  # padding basis, see comment above

# beat_role values (script_schema.py's BEAT_ROLES) whose transition IN is a whip --
# matches the structural pattern found in a reference video's own edit: whip lands on
# joke entries/exits and tonal shifts, hard cut carries ordinary fact-to-fact moves.
WHIP_ROLES = {"joke", "hedge", "ending"}


def _transition_duration(beats, i):
    """Duration for the transition INTO beat i (i.e. between beat i-1 and beat i) --
    whip if beat i is entering a structurally important role, or beat i-1 was a joke
    (so the joke gets punctuated on both entry and exit)."""
    if beats[i].get("beat_role") in WHIP_ROLES or beats[i - 1].get("beat_role") == "joke":
        return WHIP_TRANSITION_DURATION
    return HARD_CUT_DURATION


OUTPUT_FPS = 30


# Continuous push-in zoom, magnitude depends on which beat -- a documented,
# easily-automated visual pattern-interrupt/pacing device that doesn't need to know
# *where* the interesting thing in frame is, unlike a pointer/circle overlay would --
# it just scales toward center, so it can't end up pointing at nothing. Beat 0 gets a
# dramatic punch (distinct treatment on the hook); every other beat gets a lighter
# version -- static, unmoving stock footage/AI images read as low-effort and give the
# eye nothing to track, so keep constant motion through the whole video, not just the
# opening. Raised sharply 2026-08-21 (1.15->1.35 hook, 1.06->1.20 subtle) after
# feedback that the previous values read as static/boring on real AI-generated images
# -- a 6% zoom over a couple seconds is genuinely close to imperceptible at normal
# viewing speed. Bigger zoom_end also gives PAN_TARGETS below more actual room to
# drift toward (the crop window's available travel is proportional to how far zoom_end
# is above 1.0), so this also makes the Ken Burns pan more visible, not just the zoom.
HOOK_ZOOM_END = 1.35
SUBTLE_ZOOM_END = 1.20

# Applied to every clip in every video -- a consistent color grade + vignette is a
# visual signature: raw unfiltered stock footage looks like raw unfiltered stock
# footage no matter whose channel it's on, but a consistent, distinct grade is
# something a viewer can start to recognize as *this channel's* look across videos,
# unlike per-video variation which reads as generic every time. Mild boost to
# contrast/saturation plus a soft vignette pulling focus toward center -- deliberately
# conservative, not stylized enough to fight the footage or look artificial.
SIGNATURE_LOOK_FILTER = "eq=contrast=1.08:saturation=1.18:brightness=0.01,vignette=PI/4"

# Real Ken Burns pan targets (fractional focal point the crop window drifts toward as
# it zooms), applied to every clip -- every clip is now an AI-generated held image
# (see ai_broll.py) with no motion of its own, so pairing zoom with a genuine
# directional drift (not just zooming on the dead center) is what keeps it from
# reading as "a photo with a zoom filter."
PAN_TARGETS = [(0.15, 0.25), (0.85, 0.25), (0.15, 0.75), (0.85, 0.75), (0.5, 0.15), (0.5, 0.85)]

def _scale_clip(src, dst, target, zoom=None, pan_target=None):
    # fps filter forces a real, constant frame rate on the AI-generated held-image
    # clip's own encode -- concatenating segments with different/variable frame rates
    # causes a stutter at each cut point even though the audio/captions stay on
    # schedule.
    vf = (
        f"fps={OUTPUT_FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},{SIGNATURE_LOOK_FILTER}"
    )
    zoom_end = {"hook": HOOK_ZOOM_END, "subtle": SUBTLE_ZOOM_END}.get(zoom)
    if zoom_end:
        # Driven entirely off ffmpeg's built-in `t` (elapsed seconds in this filter's
        # own timeline), not zoompan's frame-to-frame self-referential "zoom"
        # accumulator -- switched 2026-08-22 after a real published video (channel-owner
        # report: "completely frozen") confirmed every clip was rendering at one
        # unmoving frame despite the zoompan z='if(eq(on,1),1,min(zoom+step,zoom_end))'
        # construction below (the standard documented workaround for zoompan's own
        # frame-1 reset bug). That construction still depends on the filter correctly
        # persisting "zoom" across frames, which apparently doesn't hold on this
        # pipeline's real ffmpeg 6.1.1 build for a genuine multi-frame video input (as
        # opposed to zoompan's more common use case of a single `-loop 1` image with a
        # large `d`). `crop`'s w/h/x/y expressions are recomputed fresh from `t` on
        # every single frame instead -- there is no accumulator to persist, so there's
        # nothing left to fail to advance.
        progress = f"min(t/{target},1)"
        fx, fy = pan_target if pan_target else (0.5, 0.5)
        # Drift the crop window's center from frame-center toward (fx,fy) as the zoom
        # progresses from 1.0 to zoom_end, instead of staying centered -- see
        # PAN_TARGETS' comment. cx/cy are the crop window's center as a 0-1 fraction of
        # the frame; out_w/out_h (ffmpeg's names for THIS crop's own computed width/
        # height) let x/y reference the already-shrinking crop window directly instead
        # of recomputing its size a second time. max/min clamp to the valid crop range
        # for the same reason the old zoompan version did: an unclamped pan target can
        # put the raw center position outside the frame at modest zoom levels.
        cx = f"(0.5+({progress})*({fx}-0.5))"
        cy = f"(0.5+({progress})*({fy}-0.5))"
        crop_w = f"iw/(1+({zoom_end}-1)*({progress}))"
        crop_h = f"ih/(1+({zoom_end}-1)*({progress}))"
        vf += (
            f",crop=w='{crop_w}':h='{crop_h}':"
            f"x='max(0,min(in_w-out_w,in_w*{cx}-out_w/2))':"
            f"y='max(0,min(in_h-out_h,in_h*{cy}-out_h/2))',"
            f"scale={WIDTH}:{HEIGHT}"
        )
    clip_len = _probe_duration(src)
    loop_count = max(math.ceil(target / clip_len), 1) if clip_len > 0 else 1
    subprocess.run([
        "ffmpeg", "-y", "-stream_loop", str(loop_count - 1), "-i", str(src),
        "-t", str(target), "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", str(dst),
    ], check=True)


def _cut_offsets(clip_targets, transition_durations):
    """Absolute-time offset (seconds) of every cut point, one entry per shot after the
    first. Shared between _build_video_track's xfade chain and _build_swoosh_track's
    sound placement (see assemble()) so the swoosh can never drift out of sync with the
    actual video cut it's supposed to punctuate -- both derive from this exact same math
    instead of two separate copies of it."""
    offsets = []
    cumulative = clip_targets[0]
    for i in range(1, len(clip_targets)):
        duration = transition_durations[i]
        offsets.append(max(cumulative - duration, 0.0))
        cumulative = cumulative + clip_targets[i] - duration
    return offsets


def _build_video_track(scaled_paths, clip_targets, transition_durations, out_path):
    """Crossfades consecutive shot clips into video_track.mp4 instead of hard-cutting
    between them -- chains ffmpeg's xfade filter across all N clips in one pass, rather
    than the old concat-demuxer approach (concat can't overlap two streams at once, only
    play them back to back, so a real crossfade needs every clip as a live filter-graph
    input instead). transition_durations[i] is the duration of the cut INTO shot i
    (index 0 is unused -- the first shot has nothing to transition from); the caller
    (assemble()) decides per-shot whether that's a real beat-boundary whip/hard-cut
    (see WHIP_ROLES/_transition_duration) or an ordinary within-beat sub-shot cut."""
    n = len(scaled_paths)
    inputs = []
    for p in scaled_paths:
        inputs += ["-i", str(p)]

    if n == 1:
        filter_complex, final_label = None, "0:v"
    else:
        offsets = _cut_offsets(clip_targets, transition_durations)
        parts = []
        prev_label = "0:v"
        for i in range(1, n):
            duration = transition_durations[i]
            transition = "hblur" if duration == WHIP_TRANSITION_DURATION else "fade"
            out_label = f"v{i}"
            parts.append(
                f"[{prev_label}][{i}:v]xfade=transition={transition}:"
                f"duration={duration}:offset={offsets[i - 1]:.3f}[{out_label}]"
            )
            prev_label = out_label
        filter_complex, final_label = ";".join(parts), prev_label

    cmd = ["ffmpeg", "-y", *inputs]
    if filter_complex:
        cmd += ["-filter_complex", filter_complex, "-map", f"[{final_label}]"]
    else:
        cmd += ["-map", final_label]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-r", str(OUTPUT_FPS), "-pix_fmt", "yuv420p",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


# Soft, genuinely musical background bed, synthesized fresh per video rather than
# picked from a manually-curated folder -- explicit channel-owner request ("the
# routine must get a track for each video"). Replaces both the old file-based
# MUSIC_ENABLED/CATEGORY_MOODS system (assets/music/ was never actually populated --
# every external hosting site tried for real tracks was unreachable from this
# environment, and manual per-video uploads don't scale to an automated routine) and
# the older textureless ambient-bed drone -- this is a real, if simple, chord
# progression instead of three detuned sine tones.
#
# i-VI-III-VII in natural minor -- a common, pleasant, slightly moody progression (the
# same shape behind a lot of cinematic/lo-fi backing loops), chosen to land in the same
# "mysterious/dramatic/epic" territory the old CATEGORY_MOODS system targeted for this
# channel's history content, without needing per-category branching logic. Each chord
# is three sine tones at the chord's own equal-tempered frequencies -- no sourced
# samples or synth plugins, same zero-licensing-question reasoning as the swipe/ambient
# synthesis elsewhere in this file.
NATURAL_MINOR_CHORDS = {
    "i": (0, 3, 7),
    "VI": (8, 11, 15),
    "III": (3, 7, 10),
    "VII": (10, 14, 17),
}
CHORD_PROGRESSION = ["i", "VI", "III", "VII"]
CHORD_SECONDS = 3.5

# A different root note (and therefore overall pitch) per video, picked from real
# equal-tempered frequencies -- small, deliberate per-video variety without needing a
# whole pre-made library. F4-Bb4, not F3-Bb3 (2026-08-23 fix): a real render at the
# lower octave measured as genuinely inaudible -- narration.mp3's own vocal fundamental
# sits in roughly this same 100-250Hz band for a male voice (this channel's "Charon"
# voice), so even at an audible volume level the chord tones were being acoustically
# masked by the narration itself, not just quiet. One octave up keeps the same
# progression/character while mostly clearing the narration's own fundamental range.
ROOT_FREQUENCIES = [349.23, 392.00, 440.00, 466.16]  # F4, G4, A4, Bb4

# 0.22, not 0.12 (2026-08-23 fix): measured the actual synthesized output locally --
# the raw chord mix sits around -22dB mean, so the old 0.12 (-18.4dB) scaling put the
# final mixed-in level around -40dB mean, well below what's actually perceptible under
# narration. 0.22 (-13.2dB) lands it closer to -35dB -- still clearly a background bed,
# not competing with narration, but no longer effectively silent.
MUSIC_VOLUME = 0.22


def _chord_frequencies(root, semitone_offsets):
    return [root * 2 ** (s / 12) for s in semitone_offsets]


def _build_chord_clip(work_dir, index, freqs):
    clip = work_dir / f"music_chord_{index:02d}.mp3"
    inputs = []
    for f in freqs:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency={f:.3f}:duration={CHORD_SECONDS}"]
    n = len(freqs)
    labels = "".join(f"[{i}:a]" for i in range(n))
    # afade in/out on every chord (not just the loop's own seams) is what gives this a
    # soft pad envelope instead of an audible click at each chord change --
    # highpass/lowpass keeps it out of both sub-bass mud and anything sharp enough to
    # compete with narration frequencies.
    filter_complex = (
        f"{labels}amix=inputs={n}:duration=longest:normalize=0,"
        f"afade=t=in:st=0:d=0.6,afade=t=out:st={CHORD_SECONDS - 0.6}:d=0.6,"
        "highpass=f=100,lowpass=f=2000[out]"
    )
    subprocess.run([
        "ffmpeg", "-y", *inputs, "-filter_complex", filter_complex,
        "-map", "[out]", "-ar", "44100", "-ac", "2", str(clip),
    ], check=True)
    return clip


def _build_background_music(work_dir, duration_seconds):
    root = random.choice(ROOT_FREQUENCIES)
    chord_clips = [
        _build_chord_clip(work_dir, i, _chord_frequencies(root, NATURAL_MINOR_CHORDS[degree]))
        for i, degree in enumerate(CHORD_PROGRESSION)
    ]

    loop = work_dir / "music_loop.mp3"
    inputs = []
    for clip in chord_clips:
        inputs += ["-i", str(clip)]
    n = len(chord_clips)
    concat_filter = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
    subprocess.run([
        "ffmpeg", "-y", *inputs, "-filter_complex", concat_filter,
        "-map", "[out]", "-ar", "44100", "-ac", "2", str(loop),
    ], check=True)

    # Looped (not regenerated) to cover the full narration, same pattern as every other
    # short bed/track in this file -- one real chord progression is plenty of material
    # for something meant to sit quietly in the background the whole video.
    loop_seconds = CHORD_SECONDS * len(CHORD_PROGRESSION)
    loop_count = max(math.ceil(duration_seconds / loop_seconds), 1)
    music = work_dir / "background_music.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-stream_loop", str(loop_count - 1), "-i", str(loop),
        "-t", str(duration_seconds), "-ar", "44100", "-ac", "2", str(music),
    ], check=True)
    return music


# Explicit per-cut punctuation, distinct from the ambient bed's continuous texture --
# channel-owner request: a swoosh on every AI-image change. A synthesized noise-burst
# version of this shipped first and sounded terrible on a real render (channel-owner
# feedback); this is a real sourced clip instead -- "Swoosh 014" by Universfield,
# royalty-free/no-attribution-required (Pixabay license), picked by the channel owner
# directly rather than another synthesis attempt. Placed at every real cut via
# _cut_offsets, same as the removed synthesized version, so it can't drift out of sync
# with the picture cut it marks.
SWOOSH_SFX_PATH = config.ASSETS_DIR / "sfx" / "swoosh_014.mp3"
# Mixed well under the narration ("a lot softer" -- explicit channel-owner feedback on
# the first attempt) rather than the old synthesized version's volume=2.5 boost.
SWOOSH_VOLUME = 0.12


def _build_swoosh_track(work_dir, cut_offsets, sfx_path=SWOOSH_SFX_PATH):
    if not cut_offsets:
        return None
    n = len(cut_offsets)
    split_labels = [f"s{i}" for i in range(n)]
    parts = [f"[0:a]asplit={n}[" + "][".join(split_labels) + "]"]
    for label, offset in zip(split_labels, cut_offsets):
        ms = max(int(round(offset * 1000)), 0)
        parts.append(f"[{label}]adelay={ms}|{ms}[{label}d]")
    mix_inputs = "".join(f"[{label}d]" for label in split_labels)
    # duration=longest, not first/shortest -- the first delayed copy is the shortest
    # stream (least delay padding); using it as the reference would truncate every
    # later swoosh right at the mix step.
    parts.append(f"{mix_inputs}amix=inputs={n}:duration=longest:normalize=0[out]")

    track = work_dir / "swoosh_track.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(sfx_path),
        "-filter_complex", ";".join(parts),
        "-map", "[out]", "-ar", "44100", "-ac", "2", str(track),
    ], check=True)
    return track


def assemble(script, narration_path, clips, out_path, work_dir):
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    # Real, measured per-beat spans from tts.py's synthesize_beats() -- each beat was
    # its own TTS call, so this is the beat's actual audio duration (plus the fixed
    # gap that follows it), not a guess. Required, not optional: narration_path always
    # comes from tts.py in this pipeline (Narration always runs immediately before
    # B-roll/Assemble in produce-upload.yml), so the sidecar always exists here -- a
    # missing sidecar means something upstream is broken and should fail loud, not
    # silently fall back to the old text-length estimate that caused the drift this
    # replaced. See module docstring.
    narration_path = Path(narration_path)
    beat_spans = json.loads(narration_path.with_suffix(".beats.json").read_text(encoding="utf-8"))["beat_spans"]
    narration_duration = min(_probe_duration(narration_path), config.MAX_SHORT_SECONDS)
    durations = beat_spans

    # broll.py already generated one DISTINCT image per visual shot and tagged each
    # clip with which beat it belongs to (pipeline/shot_planning.py decided the shot
    # count there, from that same beat's real measured duration) -- group by beat_index
    # and lay out whatever images actually exist for beat i evenly across that beat's
    # span (computed just above, from the real per-beat measurement). The two stages
    # both read the same ground-truth beat_spans now, but deliberately still don't
    # NEED to agree on an exact shot count: assemble.py just adapts to whatever
    # broll.py produced, so nothing here can desync captions/timing from the video
    # track even if that ever changes.
    shots_by_beat = {}
    for clip in clips:
        shots_by_beat.setdefault(clip["beat_index"], []).append(clip)
    for beat_clips in shots_by_beat.values():
        beat_clips.sort(key=lambda c: c["shot_index"])

    # Each sub-shot clip is rendered TRANSITION_DURATION longer than it needs to be --
    # see TRANSITION_DURATION's comment -- so the crossfade below has real overlap
    # material to consume instead of eating into CLIP_DURATION_BUFFER's own
    # frame-rounding safety margin.
    scaled_paths = []
    clip_targets = []
    transition_durations = [0.0]  # index 0 unused -- the first shot has nothing to transition from
    for i, duration in enumerate(durations):
        role = script["beats"][i].get("beat_role")
        # 'ending' matches the hook's own zoom rate (not the subtle everyday rate) --
        # when the routine uses the sentence-loop technique (ROUTINE_INSTRUCTIONS.md),
        # matching motion across the loop point is part of what makes the cut back to
        # beat 0 read as continuous rather than a hard restart.
        zoom = "hook" if role in ("hook", "ending") else "subtle"

        beat_clips = shots_by_beat[i]
        sub_duration = duration / len(beat_clips)
        sub_target = sub_duration + CLIP_DURATION_BUFFER + TRANSITION_DURATION

        for s, clip in enumerate(beat_clips):
            scaled = work_dir / f"beat_{i:02d}_shot_{s:02d}_scaled.mp4"
            # Every clip is a distinct AI-generated image with no motion of its own
            # (see PAN_TARGETS' comment), so every sub-shot gets a real Ken Burns pan
            # on top of its own already-distinct content.
            pan_target = random.choice(PAN_TARGETS)
            _scale_clip(clip["path"], scaled, sub_target, zoom=zoom, pan_target=pan_target)
            scaled_paths.append(scaled)
            clip_targets.append(sub_target)

            if i == 0 and s == 0:
                continue  # the video's very first shot -- nothing to transition from
            if s == 0:
                # A real beat boundary -- carries the existing whip/hard-cut meaning.
                transition_durations.append(_transition_duration(script["beats"], i))
            else:
                # A within-beat sub-shot cut -- not a structural boundary, always a
                # fast hard cut so it reads as pace, not punctuation.
                transition_durations.append(HARD_CUT_DURATION)

    video_track = work_dir / "video_track.mp4"
    _build_video_track(scaled_paths, clip_targets, transition_durations, video_track)

    ass_path = work_dir / "captions.ass"
    _write_ass(script["beats"], durations, ass_path)

    # See _build_background_music's comment -- one synthesized chord progression per
    # video, not a file picked from a folder, so this always has something to mix in.
    background_music = _build_background_music(work_dir, narration_duration)
    mixed_audio = work_dir / "mixed_audio.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(narration_path), "-i", str(background_music),
        "-filter_complex",
        f"[1:a]volume={MUSIC_VOLUME}[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]",
        "-map", "[aout]", "-t", str(narration_duration), str(mixed_audio),
    ], check=True)

    # A swoosh on every real image-change cut (see _build_swoosh_track's comment) --
    # cut_offsets is the exact same math _build_video_track already used above for the
    # xfade chain, so the sound can't drift out of sync with the picture cut it marks.
    cut_offsets = _cut_offsets(clip_targets, transition_durations)
    swoosh_track = _build_swoosh_track(work_dir, cut_offsets)
    if swoosh_track:
        swooshed_audio = work_dir / "swooshed_audio.mp3"
        subprocess.run([
            "ffmpeg", "-y", "-i", str(mixed_audio), "-i", str(swoosh_track),
            "-filter_complex",
            f"[1:a]volume={SWOOSH_VOLUME}[sw];[0:a][sw]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]",
            "-map", "[aout]", "-t", str(narration_duration), str(swooshed_audio),
        ], check=True)
        mixed_audio = swooshed_audio

    # No force_style override needed -- the .ass file's own [V4+ Styles] section
    # carries the base look, and per-word emphasis overrides live inline in the text.
    # No corner watermark as of 2026-08-21 -- removed per measured feedback on a real
    # published video ("costs attention and buys nothing" on a format this short);
    # reverses the earlier explicit "keep it in the corner" call, confirmed with the
    # channel owner before making the change. assets/branding/ mascot SVGs are unused
    # now but left in place rather than deleted, in case that decision gets revisited.
    escaped_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video_track), "-i", str(mixed_audio),
        "-filter_complex", f"[0:v]subtitles='{escaped_ass}'[outv]",
        "-map", "[outv]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        "-t", str(narration_duration), str(out_path),
    ], check=True)

    return str(out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True)
    parser.add_argument("--narration", required=True)
    parser.add_argument("--clips", required=True, help="JSON list of {path, source, beat_index, shot_index} shot entries (see broll.py), inline or a file path")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    script_data = json.loads(Path(args.script).read_text(encoding="utf-8"))
    if args.clips.strip().startswith("["):
        clips = json.loads(args.clips)
    else:
        clips = json.loads(Path(args.clips).read_text(encoding="utf-8"))

    result = assemble(
        script_data, args.narration, clips, args.out, args.work_dir,
    )
    print(result)
