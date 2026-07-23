[CmdletBinding()]
param(
    [ValidateRange(1, 300)]
    [int]$HealthTimeoutSeconds = 90
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Import-Module (Join-Path $PSScriptRoot "project-processes.psm1") -Force
$paths = Get-ProjectLifecyclePaths
$shellExecutable = if ($PSVersionTable.PSEdition -eq "Core") {
    Join-Path $PSHOME "pwsh.exe"
} else {
    Join-Path $PSHOME "powershell.exe"
}
$serviceRecord = $null
$tunnelRecord = $null
$stateWritten = $false

try {
    $restartResult = Stop-ProjectProcesses -Paths $paths
    foreach ($entry in $restartResult.Stopped) {
        Write-Output "Stopped existing $($entry.Name) PID $($entry.Record.ProcessId)."
    }

    New-Item -ItemType Directory -Path $paths.RuntimeDirectory -Force | Out-Null
    $serviceArguments = '-NoProfile -File "{0}"' -f $paths.WorkspaceStartScript
    $serviceProcess = Start-Process -FilePath $shellExecutable -ArgumentList $serviceArguments `
        -WorkingDirectory $paths.ProjectRoot -RedirectStandardOutput $paths.ServiceStdoutPath `
        -RedirectStandardError $paths.ServiceStderrPath -WindowStyle Hidden -PassThru
    $serviceRecord = New-ProjectProcessRecord -Process $serviceProcess -CommandMarker $paths.WorkspaceStartScript
    Write-ProjectLifecycleState -State ([pscustomobject]@{
        Version = 1
        CreatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
        Service = $serviceRecord
        Tunnel = $null
    }) -RuntimeDirectory $paths.RuntimeDirectory -StatePath $paths.StatePath
    $stateWritten = $true

    if (-not (Wait-ForProjectHealth -TimeoutSeconds $HealthTimeoutSeconds)) {
        throw "The local workspace did not become healthy within $HealthTimeoutSeconds seconds."
    }
    $serviceValidation = Test-ProjectProcessRecord -Record $serviceRecord
    if (-not $serviceValidation.IsValid) {
        throw "The workspace process could not be verified after startup: $($serviceValidation.Reason)"
    }

    $tunnelArguments = '-NoProfile -File "{0}" -Background' -f $paths.TunnelStartScript
    $tunnelStarter = Start-Process -FilePath $shellExecutable -ArgumentList $tunnelArguments `
        -WorkingDirectory $paths.ProjectRoot -PassThru -WindowStyle Hidden
    $tunnelDeadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $tunnelDeadline) {
        if (Test-Path -LiteralPath $paths.TunnelPidPath) {
            $tunnelProcessId = (Get-Content -Raw -LiteralPath $paths.TunnelPidPath).Trim()
            if ($tunnelProcessId -match "^\d+$") {
                $tunnelProcess = Get-Process -Id ([int]$tunnelProcessId) -ErrorAction SilentlyContinue
                if ($null -ne $tunnelProcess) {
                    $tunnelRecord = New-ProjectProcessRecord -Process $tunnelProcess -CommandMarker $paths.TunnelConfigPath
                    $tunnelValidation = Test-ProjectProcessRecord -Record $tunnelRecord
                    if ($tunnelValidation.IsValid) {
                        break
                    }
                    $tunnelRecord = $null
                }
            }
        }
        if ($tunnelStarter.HasExited) {
            throw "The dedicated Tunnel failed to start. Check cloudflare logs for details."
        }
        Start-Sleep -Milliseconds 100
    }
    if ($null -eq $tunnelRecord) {
        throw "The dedicated Tunnel did not become available within 10 seconds."
    }

    Write-ProjectLifecycleState -State ([pscustomobject]@{
        Version = 1
        CreatedAtUtc = (Get-Date).ToUniversalTime().ToString("o")
        Service = $serviceRecord
        Tunnel = $tunnelRecord
    }) -RuntimeDirectory $paths.RuntimeDirectory -StatePath $paths.StatePath
    Write-Output "Started project processes: service PID $($serviceRecord.ProcessId), tunnel PID $($tunnelRecord.ProcessId)."
} catch {
    if ($null -ne $tunnelRecord -and (Test-ProjectProcessRecord -Record $tunnelRecord).IsValid) {
        Stop-ProjectProcessTree -ProcessId $tunnelRecord.ProcessId
    }
    if ($null -ne $serviceRecord -and (Test-ProjectProcessRecord -Record $serviceRecord).IsValid) {
        Stop-ProjectProcessTree -ProcessId $serviceRecord.ProcessId
    }
    if ($stateWritten) {
        Remove-ProjectLifecycleState -StatePath $paths.StatePath
    }
    Write-Error "Project startup failed: $($_.Exception.Message)"
    exit 1
}
