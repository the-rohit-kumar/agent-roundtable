# Agent Roundtable - Windows environment doctor (Milestone 0)
# Read-only diagnostic. Records versions/builds of Windows, Python, Git, Codex CLI,
# Antigravity CLI and basic disk space. Prints a PASS/FAIL summary.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\doctor.ps1
#
# ARCHITECTURE.md scope note: this script NEVER reads, copies, or prints any
# credential content. It only reports version strings and existence checks.

$ErrorActionPreference = "Continue"
$results = @()

function Get-FirstLine([string]$Stdout) {
    if ([string]::IsNullOrWhiteSpace($Stdout)) { return "(no output)" }
    return ($Stdout -split "`r?`n")[0].Trim()
}

function Add-Row([string]$Check, [string]$Status, [string]$Detail) {
    $script:results += [pscustomobject]@{ Check=$Check; Status=$Status; Detail=$Detail }
}

function Add-CliCheck([string]$Name, [scriptblock]$Probe) {
    try {
        $out = & $Probe 2>&1 | Out-String
        Add-Row $Name "PASS" (Get-FirstLine $out)
    } catch {
        Add-Row $Name "FAIL" $_.Exception.Message.Trim()
    }
}

# --- Windows version / build ---
$os = Get-CimInstance Win32_OperatingSystem
$build = [System.Environment]::OSVersion.Version.ToString()
Add-Row "windows" "INFO" "$($os.Caption) build $build ($($os.OSArchitecture))"

# --- Disk space on profile drive ---
$profileDrive = Get-PSDrive -Name ($env:USERPROFILE.Substring(0,1))
$gb = [math]::Round($profileDrive.Free/1GB, 1)
Add-Row "disk_free_gb" "INFO" "$gb GiB free"

# --- Python ---
Add-CliCheck "python" { python --version }

# --- Git ---
Add-CliCheck "git" { git --version }

# --- Codex CLI ---
Add-CliCheck "codex_version" { codex --version }

# --- Codex auth (status only; never reads token contents) ---
Add-CliCheck "codex_auth" {
    $out = codex login status 2>&1 | Out-String
    if (($out -notmatch "(?i)not logged in|logged out") -and ($out -match "(?i)logged in")) { $out } else { throw ($out.Trim()) }
}

# --- Antigravity CLI ---
$agyCmd = Get-Command agy -ErrorAction SilentlyContinue
if ($agyCmd) {
    Add-CliCheck "agy" { agy --version }
    # Sandbox canary is executed separately after CLI installation (M0 criterion;
    # see docs\environment-report.md and DECISIONS.md D5).
} else {
    Add-Row "agy" "FAIL" "'agy' not found on PATH (Antigravity CLI)"
    Add-Row "agy_status" "INFO" "Antigravity adapter stays DISABLED/SANDBOX_UNVERIFIED until CLI installed + authenticated"
}

# --- Data directory (existence only) ---
$credPath = "$env:LOCALAPPDATA\AgentRoundtable"
Add-Row "data_dir" "INFO" $(if (Test-Path $credPath) { $credPath } else { "(not created yet)" })

# --- Summary ---
$failures = @($results | Where-Object { $_.Status -eq "FAIL" })
$results | Format-Table -AutoSize | Out-String -Width 200
if ($failures.Count -gt 0) {
    Write-Output "DOCTOR RESULT: DEGRADED ($($failures.Count) failing check(s))"
    if (-not $agyCmd) { Write-Output "Note: Antigravity CLI missing -> the system runs in documented single-agent (Codex-only) degraded mode." }
    exit 2
} else {
    Write-Output "DOCTOR RESULT: PASS"
    exit 0
}
