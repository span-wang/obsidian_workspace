Set-StrictMode -Version Latest

function Get-ProjectLifecyclePaths {
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $runtimeDirectory = Join-Path $projectRoot "output\runtime"
    $tunnelDirectory = Join-Path $projectRoot "cloudflare"

    [pscustomobject]@{
        ProjectRoot = $projectRoot
        RuntimeDirectory = $runtimeDirectory
        StatePath = Join-Path $runtimeDirectory "project-processes.json"
        ServiceStdoutPath = Join-Path $runtimeDirectory "workbench.stdout.log"
        ServiceStderrPath = Join-Path $runtimeDirectory "workbench.stderr.log"
        WorkspaceStartScript = Join-Path $PSScriptRoot "run-workbench.ps1"
        TunnelStartScript = Join-Path $tunnelDirectory "start-obsidian-tunnel.ps1"
        TunnelConfigPath = Join-Path $tunnelDirectory "config.yml"
        TunnelPidPath = Join-Path $tunnelDirectory "obsidian-panspan-cloud.pid"
    }
}

function Test-ProjectHealth {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:6240/api/health" -TimeoutSec 2
        return $health.status -eq "ok" -and $health.service -eq "obsidian-personal-knowledge-platform"
    } catch {
        return $false
    }
}

function Wait-ForProjectHealth {
    param(
        [ValidateRange(1, 300)]
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-ProjectHealth) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Get-ProjectProcessSnapshot {
    param(
        [Parameter(Mandatory)]
        [int]$ProcessId
    )

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $null
    }

    try {
        $commandLine = (Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop).CommandLine
        if ([string]::IsNullOrWhiteSpace($commandLine)) {
            return $null
        }
        return [pscustomobject]@{
            ProcessId = $process.Id
            ProcessName = $process.ProcessName
            StartTimeUtc = $process.StartTime.ToUniversalTime().ToString("o")
            CommandLine = $commandLine
        }
    } catch {
        return $null
    }
}

function New-ProjectProcessRecord {
    param(
        [Parameter(Mandatory)]
        [System.Diagnostics.Process]$Process,
        [Parameter(Mandatory)]
        [ValidateNotNullOrEmpty()]
        [string]$CommandMarker
    )

    $snapshot = Get-ProjectProcessSnapshot -ProcessId $Process.Id
    if ($null -eq $snapshot) {
        throw "Could not capture the process identity for PID $($Process.Id)."
    }

    return [pscustomobject]@{
        ProcessId = $snapshot.ProcessId
        ProcessName = $snapshot.ProcessName
        StartTimeUtc = $snapshot.StartTimeUtc
        CommandMarker = $CommandMarker
    }
}

function Test-ProjectProcessRecord {
    param(
        [Parameter(Mandatory)]
        [psobject]$Record
    )

    foreach ($propertyName in @("ProcessId", "ProcessName", "StartTimeUtc", "CommandMarker")) {
        if ($null -eq $Record.PSObject.Properties[$propertyName] -or [string]::IsNullOrWhiteSpace([string]$Record.$propertyName)) {
            return [pscustomobject]@{ IsValid = $false; Reason = "The process record is missing $propertyName." }
        }
    }

    $snapshot = Get-ProjectProcessSnapshot -ProcessId ([int]$Record.ProcessId)
    if ($null -eq $snapshot) {
        return [pscustomobject]@{ IsValid = $false; Reason = "PID $($Record.ProcessId) is not available for verification." }
    }
    if ($snapshot.ProcessName -ine [string]$Record.ProcessName) {
        return [pscustomobject]@{ IsValid = $false; Reason = "PID $($Record.ProcessId) has a different process name." }
    }
    try {
        $recordedStartTime = ([datetime]$Record.StartTimeUtc).ToUniversalTime()
        $currentStartTime = ([datetime]$snapshot.StartTimeUtc).ToUniversalTime()
    } catch {
        return [pscustomobject]@{ IsValid = $false; Reason = "PID $($Record.ProcessId) has an invalid recorded start time." }
    }
    if ([math]::Abs(($currentStartTime - $recordedStartTime).TotalSeconds) -gt 1) {
        return [pscustomobject]@{ IsValid = $false; Reason = "PID $($Record.ProcessId) has a different start time." }
    }
    if ($snapshot.CommandLine.IndexOf([string]$Record.CommandMarker, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
        return [pscustomobject]@{ IsValid = $false; Reason = "PID $($Record.ProcessId) does not match the recorded command marker." }
    }
    return [pscustomobject]@{ IsValid = $true; Reason = $null }
}

function Read-ProjectLifecycleState {
    param(
        [Parameter(Mandatory)]
        [string]$StatePath
    )

    try {
        return Get-Content -Raw -LiteralPath $StatePath | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw "Runtime state is unreadable: $StatePath"
    }
}

function Write-ProjectLifecycleState {
    param(
        [Parameter(Mandatory)]
        [psobject]$State,
        [Parameter(Mandatory)]
        [string]$RuntimeDirectory,
        [Parameter(Mandatory)]
        [string]$StatePath
    )

    New-Item -ItemType Directory -Path $RuntimeDirectory -Force | Out-Null
    $State | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $StatePath -Encoding utf8
}

function Remove-ProjectLifecycleState {
    param(
        [Parameter(Mandatory)]
        [string]$StatePath
    )

    Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
}

function Stop-ProjectProcessTree {
    param(
        [Parameter(Mandatory)]
        [int]$ProcessId
    )

    if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) {
        return
    }

    $taskkillPath = Join-Path $env:SystemRoot "System32\taskkill.exe"
    & $taskkillPath /PID $ProcessId /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not stop the process tree rooted at PID $ProcessId."
    }
}

