# Pantheon-Fleet installer for Windows (PowerShell) — user-space, no admin.
#
# The macOS/Linux `curl … | sh` installer can't run on native Windows (no sh),
# so Windows uses this. Downloads the matching fleet.exe into
# %LOCALAPPDATA%\PantheonFleet and runs `fleet up` with the args you pass.
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
$exe = Join-Path $dir 'fleet.exe'

Write-Host "pantheon-fleet: downloading $base/fleet-windows-$arch.exe"
# GitHub requires TLS 1.2 — Windows PowerShell 5.1 defaults to older protocols.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
try {
    Invoke-WebRequest -Uri "$base/fleet-windows-$arch.exe" -OutFile $exe -UseBasicParsing
} catch {
    Write-Error "pantheon-fleet: download failed: $_"
    exit 1
}
Write-Host "pantheon-fleet: installed $exe"

# Add the install dir to the user's PATH (idempotent) so `fleet` works in new shells.
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($userPath -notlike "*$dir*") {
    [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ";$dir"), 'User')
    Write-Host "pantheon-fleet: added $dir to your PATH (open a new terminal to use ``fleet`` directly)"
}

if ($InstallOnly -or $env:FLEET_INSTALL_ONLY) { exit 0 }

$fleetArgs = @('up')
if ($Controller) { $fleetArgs += @('--controller', $Controller) }
if ($JoinToken)  { $fleetArgs += @('--join-token', $JoinToken) }
if ($Key)        { $fleetArgs += @('--key', $Key) }

# First run: Windows Defender Firewall will pop up asking to allow fleet.exe's
# UDP listener — click Allow so peers can reach this node (private networks is enough).
Write-Host 'pantheon-fleet: starting node (Ctrl-C to leave the fleet)'
& $exe @fleetArgs
exit $LASTEXITCODE
