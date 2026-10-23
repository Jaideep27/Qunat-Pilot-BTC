# Register (or with -Remove, unregister) the QuantPilot ops tasks for the current user:
#   "QuantPilot Watchdog"  every 5 minutes and at logon  (scripts/watchdog.py)
#   "QuantPilot Backup"    daily at 02:30, run late if the laptop was asleep  (scripts/backup.py)
# They run only while the user is logged on, through pythonw.exe (no console window). No admin rights needed.
param([switch]$Remove)
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\pythonw.exe'
$path = '\QuantPilot\'
$names = 'QuantPilot Watchdog', 'QuantPilot Backup'
if ($Remove) {
  foreach ($n in $names) { Unregister-ScheduledTask -TaskName $n -TaskPath $path -Confirm:$false -ErrorAction SilentlyContinue }
  'Removed the QuantPilot ops tasks.'
  return
}
if (-not (Test-Path $py)) { throw "Missing $py; install the environment first." }
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$watch = New-ScheduledTaskAction -Execute $py -Argument ('"' + (Join-Path $root 'scripts\watchdog.py') + '"') -WorkingDirectory $root
$every = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$logon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$watchSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'QuantPilot Watchdog' -TaskPath $path -Action $watch -Trigger $every, $logon `
  -Principal $principal -Settings $watchSettings -Description 'Restarts stuck QuantPilot services and emails alerts.' -Force | Out-Null

$backup = New-ScheduledTaskAction -Execute $py -Argument ('"' + (Join-Path $root 'scripts\backup.py') + '"') -WorkingDirectory $root
$nightly = New-ScheduledTaskTrigger -Daily -At '02:30'
$backupSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'QuantPilot Backup' -TaskPath $path -Action $backup -Trigger $nightly `
  -Principal $principal -Settings $backupSettings -Description 'Nightly verified QuantPilot backup to data\backups and OneDrive.' -Force | Out-Null

Get-ScheduledTask -TaskPath $path | ForEach-Object {
  $info = $_ | Get-ScheduledTaskInfo
  '{0}: {1}, next run {2}' -f $_.TaskName, $_.State, $info.NextRunTime
}
