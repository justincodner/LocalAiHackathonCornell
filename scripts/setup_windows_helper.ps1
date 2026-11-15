# Emmet. One-time setup for local Beacon on Windows, run while online:
#   - a venv at brain\.venv with the Brain and the Windows Helper's packages
#   - the offline speech model (faster-whisper tiny.en, ~75 MB) in models\faster-whisper
# Bonsai itself comes from scripts\setup_windows.ps1 (PrismML Bonsai-demo next to this repo).
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup_windows_helper.ps1
[CmdletBinding()]
param([string]$Python = '')

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $repo 'brain\.venv'
$py = Join-Path $venv 'Scripts\python.exe'

if (-not (Test-Path -LiteralPath $py)) {
    if (-not $Python) {
        foreach ($cand in @((Join-Path $env:USERPROFILE 'anaconda3\python.exe'), 'py', 'python')) {
            if ((Get-Command $cand -ErrorAction SilentlyContinue)) { $Python = $cand; break }
        }
    }
    if (-not $Python) { throw 'No Python 3.11+ found. Pass -Python C:\path\to\python.exe' }
    Write-Host "Creating $venv with $Python"
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'venv creation failed' }
}

$ErrorActionPreference = 'Continue'  # pip writes notices to stderr
& $py -m pip install --disable-pip-version-check -q -e (Join-Path $repo 'brain') pytest
& $py -m pip install --disable-pip-version-check -q -r (Join-Path $repo 'win_helper\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

Write-Host 'Downloading the speech model (tiny.en)...'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
& $py -c "from faster_whisper import WhisperModel; WhisperModel('tiny.en', device='cpu', compute_type='int8', download_root=r'$repo\models\faster-whisper'); print('speech model ready')"
& $py -c "import sys; sys.coinit_flags = 0; import comtypes.client; comtypes.client.GetModule('UIAutomationCore.dll'); print('UI Automation ready')"
Write-Host 'Done. Start Beacon with: powershell -ExecutionPolicy Bypass -File scripts\run_windows.ps1' -ForegroundColor Green
