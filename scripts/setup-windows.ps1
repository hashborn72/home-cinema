$ErrorActionPreference = 'Stop'
$devRoot = 'C:\Users\admin\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\HomeCinemaDev'
$downloads = Join-Path $devRoot 'downloads'
$sdkRoot = Join-Path $devRoot 'android-sdk'
New-Item -ItemType Directory -Force -Path $downloads,$sdkRoot | Out-Null
if (-not (Test-Path "$devRoot\jdk")) {
    $asset = (Invoke-RestMethod 'https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture=x64&image_type=jdk&os=windows')[0].binary.package
    & curl.exe --fail --location --retry 2 $asset.link -o "$downloads\jdk.zip"
    if ($LASTEXITCODE -ne 0) { throw 'JDK download failed' }
    if ((Get-FileHash "$downloads\jdk.zip" -Algorithm SHA256).Hash.ToLower() -ne $asset.checksum) { throw 'JDK checksum mismatch' }
    Expand-Archive -LiteralPath "$downloads\jdk.zip" -DestinationPath "$devRoot\jdk-stage"
    $folder = Get-ChildItem "$devRoot\jdk-stage" -Directory | Select-Object -First 1
    Move-Item -LiteralPath $folder.FullName -Destination "$devRoot\jdk"
}
if (-not (Test-Path "$sdkRoot\cmdline-tools\latest\bin\sdkmanager.bat")) {
    & curl.exe --fail --location --retry 2 https://dl.google.com/android/repository/commandlinetools-win-15859902_latest.zip -o "$downloads\commandline-win.zip"
    if ($LASTEXITCODE -ne 0) { throw 'SDK download failed' }
    if ((Get-FileHash "$downloads\commandline-win.zip" -Algorithm SHA256).Hash.ToLower() -ne '90ae805d20434428bffcb699c290860f19bb5f66a67e6b330067e3de801fb04a') { throw 'SDK checksum mismatch' }
    Expand-Archive -LiteralPath "$downloads\commandline-win.zip" -DestinationPath "$devRoot\sdk-stage"
    New-Item -ItemType Directory -Force -Path "$sdkRoot\cmdline-tools" | Out-Null
    Move-Item -LiteralPath "$devRoot\sdk-stage\cmdline-tools" -Destination "$sdkRoot\cmdline-tools\latest"
}
$env:JAVA_HOME = "$devRoot\jdk"
$env:ANDROID_HOME = $sdkRoot
$sdkmanager = "$sdkRoot\cmdline-tools\latest\bin\sdkmanager.bat"
1..100 | ForEach-Object { 'y' } | & $sdkmanager "--sdk_root=$sdkRoot" --licenses | Out-Null
& $sdkmanager "--sdk_root=$sdkRoot" 'emulator' 'platform-tools'
if ($LASTEXITCODE -ne 0) { throw 'Emulator installation failed' }
& "$sdkRoot\emulator\emulator.exe" -accel-check
& $sdkmanager "--sdk_root=$sdkRoot" --list | Select-String 'system-images;android-(30|31|33|34|35|36);.*tv.*x86'
