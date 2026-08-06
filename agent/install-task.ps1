# Registers a Windows scheduled task that publishes prices every 15 minutes.
#   powershell -ExecutionPolicy Bypass -File agent\install-task.ps1
$ErrorActionPreference = 'Stop'
$root   = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $python)) { $python = Join-Path $root '.venv\Scripts\python.exe' }

$action  = New-ScheduledTaskAction -Execute $python `
    -Argument 'watch_prices.py --once' -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 15)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName 'CoA High Risk prices' -Action $action `
    -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Registered. It publishes only when a new scan has landed."
