# Emmet / B1. Install the Bonsai 2 GGUF packs and sync the Beacon Brain.
[CmdletBinding()]
param(
    [string]$DemoPath = '',
    [ValidateSet('2B', '4B')]
    [string[]]$Models = @('2B', '4B')
)

$ErrorActionPreference = 'Stop'
# $PSScriptRoot is empty inside param() defaults on Windows PowerShell 5.1, so resolve here.
if (-not $DemoPath) { $DemoPath = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'Bonsai-demo' }
$repoRoot = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path -LiteralPath $DemoPath)) {
    git clone 'https://github.com/PrismML-Eng/Bonsai-demo.git' $DemoPath
    if ($LASTEXITCODE -ne 0) { throw 'Could not clone PrismML Bonsai-demo.' }
}
$setup = Join-Path (Resolve-Path -LiteralPath $DemoPath).Path 'setup.ps1'
if (-not (Test-Path -LiteralPath $setup -PathType Leaf)) {
    throw "Could not find PrismML's setup.ps1 at $setup."
}

$previousFamily = $env:BONSAI_FAMILY
$previousModel = $env:BONSAI_MODEL
Push-Location
try {
    $env:BONSAI_FAMILY = 'bonsai2'
    Set-Location -LiteralPath (Split-Path -Parent $setup)
    foreach ($model in $Models) {
        $env:BONSAI_MODEL = $model
        & $setup
        if (-not $?) { throw "PrismML setup failed for Bonsai $model." }
    }
}
finally {
    Pop-Location
    $env:BONSAI_FAMILY = $previousFamily
    $env:BONSAI_MODEL = $previousModel
}

uv sync --project (Join-Path $repoRoot 'brain')
if ($LASTEXITCODE -ne 0) { throw 'Could not sync Beacon Brain dependencies.' }
