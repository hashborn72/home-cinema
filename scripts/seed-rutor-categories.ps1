# Development helper only. Runs read-only Python in memory on the PBR host.
$ErrorActionPreference = 'Stop'
$ssh = 'C:\Windows\System32\OpenSSH\ssh.exe'
$rows = (& $ssh -o BatchMode=yes -o StrictHostKeyChecking=yes vm-221 'python3 /root/home-cinema/scripts/rutor-category-bridge.py export') -join "`n"
if ($LASTEXITCODE -ne 0) { throw 'Cannot obtain category inputs' }
$null = $rows | ConvertFrom-Json
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($rows))
$helper = Get-Content (Join-Path $PSScriptRoot 'rutor-category-bridge.py') -Raw
$payload = $helper + "`nimport base64`nclassify(json.loads(base64.b64decode('$encoded')))`n"
$categories = ($payload | & $ssh -o BatchMode=yes -o StrictHostKeyChecking=yes nextcloud-144 'python3 -') -join "`n"
if ($LASTEXITCODE -ne 0) { throw 'Category check failed' }
$null = $categories | ConvertFrom-Json
$categories | & $ssh -o BatchMode=yes -o StrictHostKeyChecking=yes vm-221 'python3 /root/home-cinema/scripts/rutor-category-bridge.py import'
if ($LASTEXITCODE -ne 0) { throw 'Cannot save category cache' }
