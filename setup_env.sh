#!/usr/bin/env bash
# Run once at the top of every routine session (cloud checkout is fresh each fire).
set -euo pipefail

# Check ffprobe too, not just ffmpeg -- some base images ship one without the other.
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
    if command -v sudo >/dev/null 2>&1; then
        sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg
    else
        apt-get update -qq && apt-get install -y -qq ffmpeg
    fi
fi

pip install --quiet -r requirements.txt
mkdir -p work
