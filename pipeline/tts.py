"""Provider-abstracted TTS. Default provider is Google Cloud TTS, chosen because it's
metered (not a subscription) and its free monthly quota comfortably covers this
volume -- $0 out of pocket until the channel earns. Swap providers later (e.g.
ElevenLabs, for better voice quality once there's income) via TTS_PROVIDER without
touching any other pipeline stage.
"""
import argparse
import base64
import json
import os
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


def synthesize_google(text, voice_name="en-US-Standard-D"):
    import urllib.request

    token = _google_access_token()
    body = json.dumps({
        "input": {"text": text},
        "voice": {"languageCode": "en-US", "name": voice_name},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.05},
    }).encode()

    req = urllib.request.Request(
        "https://texttospeech.googleapis.com/v1/text:synthesize",
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    return base64.b64decode(result["audioContent"])


PROVIDERS = {"google": synthesize_google}


def synthesize(text, out_path, provider=None):
    provider = provider or os.environ.get("TTS_PROVIDER", "google")
    audio_bytes = PROVIDERS[provider](text)
    Path(out_path).write_bytes(audio_bytes)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", required=True, help="path to script.json")
    parser.add_argument("--out", required=True, help="output narration audio path")
    args = parser.parse_args()

    script = json.loads(Path(args.script).read_text(encoding="utf-8"))
    full_text = " ".join(beat["text"] for beat in script["beats"])
    synthesize(full_text, args.out)
    print(f"wrote {args.out}")
