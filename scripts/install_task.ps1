<#
Registers or removes the Task Scheduler task that starts the receiver at logon.
It runs under pythonw, so no console window appears.

Run in the project folder. Use an Administrator PowerShell if Windows denies access:
    powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1
    powershell -ExecutionPolicy Bypass -File scripts\install_task.ps1 -Uninstall

-DryRun builds the task and prints it without registering anything.
#>
param(
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$taskName = "Balcony Station Receiver"
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root ".venv\Scripts\pythonw.exe"
$user = "$env:USERDOMAIN\$env:USERNAME"

if ($Uninstall) {
    if ($DryRun) {
        Write-Output "Would stop and remove task '$taskName'."
        exit 0
    }
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $taskName
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Output "Removed task '$taskName'."
    } else {
        Write-Output "Task '$taskName' is not installed."
    }
    exit 0
}

if (-not (Test-Path $pythonw)) {
    Write-Error "Missing $pythonw. Run 'uv sync' first."
}

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "-m receiver" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
# A laptop needs the battery options: tasks otherwise refuse to run on battery.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$task = New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings

if ($DryRun) {
    Write-Output "Would register task '$taskName' for $user"
    Write-Output "  Run:       $pythonw -m receiver"
    Write-Output "  Folder:    $root"
    Write-Output "  Trigger:   at logon"
    Write-Output "  Battery:   allowed ($(-not $task.Settings.DisallowStartIfOnBatteries))"
    Write-Output "  Restarts:  $($task.Settings.RestartCount) every $($task.Settings.RestartInterval)"
    Write-Output "  Time limit: $($task.Settings.ExecutionTimeLimit)"
    exit 0
}

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
Write-Output "Registered task '$taskName'. Start it now with: Start-ScheduledTask -TaskName '$taskName'"
