#!/usr/bin/env bash
# Run once at the top of every routine session (cloud checkout is fresh each fire).
set -euo pipefail

if ! command -v ffmpeg >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq ffmpeg
fi

pip install --quiet -r requirements.txt
mkdir -p work
