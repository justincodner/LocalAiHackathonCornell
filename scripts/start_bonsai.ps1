# Emmet / B1. Start the local PrismML llama.cpp server for Beacon.
[CmdletBinding()]
param(
    [ValidateSet('2B', '4B')]
    [string]$Model = '2B',  # docs/bench.md: the 4B's prompt speed on the M1 misses the 2.5 s budget
    [string]$DemoPath = ''
)

$ErrorActionPreference = 'Stop'
# $PSScriptRoot is empty inside param() defaults on Windows PowerShell 5.1, so resolve here.
if (-not $DemoPath) { $DemoPath = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'Bonsai-demo' }
$launcher = Join-Path $DemoPath 'scripts\start_llama_server.ps1'
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Bonsai launcher missing at $launcher. Run scripts\setup_windows.ps1 first."
}
$launcher = (Resolve-Path -LiteralPath $launcher).Path

$previousFamily = $env:BONSAI_FAMILY
$previousModel = $env:BONSAI_MODEL
$previousThinking = $env:BONSAI_THINKING
$previousContext = $env:BONSAI_CTX
$previousHost = $env:BONSAI_HOST
Push-Location
try {
    $env:BONSAI_FAMILY = 'bonsai2'
    $env:BONSAI_MODEL = $Model
    $env:BONSAI_THINKING = '0'
    $env:BONSAI_CTX = '16384'
    $env:BONSAI_HOST = $null
    & $launcher -np 1
    if (-not $?) { throw "PrismML launcher failed for Bonsai $Model." }
}
finally {
    Pop-Location
    $env:BONSAI_FAMILY = $previousFamily
    $env:BONSAI_MODEL = $previousModel
    $env:BONSAI_THINKING = $previousThinking
    $env:BONSAI_CTX = $previousContext
    $env:BONSAI_HOST = $previousHost
}
