# Pantheon-Fleet installer for Windows (PowerShell) — user-space, no admin.
#
# The macOS/Linux `curl … | sh` installer can't run on native Windows (no sh),
# so Windows uses this. Downloads the matching pantheon.exe into
# %LOCALAPPDATA%\PantheonFleet (with `fleet`, its earlier name, as an alias)
# and runs `pantheon node up` with the args you pass.
# Files shares home by default. -NoFiles opts out; -ShareDir limits the folders.
# Saved choices survive reinstalls.
#
#   & ([scriptblock]::Create((irm https://github.com/aristoteleo/PantheonOS/releases/download/fleet-latest/install.ps1))) `
#       -Controller <url> -JoinToken <token>
#
# Env overrides (also used by the test harness):
#   FLEET_BASE_URL   where the binaries live (default: the hosted release)
#   FLEET_DIR        install dir (default: %LOCALAPPDATA%\PantheonFleet)
#   FLEET_INSTALL_ONLY  if set, install the exe but do not run `up`
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Controller,
    [string]$JoinToken,
    [string]$Key,
    [string[]]$ShareDir,
    [switch]$NoFiles,
    [switch]$InstallOnly
)
$ErrorActionPreference = 'Stop'

$base = if ($env:FLEET_BASE_URL) { $env:FLEET_BASE_URL } else {
    'https://github.com/aristoteleo/PantheonOS/releases/download/fleet-latest'
}

# Match this machine's architecture (Windows on ARM ships as arm64).
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }

$dir = if ($env:FLEET_DIR) { $env:FLEET_DIR } else { Join-Path $env:LOCALAPPDATA 'PantheonFleet' }
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$exe = Join-Path $dir 'pantheon.exe'

Write-Host "pantheon: downloading $base/pantheon-windows-$arch.exe"
# GitHub requires TLS 1.2 — Windows PowerShell 5.1 defaults to older protocols.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
try {
    Invoke-WebRequest -Uri "$base/pantheon-windows-$arch.exe" -OutFile $exe -UseBasicParsing
} catch {
    Write-Error "pantheon: download failed: $_"
    exit 1
}
# `fleet` stays as an alias: `fleet X` is `pantheon node X`. The earlier
# fleet.exe would shadow it, so it goes (it cannot while a node runs from it).
Set-Content -Path (Join-Path $dir 'fleet.cmd') -Value '@"%~dp0pantheon.exe" node %*' -Encoding ASCII
$legacy = Join-Path $dir 'fleet.exe'
if (Test-Path $legacy) {
    try { Remove-Item -Force $legacy } catch {
        Write-Warning "pantheon: $legacy is in use; stop that node and run this installer again to finish"
    }
}
$helper = Join-Path $dir 'fleet-native-capture.exe'
$pendingHelper = "$helper.download"
try {
    Invoke-WebRequest -Uri "$base/fleet-native-capture-windows-$arch.exe" -OutFile $pendingHelper -UseBasicParsing
    Move-Item -Force $pendingHelper $helper
} catch {
    Remove-Item -ErrorAction SilentlyContinue $pendingHelper
    Write-Error "pantheon: native capture helper download failed: $_"
    exit 1
}
Write-Host "pantheon: installed $exe and native capture helper"

# Add the install dir to the user's PATH (idempotent) so `pantheon` works in new shells.
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$dir*") {
    [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ";$dir"), 'User')
    Write-Host "pantheon: added $dir to your PATH (open a new terminal to use ``pantheon`` directly)"
}

if ($InstallOnly -or $env:FLEET_INSTALL_ONLY) { exit 0 }

$fleetArgs = @('node', 'up')
if ($Controller) { $fleetArgs += @('--controller', $Controller) }
if ($JoinToken)  { $fleetArgs += @('--join-token', $JoinToken) }
if ($Key)        { $fleetArgs += @('--key', $Key) }
foreach ($folder in $ShareDir) { $fleetArgs += @('--share-dir', $folder) }
if ($NoFiles) { $fleetArgs += '--no-files' }

# First run: Windows Defender Firewall will pop up asking to allow pantheon.exe's
# UDP listener — click Allow so peers can reach this node (private networks is enough).
Write-Host 'pantheon: starting node (Ctrl-C to leave the fleet)'
& $exe @fleetArgs
exit $LASTEXITCODE
