"""Provider-abstracted TTS. Default provider is Google Cloud TTS, chosen because it's
metered (not a subscription) and its free monthly quota comfortably covers this
volume -- $0 out of pocket until the channel earns. Swap providers later (e.g.
ElevenLabs, for better voice quality once there's income) via TTS_PROVIDER without
touching any other pipeline stage.

Voice defaults to Chirp3-HD (Google's highest-quality TTS tier, 2026-08-18) --
this channel's actual volume (4 videos/day as of 2026-08-25, ~400 chars narration
each, ~48k chars/month) sits comfortably under Chirp3-HD's ~1M free characters/month,
so this costs $0 at current scale despite being the premium tier, not the
free-by-necessity Standard voice used before.

Synthesizes each beat as a SEPARATE TTS call rather than joining every beat into one
text blob for a single call (the original approach) -- channel-owner report
("the text and narration still dont line up properly") traced back to assemble.py
having no real per-beat timing at all: with one continuous audio file and no
word/sentence timestamps from the TTS API, caption/cut timing was a pure guess
(proportional to each beat's TEXT LENGTH against the total audio duration), which
has no actual relationship to how long Chirp3-HD takes to SPEAK that beat. Splitting
into one call per beat costs nothing extra (Cloud TTS bills by character, not by
request -- see module docstring above) and gives an exact, measured duration for
every beat instead of an estimate. See synthesize_beats().
"""
import argparse
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config


def _google_access_token():
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    creds_info = json.loads(config.require("GOOGLE_TTS_CREDENTIALS_JSON"))
    creds = service_account.Credentials.from_service_account_info(
        creds_info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    creds.refresh(Request())
    return creds.token


# Switched from en-US-Chirp3-HD-Despina (female) to a male, British-English voice
# 2026-08-21 per explicit channel-owner request for a deeper, more classic
# documentary-narrator feel for this history channel. Picked from Google's own
# published voice-characteristics table (ai.google.dev/gemini-api/docs/speech-
# generation), not by ear -- "Charon" is officially tagged "Informative", the right
# register for factual narration; the closest realistic mature-male alternative on
# that table was "Sadaltager" (Knowledgeable). This selects an existing catalog voice
# by its documented style, not any attempt to reproduce a specific real narrator's
# voice. en-GB rather than en-US for the more classic-documentary accent.
def synthesize_google(text, voice_name="en-GB-Chirp3-HD-Charon"):
    import urllib.request

    token = _google_access_token()
    body = json.dumps({
        "input": {"text": text},
        "voice": {"languageCode": "en-GB", "name": voice_name},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.05},
    }).encode()

    req = urllib.request.Request(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )

    def _do_request():
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    try:
        result = config.retry_transient(_do_request, is_retryable=config.is_retryable_urllib_error)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Cloud TTS request failed ({e.code}): {e.read().decode()}") from e
    return base64.b64decode(result["audioContent"])


PROVIDERS = {"google": synthesize_google}


def synthesize(text, out_path, provider=None):
    provider = provider or os.environ.get("TTS_PROVIDER", "google")
    audio_bytes = PROVIDERS[provider](text)
    Path(out_path).write_bytes(audio_bytes)


def _probe_duration(path):
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(out.strip())


# Caps any internal silence WITHIN a single beat's own synthesized audio (a beat with
# internal punctuation -- a comma, a hedge clause -- can still get a natural TTS pause
# mid-sentence) at MAX_INTERNAL_PAUSE_SECONDS, the same way the old whole-file version
# of this (formerly in assemble.py) did -- see state/ruleset_changelog.json and this
# module's own docstring for why that's a real, measured Chirp3-HD behavior and why
# it's done via silenceremove rather than SSML <break> tags. Applied per-beat now
# instead of once on the full joined file, since each beat is its own TTS call.
MAX_INTERNAL_PAUSE_SECONDS = 0.25
SILENCE_THRESHOLD_DB = "-40dB"  # narration's dead air measured -57 to -60+ dBFS --
                                # comfortably below any actual quiet speech, low risk
                                # of clipping a real quiet word as "silence"

