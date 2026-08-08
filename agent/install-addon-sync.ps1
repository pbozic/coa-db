# Registers a scheduled task that keeps the CoAFarm addon data current.
#   powershell -ExecutionPolicy Bypass -File agent\install-addon-sync.ps1
#
# Runs CoAFarmSync.exe --once every 15 minutes and at logon. The exe downloads
# the published data and rewrites Data.lua in the addon folder, which is safe
# even mid-session: WoW only reads that file. Use /reload in game to pick it up.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
# The windowless build: a console exe would flash a window into focus every
# fifteen minutes, which is worse than useless while you are playing.
$exe = Join-Path $root 'dist\CoAFarmSyncSilent.exe'
if (-not (Test-Path $exe)) {
    throw "CoAFarmSyncSilent.exe not found at $exe - build it first (see README)."
}

$action = New-ScheduledTaskAction -Execute $exe `
    -Argument '--once --log logs\addon-sync.log' -WorkingDirectory $root

# Scoped to this user: an unscoped -AtLogOn trigger registers for all users and
# needs an elevated shell.
$atLogon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$atLogon.Delay = 'PT3M'
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 15)
$trigger = @($atLogon, $repeat)

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -Hidden

Register-ScheduledTask -TaskName 'CoA Farm addon sync' -Action $action `
    -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Registered. The addon data refreshes every 15 minutes."
Write-Host "Type /reload in game when you want the client to pick it up."