function Get-DedicatedTunnelRecord {
    param(
        [Parameter(Mandatory)]
        [psobject]$Paths
    )

    if (-not (Test-Path -LiteralPath $Paths.TunnelPidPath)) {
        return $null
    }

    try {
        $tunnelProcessId = (Get-Content -Raw -LiteralPath $Paths.TunnelPidPath).Trim()
        if ($tunnelProcessId -notmatch "^\d+$") {
            return $null
        }
        $tunnelProcess = Get-Process -Id ([int]$tunnelProcessId) -ErrorAction Stop
        $record = New-ProjectProcessRecord -Process $tunnelProcess -CommandMarker $Paths.TunnelConfigPath
        if ((Test-ProjectProcessRecord -Record $record).IsValid) {
            return $record
        }
    } catch {
        return $null
    }
    return $null
}

function Get-VerifiedWorkspaceRecord {
    param(
        [Parameter(Mandatory)]
        [psobject]$Paths
    )

    if (-not (Test-ProjectHealth)) {
        return $null
    }

    try {
        $listeners = @(Get-NetTCPConnection -LocalAddress "127.0.0.1" -LocalPort 6240 -State Listen -ErrorAction Stop)
        if ($listeners.Count -ne 1) {
            return $null
        }

        $allProcesses = Get-CimInstance -ClassName Win32_Process -ErrorAction Stop
        $byProcessId = @{}
        foreach ($processInfo in $allProcesses) {
            $byProcessId[[int]$processInfo.ProcessId] = $processInfo
        }

        $currentProcessId = [int]$listeners[0].OwningProcess
        $fallbackProcessId = $null
        while ($byProcessId.ContainsKey($currentProcessId)) {
            $processInfo = $byProcessId[$currentProcessId]
            $commandLine = [string]$processInfo.CommandLine
            if ($commandLine.IndexOf($Paths.WorkspaceStartScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $workspaceProcess = Get-Process -Id $currentProcessId -ErrorAction Stop
                return New-ProjectProcessRecord -Process $workspaceProcess -CommandMarker $Paths.WorkspaceStartScript
            }
            if ($fallbackProcessId -eq $null -and $commandLine.IndexOf("-m api.main", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                $fallbackProcessId = $currentProcessId
            }
            if ($processInfo.ParentProcessId -eq 0 -or $processInfo.ParentProcessId -eq $currentProcessId) {
                break
            }
            $currentProcessId = [int]$processInfo.ParentProcessId
        }

        if ($fallbackProcessId -ne $null) {
            $workspaceProcess = Get-Process -Id $fallbackProcessId -ErrorAction Stop
            return New-ProjectProcessRecord -Process $workspaceProcess -CommandMarker "-m api.main"
        }
    } catch {
        return $null
    }
    return $null
}

function Stop-ProjectProcesses {
    param(
        [Parameter(Mandatory)]
        [psobject]$Paths
    )

    $records = @()
    $tunnelRecord = Get-DedicatedTunnelRecord -Paths $Paths
    if ($null -ne $tunnelRecord) {
        $records += [pscustomobject]@{ Name = "Tunnel"; Record = $tunnelRecord }
    }
    $workspaceRecord = Get-VerifiedWorkspaceRecord -Paths $Paths
    if ($null -ne $workspaceRecord) {
        $records += [pscustomobject]@{ Name = "Workspace"; Record = $workspaceRecord }
    }

    if (Test-Path -LiteralPath $Paths.StatePath) {
        try {
            $state = Read-ProjectLifecycleState -StatePath $Paths.StatePath
            foreach ($entry in @(
                [pscustomobject]@{ Name = "Tunnel"; Record = $state.Tunnel },
                [pscustomobject]@{ Name = "Workspace"; Record = $state.Service }
            )) {
                if ($null -ne $entry.Record -and (Test-ProjectProcessRecord -Record $entry.Record).IsValid) {
                    $records += $entry
                }
            }
        } catch {
            # A malformed state file must not prevent discovery of live project processes.
        }
    }

    $uniqueRecords = @($records | Group-Object { [string]$_.Record.ProcessId } | ForEach-Object { $_.Group[0] })
    $stopped = @()
    foreach ($entry in ($uniqueRecords | Sort-Object { if ($_.Name -eq "Tunnel") { 0 } else { 1 } })) {
        Stop-ProjectProcessTree -ProcessId $entry.Record.ProcessId
        $stopped += $entry
    }

    if (Test-Path -LiteralPath $Paths.TunnelPidPath) {
        $tunnelProcessId = (Get-Content -Raw -LiteralPath $Paths.TunnelPidPath).Trim()
        if ($tunnelProcessId -notmatch "^\d+$" -or $null -eq (Get-Process -Id ([int]$tunnelProcessId) -ErrorAction SilentlyContinue)) {
            Remove-Item -LiteralPath $Paths.TunnelPidPath -Force
        } elseif ($stopped.Record.ProcessId -contains [int]$tunnelProcessId) {
            Remove-Item -LiteralPath $Paths.TunnelPidPath -Force
        }
    }
    Remove-ProjectLifecycleState -StatePath $Paths.StatePath

    return [pscustomobject]@{ Stopped = $stopped }
}

Export-ModuleMember -Function @(
    "Get-ProjectLifecyclePaths",
    "Test-ProjectHealth",
    "Wait-ForProjectHealth",
    "New-ProjectProcessRecord",
    "Test-ProjectProcessRecord",
    "Read-ProjectLifecycleState",
    "Write-ProjectLifecycleState",
    "Remove-ProjectLifecycleState",
    "Stop-ProjectProcessTree",
    "Get-DedicatedTunnelRecord",
    "Get-VerifiedWorkspaceRecord",
    "Stop-ProjectProcesses"
)