# Fixed, authored silence between beats -- replaces relying on whatever pause length
# Chirp3-HD happened to insert at a sentence boundary inside the old single joined-text
# call (that natural pause length varied per beat and was invisible to assemble.py,
# one root cause of caption/cut drift). Same target the old post-hoc trimming
# converged on (MAX_INTERNAL_PAUSE_SECONDS above), just authored directly instead of
# capped after the fact.
BEAT_GAP_SECONDS = 0.25


def _trim_internal_pauses(path, work_dir, index):
    trimmed = work_dir / f"beat_{index:02d}_trimmed.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-i", str(path),
        "-af", f"silenceremove=stop_periods=-1:stop_duration={MAX_INTERNAL_PAUSE_SECONDS}:stop_threshold={SILENCE_THRESHOLD_DB}",
        "-ar", "44100", "-ac", "2", str(trimmed),
    ], check=True)
    return trimmed


def _silence_clip(work_dir, duration_seconds):
    clip = work_dir / "beat_gap.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(duration_seconds), "-ar", "44100", "-ac", "2", str(clip),
    ], check=True)
    return clip


def synthesize_beats(beats, out_path, provider=None):
    """Synthesizes each beat as its own TTS call, trims each beat's own internal
    pauses, and concatenates them with a fixed BEAT_GAP_SECONDS of silence between
    beats (none after the last) into one narration file at out_path -- same output
    contract as the old single-call version, so nothing downstream of --out needs to
    change. Additionally writes a sidecar <out_path>.beats.json with each beat's exact
    measured on-screen span (its own trimmed duration, plus the gap that follows it),
    which assemble.py now uses directly for caption/cut timing instead of guessing
    from text length -- see this module's docstring."""
    out_path = Path(out_path)
    work_dir = out_path.parent / f"{out_path.stem}_tts_work"
    work_dir.mkdir(parents=True, exist_ok=True)

    trimmed_paths, beat_durations = [], []
    for i, beat in enumerate(beats):
        text = config.strip_emphasis_markup(beat["text"])
        raw = work_dir / f"beat_{i:02d}_raw.mp3"
        synthesize(text, raw, provider=provider)
        trimmed = _trim_internal_pauses(raw, work_dir, i)
        trimmed_paths.append(trimmed)
        beat_durations.append(_probe_duration(trimmed))

    gap = _silence_clip(work_dir, BEAT_GAP_SECONDS) if len(trimmed_paths) > 1 else None

    inputs = []
    for i, p in enumerate(trimmed_paths):
        inputs += ["-i", str(p)]
        if gap and i < len(trimmed_paths) - 1:
            inputs += ["-i", str(gap)]
    n_inputs = len(inputs) // 2
    concat_filter = "".join(f"[{i}:a]" for i in range(n_inputs)) + f"concat=n={n_inputs}:v=0:a=1[out]"
    subprocess.run([
        "ffmpeg", "-y", *inputs, "-filter_complex", concat_filter,
        "-map", "[out]", "-ar", "44100", "-ac", "2", str(out_path),
    ], check=True)

    beat_spans = [
        d + (BEAT_GAP_SECONDS if i < len(beat_durations) - 1 else 0.0)
        for i, d in enumerate(beat_durations)
    ]
    beats_sidecar = out_path.with_suffix(".beats.json")
    beats_sidecar.write_text(json.dumps({"beat_spans": beat_spans}), encoding="utf-8")

    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True, help="path to script.json")
    parser.add_argument("--out", required=True, help="output narration audio path")
    args = parser.parse_args()

    script = json.loads(Path(args.script).read_text(encoding="utf-8"))
    synthesize_beats(script["beats"], args.out)
    print(f"wrote {args.out}")
