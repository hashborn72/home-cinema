# Run only after the Android TV emulator has been started.
$ErrorActionPreference = 'Stop'
$devRoot = 'C:\Users\admin\AppData\Local\Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\HomeCinemaDev'
$adb = "$devRoot\android-sdk\platform-tools\adb.exe"
$devices = & $adb devices
if (-not ($devices -match '^emulator-5554\s+device$')) { throw 'Android TV emulator-5554 is not online' }
& $adb -s emulator-5554 install -r "$devRoot\downloads\Just.Player.v0.217.apk"
if ($LASTEXITCODE -ne 0) { throw 'Just Player installation failed' }
& $adb -s emulator-5554 install -r "$devRoot\home-cinema-debug.apk"
if ($LASTEXITCODE -ne 0) { throw 'Probe installation failed' }
$deviceToken = (Get-Content -LiteralPath "$devRoot\device-token" -Raw).Trim()
# Do not print the provisioning token or the adb invocation.
& $adb -s emulator-5554 shell am start -n space.hashborn.cinema/.MainActivity --es backend http://192.168.0.221:18093 --es backend_token $deviceToken | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Probe launch failed' }
Write-Output 'Probe installed and connected. Token was not logged.'
