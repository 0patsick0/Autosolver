param(
  [string]$BenchmarkPath = "examples/benchmarks/benchmark_manifest.json",
  [int]$Rounds = 2,
  [string]$DashboardOutput = "dashboard/public/replay-data.json",
  [string]$EventsPath = "examples/events/live_dashboard.jsonl",
  [string]$SummaryOutput = "examples/live_dashboard_summary.json",
  [string]$SearchSpacePath = "examples/research_search_space.json",
  [string]$DashboardUrl = "http://localhost:4173",
  [switch]$NoBrowser,
  [switch]$SkipDashboardServer,
  [switch]$AllowRuleBasedFallback
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Resolve-RepoPath {
  param([string]$Path)

  if ([string]::IsNullOrWhiteSpace($Path)) {
    return $Path
  }

  if ([System.IO.Path]::IsPathRooted($Path)) {
    return [System.IO.Path]::GetFullPath($Path)
  }

  return [System.IO.Path]::GetFullPath((Join-Path $repoRoot $Path))
}

$dashboardDir = Resolve-RepoPath "dashboard"
$benchmarkFullPath = Resolve-RepoPath $BenchmarkPath
$dashboardOutputFullPath = Resolve-RepoPath $DashboardOutput
$eventsFullPath = Resolve-RepoPath $EventsPath
$summaryFullPath = Resolve-RepoPath $SummaryOutput
$searchSpaceFullPath = Resolve-RepoPath $SearchSpacePath

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dashboardOutputFullPath) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $eventsFullPath) | Out-Null
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $summaryFullPath) | Out-Null

if (-not $SkipDashboardServer) {
  Write-Host "==> Starting dashboard dev server in a new PowerShell window"
  Start-Process powershell -WorkingDirectory $dashboardDir -ArgumentList @(
    "-NoExit",
    "-Command",
    "npm run dev"
  )

  Start-Sleep -Seconds 2
}

if (-not $NoBrowser) {
  Write-Host "==> Opening dashboard in the default browser"
  Start-Process $DashboardUrl
}

Write-Host "==> Running research with live dashboard replay output"
$researchArgs = @(
  "run",
  "autosolver",
  "research",
  $benchmarkFullPath,
  "--rounds",
  $Rounds,
  "--events",
  $eventsFullPath,
  "--output",
  $summaryFullPath,
  "--search-space",
  $searchSpaceFullPath,
  "--dashboard-output",
  $dashboardOutputFullPath
)

if ($AllowRuleBasedFallback) {
  $researchArgs += "--allow-rule-based-fallback"
}

Push-Location $repoRoot
try {
  & uv @researchArgs
}
finally {
  Pop-Location
}

if ($LASTEXITCODE -ne 0) {
  throw "autosolver research failed with exit code $LASTEXITCODE"
}

Write-Host "==> Live run finished"
Write-Host "Dashboard: $DashboardUrl"
Write-Host "Summary: $summaryFullPath"
