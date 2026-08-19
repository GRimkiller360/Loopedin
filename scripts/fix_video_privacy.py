"""One-off: set privacyStatus=public on videos that were uploaded unlisted because
the UPLOAD_PRIVACY repo variable silently changed on 2026-08-19. See
.github/workflows/fix-video-privacy.yml. Safe to delete once run.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config

VIDEO_IDS = [
    "eybZwS54Pvo",
    "iYS7EOFk4MQ",
    "rL1YkvaDUJ4",
    "EvX57hXfPFE",
    "u36H3r8439k",
    "RyZyP4q30lE",
    "AKt4sEWMPz4",
    "g8XM42n2xvQ",
    "924WTGrZ2Ss",
]

if __name__ == "__main__":
    youtube = config.youtube_client()
    for video_id in VIDEO_IDS:
        youtube.videos().update(
            part="status",
            body={"id": video_id, "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}},
        ).execute()
        print(f"{video_id}: set to public")
