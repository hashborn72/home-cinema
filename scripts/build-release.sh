#!/usr/bin/env bash
set -euo pipefail
cd /root/home-cinema
umask 077
export ANDROID_HOME="$PWD/.toolchain/android-sdk"
export GRADLE_USER_HOME="$PWD/.toolchain/gradle-cache"
python3 scripts/setup-signing.py
.toolchain/gradle-8.11.1/bin/gradle --no-daemon :app:testDebugUnitTest :app:testReleaseUnitTest :app:assembleRelease
signing=/root/.local/share/home-cinema-signing
buildtools="$ANDROID_HOME/build-tools/35.0.0"
"$buildtools/zipalign" -f -p 4 app/build/outputs/apk/release/app-release-unsigned.apk dist/home-cinema-aligned.apk
"$buildtools/apksigner" sign --ks "$signing/release.jks" --ks-key-alias home-cinema \
  --ks-pass "file:$signing/store-password" \
  --out dist/home-cinema-release.apk dist/home-cinema-aligned.apk
"$buildtools/apksigner" verify --verbose --print-certs dist/home-cinema-release.apk
# Publish only after signature verification; rename makes the switch atomic.
install -m 644 dist/home-cinema-release.apk dist/home-cinema.apk.new
mv dist/home-cinema.apk.new dist/home-cinema.apk
