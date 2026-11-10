param(
    [switch]$StopController
)

$ErrorActionPreference = "Stop"
$taskName = "Agent Roundtable Controller"
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Output "STARTUP TASK: not installed"
    exit 0
}

if ($StopController) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
}
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Output "STARTUP TASK REMOVED: $taskName"
