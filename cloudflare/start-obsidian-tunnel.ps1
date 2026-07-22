[CmdletBinding()]
param(
    [switch]$Background
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

foreach ($proxyVariable in @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")) {
    Remove-Item -Path "Env:$proxyVariable" -ErrorAction SilentlyContinue
}

$tunnelName = "obsidian-panspan-cloud"
$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$configPath = Join-Path $scriptDirectory "config.yml"
$credentialsPath = Join-Path $scriptDirectory "$tunnelName.json"
$pidPath = Join-Path $scriptDirectory "$tunnelName.pid"
$stdoutPath = Join-Path $scriptDirectory "$tunnelName.stdout.log"
$stderrPath = Join-Path $scriptDirectory "$tunnelName.stderr.log"

function Fail([string]$message) {
    Write-Error $message
    exit 1
}

try {
    $cloudflared = Get-Command cloudflared -ErrorAction Stop
} catch {
    Fail "cloudflared is not installed or is not on PATH."
}

if (-not (Test-Path -LiteralPath $configPath)) {
    Fail "Tunnel config is missing: $configPath"
}

if (-not (Test-Path -LiteralPath $credentialsPath)) {
    Fail "Tunnel credentials are missing: $credentialsPath"
}

try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:6240/api/health" -TimeoutSec 5
} catch {
    Fail "The local Obsidian workspace is not healthy at http://127.0.0.1:6240. Start it with npm run start first."
}

if ($health.status -ne "ok" -or $health.service -ne "obsidian-personal-knowledge-platform") {
    Fail "The service on port 6240 is not the expected Obsidian workspace."
}

& $cloudflared.Source tunnel --config $configPath ingress validate
if ($LASTEXITCODE -ne 0) {
    Fail "Tunnel ingress validation failed."
}

if (-not $Background) {
    & $cloudflared.Source tunnel --config $configPath run
    exit $LASTEXITCODE
}

if (Test-Path -LiteralPath $pidPath) {
    $existingPid = Get-Content -Raw -LiteralPath $pidPath
    if ($existingPid -match '^\d+$' -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
        Fail "The $tunnelName tunnel is already running with PID $existingPid."
    }
    Remove-Item -LiteralPath $pidPath -Force
}

$process = Start-Process -FilePath $cloudflared.Source -ArgumentList @(
    "tunnel", "--config", $configPath, "run"
) -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath -WindowStyle Hidden -PassThru

$process.Id | Set-Content -LiteralPath $pidPath -NoNewline
Write-Output "Started $tunnelName with PID $($process.Id)."
