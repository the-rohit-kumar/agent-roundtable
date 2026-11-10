param(
    [string]$ConfigPath = "$env:LOCALAPPDATA\AgentRoundtable\config\config.yaml",
    [string]$UvPath = "uv"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$uv = Get-Command $UvPath -ErrorAction Stop
$logDir = Join-Path $env:LOCALAPPDATA "AgentRoundtable\logs"
$logFile = Join-Path $logDir "startup-wrapper.log"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$started = [DateTimeOffset]::Now.ToString("o")
Add-Content -LiteralPath $logFile -Value "$started START pid=$PID"
Push-Location $repoRoot
try {
    & $uv.Source run --project $repoRoot agent-roundtable --config $ConfigPath run
    $exitCode = $LASTEXITCODE
    $finished = [DateTimeOffset]::Now.ToString("o")
    Add-Content -LiteralPath $logFile -Value "$finished EXIT code=$exitCode"
    exit $exitCode
} catch {
    $finished = [DateTimeOffset]::Now.ToString("o")
    Add-Content -LiteralPath $logFile -Value "$finished WRAPPER_ERROR type=$($_.Exception.GetType().Name)"
    exit 3
} finally {
    Pop-Location
}
