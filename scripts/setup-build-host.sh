#!/usr/bin/env bash
set -euo pipefail
cd /root/home-cinema
mkdir -p .toolchain/downloads .toolchain/android-sdk/cmdline-tools dist
if ! command -v java >/dev/null; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openjdk-21-jdk-headless
fi
sdk="$PWD/.toolchain/android-sdk"
if [ ! -x "$sdk/cmdline-tools/latest/bin/sdkmanager" ]; then
  curl --fail --location --retry 2 https://dl.google.com/android/repository/commandlinetools-linux-15859902_latest.zip -o .toolchain/downloads/commandline-linux.zip
  printf '%s  %s\n' '4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583' '.toolchain/downloads/commandline-linux.zip' | sha256sum -c -
  unzip -q .toolchain/downloads/commandline-linux.zip -d "$sdk/cmdline-tools/staging"
  mv "$sdk/cmdline-tools/staging/cmdline-tools" "$sdk/cmdline-tools/latest"
fi
set +o pipefail
yes | "$sdk/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$sdk" --licenses >/dev/null
set -o pipefail
"$sdk/cmdline-tools/latest/bin/sdkmanager" --sdk_root="$sdk" 'platforms;android-35' 'build-tools;35.0.0' 'platform-tools'
if [ ! -x .toolchain/gradle-8.11.1/bin/gradle ]; then
  curl --fail --location --retry 2 https://services.gradle.org/distributions/gradle-8.11.1-bin.zip -o .toolchain/downloads/gradle.zip
  curl --fail --location https://services.gradle.org/distributions/gradle-8.11.1-bin.zip.sha256 -o .toolchain/downloads/gradle.sha256
  expected=$(tr -d '\r\n' < .toolchain/downloads/gradle.sha256)
  printf '%s  %s\n' "$expected" '.toolchain/downloads/gradle.zip' | sha256sum -c -
  unzip -q .toolchain/downloads/gradle.zip -d .toolchain
fi
java -version
echo 'Build toolchain ready (project scoped SDK and Gradle).'
