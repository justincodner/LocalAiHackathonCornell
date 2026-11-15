# Emmet. Stop local Beacon on Windows: the Brain, the Windows Helper (or Mock Helper) and Bonsai's llama-server.
$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$python = (Join-Path $repo 'brain\.venv\Scripts\python.exe').ToLower()

Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object {
    $_.ExecutablePath -and $_.ExecutablePath.ToLower() -eq $python -and
    $_.CommandLine -match '-m (beacon_brain|win_helper|mock_helper)'
} | ForEach-Object {
    Write-Host "Stopping $($_.CommandLine -replace '^.*-m ', '') (pid $($_.ProcessId))"
    Stop-Process -Id $_.ProcessId -Force
}
Get-Process llama-server -ErrorAction SilentlyContinue | ForEach-Object {
    Write-Host "Stopping llama-server (pid $($_.Id))"
    Stop-Process -Id $_.Id -Force
}
