param([string]$PreferencesDirectory)
$ErrorActionPreference = 'Stop'
if (-not $PreferencesDirectory) {
    $PreferencesDirectory = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'houdini20.5'
    if ($env:HOUDINI_USER_PREF_DIR) { $PreferencesDirectory=$env:HOUDINI_USER_PREF_DIR.Replace('__HVER__','20.5') }
}
$packageSource = Join-Path $PSScriptRoot 'package'
if (-not (Test-Path -LiteralPath (Join-Path $packageSource 'python3.11libs\rtc_bridge\bridge.py'))) { throw 'Incomplete bridge package' }
$packageTarget = Join-Path $PreferencesDirectory 'rtc_rendercore_bridge'
$jsonTarget = Join-Path $PreferencesDirectory 'packages\rtc_rendercore_bridge.json'
$stamp=Get-Date -Format 'yyyyMMdd-HHmmss'
if (Test-Path -LiteralPath $packageTarget) { Copy-Item -LiteralPath $packageTarget -Destination ($packageTarget+'.backup-'+$stamp) -Recurse }
if (Test-Path -LiteralPath $jsonTarget) { Copy-Item -LiteralPath $jsonTarget -Destination ($jsonTarget+'.backup-'+$stamp) }
New-Item -ItemType Directory -Force -Path $packageTarget,(Split-Path $jsonTarget) | Out-Null
Copy-Item -Path (Join-Path $packageSource '*') -Destination $packageTarget -Recurse -Force
$packageJson=@{path=$packageTarget.Replace('\','/')} | ConvertTo-Json
[IO.File]::WriteAllText($jsonTarget,$packageJson,[Text.UTF8Encoding]::new($false))
Write-Output "Installed for Houdini 20.5: $jsonTarget"
Write-Output 'Restart Houdini, then add shelf RTC RenderCore through the shelf + menu.'
