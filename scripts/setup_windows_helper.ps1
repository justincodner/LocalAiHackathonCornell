# Emmet. One-time setup for local Beacon on Windows, run while online:
#   - a venv at brain\.venv with the Brain and the Windows Helper's packages
#   - the offline speech model (faster-whisper tiny.en, ~75 MB) in models\faster-whisper
#   - Bonsai's Vulkan (GPU) build in ..\Bonsai-demo\bin\vulkan when the machine has a Vulkan-capable GPU
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

# GPU for Bonsai. PrismML's setup only picks Vulkan when the Vulkan SDK is installed, so laptops with Intel or
# AMD integrated graphics get the CPU build. The Vulkan runtime ships with the graphics driver, and the Vulkan
# build reads prompts ~2.5x faster on an Iris Xe (docs: win_helper/README.md). start_bonsai.ps1 prefers bin\vulkan.
$ErrorActionPreference = 'Stop'
$demo = Join-Path (Split-Path -Parent $repo) 'Bonsai-demo'
$vulkanDir = Join-Path $demo 'bin\vulkan'
$hasGpuBuild = (Test-Path (Join-Path $demo 'bin\cuda\llama-server.exe')) -or (Test-Path (Join-Path $demo 'bin\hip\llama-server.exe'))
if ((Test-Path (Join-Path $vulkanDir 'llama-server.exe')) -or $hasGpuBuild) {
    Write-Host 'Bonsai GPU build already present.'
} elseif (-not (Test-Path (Join-Path $env:WINDIR 'System32\vulkan-1.dll'))) {
    Write-Host 'No Vulkan runtime (graphics driver); Bonsai stays on the CPU.' -ForegroundColor Yellow
} elseif (-not (Test-Path (Join-Path $demo 'setup.ps1'))) {
    Write-Host "Bonsai-demo not found at $demo; run scripts\setup_windows.ps1, then this script again." -ForegroundColor Yellow
} else {
    # Use the same pinned fork release as the CPU build PrismML's setup installed.
    $tag = (Select-String -Path (Join-Path $demo 'setup.ps1') -Pattern '^\$ReleaseTag\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
    $asset = "llama-$tag-bin-win-vulkan-x64.zip"
    $zip = Join-Path ([System.IO.Path]::GetTempPath()) $asset
    Write-Host "Downloading Bonsai's Vulkan build ($asset, ~30 MB)..."
    try {
        Invoke-WebRequest -Uri "https://github.com/PrismML-Eng/llama.cpp/releases/download/$tag/$asset" -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath $vulkanDir -Force
        Remove-Item $zip -Force
        $devices = & (Join-Path $vulkanDir 'llama-server.exe') --list-devices 2>&1 | Select-String 'Vulkan\d'
        if ($devices) {
            Write-Host "Bonsai will use the GPU: $($devices[0].Line.Trim())" -ForegroundColor Green
        } else {
            Write-Host 'The Vulkan build found no GPU; removing it so Bonsai stays on the CPU.' -ForegroundColor Yellow
            Remove-Item $vulkanDir -Recurse -Force
        }
    } catch {
        Write-Host "Could not install the Vulkan build ($($_.Exception.Message)); Bonsai stays on the CPU." -ForegroundColor Yellow
    }
}
Write-Host 'Done. Start Beacon with: powershell -ExecutionPolicy Bypass -File scripts\run_windows.ps1' -ForegroundColor Green
