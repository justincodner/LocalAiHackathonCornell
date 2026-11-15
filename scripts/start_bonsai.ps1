# Emmet / B1. Start a local PrismML llama.cpp server (Bonsai 2) for Beacon on its own port.
#
# Two servers can run side by side, one per console:
#   powershell -ExecutionPolicy Bypass -File scripts\start_bonsai.ps1                       # 2B on :8080 (actions)
#   powershell -ExecutionPolicy Bypass -File scripts\start_bonsai.ps1 -Model 4B -Port 8082  # 4B on :8082 (questions)
#
# This starts llama-server.exe directly instead of going through PrismML's
# scripts\start_llama_server.ps1, which hard-codes port 8080 and refuses to start
# when anything answers on it. Always binds 127.0.0.1 (BONSAI_HOST is ignored).
# BONSAI_NGL overrides the GPU layer count; BONSAI_CTX sets the default -Context (total, split
# across the slots); BONSAI_SLOTS sets the slot count.
[CmdletBinding()]
param(
    [ValidateSet('2B', '4B')]
    [string]$Model = '2B',  # docs/bench.md: the 4B's prompt speed on the M1 misses the 2.5 s budget
    [ValidateRange(1, 65535)]
    [int]$Port = 8080,
    [string]$DemoPath = '',
    [int]$Context = 0
)

$ErrorActionPreference = 'Stop'

# $PSScriptRoot is empty inside param() defaults on Windows PowerShell 5.1, so resolve here.
if (-not $DemoPath) { $DemoPath = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'Bonsai-demo' }
if (-not (Test-Path -LiteralPath $DemoPath -PathType Container)) {
    Write-Host "[ERR] Bonsai-demo folder not found at $DemoPath. Run scripts\setup_windows.ps1 first." -ForegroundColor Red
    exit 1
}
$DemoPath = (Resolve-Path -LiteralPath $DemoPath).Path

# One slot per prompt family. The Brain prewarms six (loop.prompt_families) and pins each to its
# own slot (llm.py, id_slot); the seventh serves anything else. Bonsai 2 is a hybrid model, so a
# slot shared by several families kept losing their cached prefixes (10-19 s steps; docs/bench.md).
$slots = 7
$envSlots = 0
if ($env:BONSAI_SLOTS -and [int]::TryParse($env:BONSAI_SLOTS, [ref]$envSlots) -and $envSlots -gt 0) { $slots = $envSlots }

if ($Context -le 0) {
    $envCtx = 0
    if ($env:BONSAI_CTX -and [int]::TryParse($env:BONSAI_CTX, [ref]$envCtx) -and $envCtx -gt 0) {
        $Context = $envCtx
    } else {
        $Context = 6144 * $slots  # an action step peaked at ~3,800 tokens
    }
}

# Binary: same preference order as PrismML's launcher.
$binRel = $null
foreach ($cand in @('bin\cuda\llama-server.exe', 'bin\hip\llama-server.exe', 'bin\vulkan\llama-server.exe', 'bin\cpu\llama-server.exe')) {
    if (Test-Path -LiteralPath (Join-Path $DemoPath $cand) -PathType Leaf) { $binRel = $cand; break }
}
if (-not $binRel) {
    Write-Host "[ERR] llama-server.exe not found under $DemoPath\bin\{cuda,hip,vulkan,cpu}. Run scripts\setup_windows.ps1 first." -ForegroundColor Red
    exit 1
}
$bin = Join-Path $DemoPath $binRel

# Model: exactly one PQ2_0 gguf (never a projector, F16 or other quant).
$modelDir = Join-Path $DemoPath "models\bonsai2-gguf\$Model"
$ggufs = @(Get-ChildItem -LiteralPath $modelDir -Filter '*-PQ2_0.gguf' -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notlike '*mmproj*' })
if ($ggufs.Count -eq 0) {
    Write-Host "[ERR] No *-PQ2_0.gguf for Bonsai $Model in $modelDir. Run scripts\setup_windows.ps1 first." -ForegroundColor Red
    exit 1
}
if ($ggufs.Count -gt 1) {
    Write-Host "[ERR] Expected one *-PQ2_0.gguf in $modelDir, found $($ggufs.Count): $(($ggufs | ForEach-Object Name) -join ', ')" -ForegroundColor Red
    exit 1
}
$modelFile = $ggufs[0]

# Refuse to start a second server on a busy port.
$busy = $false
try {
    $null = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 2
    $busy = $true
} catch [System.Net.WebException] {
    # An HTTP error status (e.g. 503 while a model loads) still means something is listening.
    if ($_.Exception.Response) { $busy = $true }
} catch {}
if ($busy) {
    Write-Host "[ERR] Port $Port is busy: http://127.0.0.1:$Port/health already responds (llama-server running?). Pick another -Port." -ForegroundColor Red
    exit 1
}

$ngl = if ($env:BONSAI_NGL) { $env:BONSAI_NGL } elseif ($binRel -like 'bin\cpu\*') { '0' } else { '99' }

# Windows PowerShell 5.1 does not escape embedded quotes for native commands.
$templateKwargs = if ($PSVersionTable.PSEdition -eq 'Desktop') { '{\"enable_thinking\":false}' } else { '{"enable_thinking":false}' }

# --no-kv-unified: with a shared cache, llama-server clears idle slots when another one starts.
# --cache-ram 0: the slots keep their own prefixes, so the RAM prompt cache (4-5 prompts, up to
# 8 GB) only added ~0.1-0.3 s of saving per request.
# Sampling: PrismML's non-thinking small-model profile (small_model_sampling.ps1, BONSAI_THINKING=0).
$serverArgs = @(
    '-m', $modelFile.FullName,
    '--host', '127.0.0.1',
    '--port', "$Port",
    '-ngl', $ngl, '-fa', 'on',
    '-c', "$Context",
    '-np', "$slots", '--no-kv-unified', '--cache-ram', '0',
    '--jinja',
    '--temp', '0.7', '--top-p', '0.8', '--top-k', '20', '--min-p', '0',
    '--presence-penalty', '1.5', '--repeat-penalty', '1.0', '--repeat-last-n', '64',
    '--chat-template-kwargs', $templateKwargs
)

$nglNote = if ($env:BONSAI_NGL) { 'BONSAI_NGL' } else { 'auto' }
Write-Host ''
Write-Host "=== Bonsai $Model llama-server ==="
Write-Host "  Model:   $($modelFile.Name)"
Write-Host "  URL:     http://127.0.0.1:$Port  (API /v1/chat/completions)"
Write-Host "  Binary:  $bin"
Write-Host "  GPU:     -ngl $ngl ($nglNote)"
Write-Host "  Context: -c $Context, $slots slots of $([math]::Floor($Context / $slots)) tokens"
Write-Host '  Press Ctrl+C to stop.'
Write-Host ''

# Native stderr must not become a terminating error under 'Stop'.
$ErrorActionPreference = 'Continue'
& $bin @serverArgs
exit $LASTEXITCODE
