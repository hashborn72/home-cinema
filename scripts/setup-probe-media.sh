#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .toolchain data
if [[ ! -f data/BigBuckBunny_320x180.mp4 ]]; then
  curl -fL --max-time 180 https://download.blender.org/peach/bigbuckbunny_movies/BigBuckBunny_320x180.mp4.zip -o .toolchain/bbb.zip
  unzip -n .toolchain/bbb.zip BigBuckBunny_320x180.mp4 -d data
fi
chown 65532:65532 data/BigBuckBunny_320x180.mp4
chmod 644 data/BigBuckBunny_320x180.mp4
