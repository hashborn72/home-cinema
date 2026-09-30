#!/usr/bin/env bash
set -euo pipefail
cd /root/home-cinema
export ANDROID_HOME="$PWD/.toolchain/android-sdk"
export GRADLE_USER_HOME="$PWD/.toolchain/gradle-cache"
.toolchain/gradle-8.11.1/bin/gradle --no-daemon :app:testDebugUnitTest :app:assembleDebug
cp app/build/outputs/apk/debug/app-debug.apk dist/home-cinema-debug.apk
