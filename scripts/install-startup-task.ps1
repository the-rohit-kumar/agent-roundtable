param(
    [string]$ConfigPath = "$env:LOCALAPPDATA\AgentRoundtable\config\config.yaml"
)

$ErrorActionPreference = "Stop"
$taskName = "Agent Roundtable Controller"
$scriptPath = (Resolve-Path (Join-Path $PSScriptRoot "start-controller.ps1")).Path
$config = (Resolve-Path -LiteralPath $ConfigPath).Path
$uv = (Get-Command uv -ErrorAction Stop).Source
$powerShell = (Get-Command powershell.exe -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $config -PathType Leaf)) {
    throw "Configuration file not found: $ConfigPath"
}

$argumentLine = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$scriptPath`" -ConfigPath `"$config`" -UvPath `"$uv`""
$action = New-ScheduledTaskAction -Execute $powerShell -Argument $argumentLine
$trigger = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
$principal = New-ScheduledTaskPrincipal `
    -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description "Starts Agent Roundtable at this user's logon and restarts it after failures." -Force | Out-Null
Write-Output "STARTUP TASK INSTALLED: $taskName (current user, limited privileges, up to 3 restarts at 5 minute intervals)"
Write-Output "The controller runs only while this user is logged in and Windows is awake."
