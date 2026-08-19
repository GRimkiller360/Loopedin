"""One-off utility: update the channel's About-section description via the YouTube
Data API. Not part of the regular pipeline (nothing else calls this) -- run manually
via .github/workflows/update-channel-branding.yml's workflow_dispatch when the
channel's identity/positioning copy needs to change.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline import config


def update_description(description):
    youtube = config.youtube_client()

    channels = youtube.channels().list(part="brandingSettings", mine=True).execute()
    items = channels.get("items", [])
    if not items:
        raise RuntimeError("no channel found for these credentials (mine=True returned nothing)")
    channel_id = items[0]["id"]
    branding = items[0].get("brandingSettings", {})
    branding.setdefault("channel", {})["description"] = description

    youtube.channels().update(
        part="brandingSettings",
        body={"id": channel_id, "brandingSettings": branding},
    ).execute()
    return channel_id


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--description", required=True)
    args = parser.parse_args()

    channel_id = update_description(args.description)
    print(f"Updated channel {channel_id}'s About description.")
