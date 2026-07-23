[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Import-Module (Join-Path $PSScriptRoot "project-processes.psm1") -Force
$paths = Get-ProjectLifecyclePaths

try {
    $shutdownResult = Stop-ProjectProcesses -Paths $paths
    foreach ($entry in $shutdownResult.Stopped) {
        Write-Output "Stopped $($entry.Name) PID $($entry.Record.ProcessId)."
    }
    if ($shutdownResult.Stopped.Count -eq 0) {
        Write-Output "No verified project process is running."
    }
} catch {
    Write-Error "Project shutdown failed: $($_.Exception.Message)"
    exit 1
}
