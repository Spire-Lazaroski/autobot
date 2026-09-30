# Automatic start at log-on for the Spire paper-trading bot and its lab.
#   powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -Action on|off|status|remove
# on     : create (or re-enable) two tasks for the current user, each 2 minutes after log-on:
#          "Spire autobot"      -> .venv\Scripts\pythonw.exe run_bot.py (the bot under its supervisor, no window)
#          "Spire autobot lab"  -> start_lab.bat auto (API, dashboard, news collector; minimized, no browser)
# off    : disable both tasks (they stay in Task Scheduler; "on" enables them again)
# remove : delete both tasks
# The bot is paper trading only; a second copy exits quietly.
param([ValidateSet("on", "off", "status", "remove")][string]$Action = "status")

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$User = "$env:USERDOMAIN\$env:USERNAME"
$Tasks = @(
    @{ Name = "Spire autobot"; Exe = Join-Path $Root ".venv\Scripts\pythonw.exe"; Args = "run_bot.py";
       Description = "Spire Analytics paper-trading bot (run_bot.py supervisor: restarts after a crash, max 3 per hour). Paper only." },
    @{ Name = "Spire autobot lab"; Exe = "$env:WINDIR\System32\cmd.exe"; Args = "/c start_lab.bat auto";
       Description = "Spire Analytics lab: API, dashboard and news collector (minimized windows)." }
)

function Show-Status {
    foreach ($t in $Tasks) {
        $task = Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue
        if (-not $task) { "{0,-18} not installed" -f $t.Name; continue }
        $info = Get-ScheduledTaskInfo -TaskName $t.Name
        "{0,-18} {1,-9} trigger: at log-on of {2}, delay {3} | last run {4} (result {5})" -f $t.Name, $task.State,
            $task.Triggers[0].UserId, $task.Triggers[0].Delay, $info.LastRunTime, $info.LastTaskResult
    }
}

switch ($Action) {
    "on" {
        foreach ($t in $Tasks) {
            if (-not (Test-Path $t.Exe)) { throw "Missing $($t.Exe). Run start_lab.bat once first to create the .venv." }
            $taskAction = New-ScheduledTaskAction -Execute $t.Exe -Argument $t.Args -WorkingDirectory $Root
            $trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
            $trigger.Delay = "PT2M"  # 2 minutes, so the network is up
            $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
            $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
            Register-ScheduledTask -TaskName $t.Name -Action $taskAction -Trigger $trigger -Settings $settings `
                -Principal $principal -Description $t.Description -Force | Out-Null
            Enable-ScheduledTask -TaskName $t.Name | Out-Null
        }
        "Automatic start is ON (2 minutes after you log on)."
    }
    "off" {
        foreach ($t in $Tasks) { if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) { Disable-ScheduledTask -TaskName $t.Name | Out-Null } }
        "Automatic start is OFF. Programs already running keep running (stop the bot with stop_bot.bat)."
    }
    "remove" {
        foreach ($t in $Tasks) { if (Get-ScheduledTask -TaskName $t.Name -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $t.Name -Confirm:$false } }
        "Automatic start tasks removed."
    }
}
Show-Status
