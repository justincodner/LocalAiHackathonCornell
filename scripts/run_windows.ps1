# Emmet. Start local Beacon on Windows: Bonsai (llama-server), the Windows Helper and the Brain.
# Each runs hidden in the background with its log in logs\ (bonsai.log, helper.log, brain.log).
# Stop them all with scripts\stop_windows.ps1.
#
#   powershell -ExecutionPolicy Bypass -File scripts\run_windows.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\run_windows.ps1 -Model 4B -TalkKey right_control
#
# Then hold Right Alt and speak (or type in the "Beacon" window) with the app you want help with in front.
[CmdletBinding()]
param(
    [ValidateSet('2B', '4B')]
    [string]$Model = '2B',
    [string]$TalkKey = '',
    [switch]$NoVoice
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo 'brain\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "[ERR] $python not found. Run scripts\setup_windows_helper.ps1 first." -ForegroundColor Red
    exit 1
}
New-Item -ItemType Directory -Force (Join-Path $repo 'logs') | Out-Null

function Test-Up([string]$url) {
    try { $null = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2; return $true }
    catch [System.Net.WebException] { return [bool]$_.Exception.Response }
    catch { return $false }
}

function Wait-Up([string]$name, [string]$url, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
            if ($r.StatusCode -eq 200) { Write-Host "  $name is up." -ForegroundColor Green; return }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    Write-Host "[ERR] $name did not come up at $url within $seconds s. See logs\." -ForegroundColor Red
    exit 1
}

# CPU-only laptops are slower than the M1 the defaults were tuned on (docs/BRAIN_HANDOFF.md).
foreach ($pair in @(@('BEACON_LLM_TIMEOUT_S', '60'), @('BEACON_TASK_TIMEOUT_S', '150'), @('BEACON_THINKING_FILLER_MS', '2500'))) {
    if (-not [Environment]::GetEnvironmentVariable($pair[0])) { [Environment]::SetEnvironmentVariable($pair[0], $pair[1]) }
}
if (-not $env:BEACON_PROMPT_PATH -and (Test-Path (Join-Path $repo 'brain\prompts\system_v3_windows.txt'))) {
    $env:BEACON_PROMPT_PATH = Join-Path $repo 'brain\prompts\system_v3_windows.txt'
}

Write-Host '=== Beacon (Windows, local) ==='

if (Test-Up 'http://127.0.0.1:8080/health') {
    Write-Host '  Bonsai already answering on :8080.'
} else {
    Write-Host "  Starting Bonsai $Model on :8080..."
    # Windows PowerShell 5.1 does not quote Start-Process arguments, and this repo's path has spaces.
    Start-Process powershell -ArgumentList @('-ExecutionPolicy', 'Bypass', '-File',
        "`"$(Join-Path $PSScriptRoot 'start_bonsai.ps1')`"", '-Model', $Model) -WindowStyle Hidden `
        -RedirectStandardError (Join-Path $repo 'logs\bonsai.log') -RedirectStandardOutput (Join-Path $repo 'logs\bonsai.out.log')
}

if (Test-Up 'http://127.0.0.1:8765/health') {
    Write-Host '  A Helper is already answering on :8765 (mock or Windows); using it.'
} else {
    Write-Host '  Starting the Windows Helper on :8765...'
    $helperArgs = @('-m', 'win_helper')
    if ($TalkKey) { $helperArgs += @('--talk-key', $TalkKey) }
    if ($NoVoice) { $helperArgs += '--no-voice' }
    Start-Process -FilePath $python -ArgumentList $helperArgs -WorkingDirectory $repo -WindowStyle Hidden `
        -RedirectStandardError (Join-Path $repo 'logs\helper.log') -RedirectStandardOutput (Join-Path $repo 'logs\helper.out.log')
}

Wait-Up 'Helper' 'http://127.0.0.1:8765/health' 30
Wait-Up 'Bonsai' 'http://127.0.0.1:8080/health' 180

Write-Host '  Starting the Brain...'
Start-Process -FilePath $python -ArgumentList @('-m', 'beacon_brain') -WorkingDirectory $repo -WindowStyle Hidden `
    -RedirectStandardError (Join-Path $repo 'logs\brain.log') -RedirectStandardOutput (Join-Path $repo 'logs\brain.out.log')

$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) {
    if ((Test-Path (Join-Path $repo 'logs\brain.log')) -and
        (Select-String -Path (Join-Path $repo 'logs\brain.log') -Pattern 'listening for voice events' -Quiet)) {
        $key = if ($TalkKey) { $TalkKey } else { 'Right Alt' }
        Write-Host ''
        Write-Host "Beacon is ready. Bring an app to the front, hold $key and speak," -ForegroundColor Green
        Write-Host 'or type in the "Beacon" window. Stop everything with scripts\stop_windows.ps1.' -ForegroundColor Green
        exit 0
    }
    Start-Sleep -Milliseconds 500
}
Write-Host '[WARN] The Brain has not said "listening for voice events" yet; check logs\brain.log.' -ForegroundColor Yellow
