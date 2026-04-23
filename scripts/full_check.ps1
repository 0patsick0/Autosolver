param(
  [string]$OutputDir = "examples/fullcheck_run",
  [int]$Rounds = 2,
  [int]$Seed = 21
)

$ErrorActionPreference = "Stop"

Write-Host "==> Running Python tests"
uv run pytest -q

Write-Host "==> Running end-to-end smoke flow"
uv run autosolver smoke $OutputDir --rounds $Rounds --seed $Seed

$instancePath = Join-Path $OutputDir "benchmark\smoke-benchmark-001.json"
$researchSummaryPath = Join-Path $OutputDir "research_summary.json"
$solveFromResearchPath = Join-Path $OutputDir "solve_result_from_research_via_cli.json"
$validationFromResearchPath = Join-Path $OutputDir "validation_from_research_via_cli.json"

Write-Host "==> Verifying research-to-solve deployment"
uv run autosolver solve $instancePath --config-source $researchSummaryPath --output $solveFromResearchPath
uv run autosolver validate $instancePath $solveFromResearchPath --output $validationFromResearchPath

Write-Host "==> Building dashboard"
Push-Location "dashboard"
try {
  npm run build
} finally {
  Pop-Location
}

Write-Host "==> Full check complete"
Write-Host "Artifacts:"
Write-Host "  Smoke summary: $OutputDir\smoke_summary.json"
Write-Host "  Research summary: $OutputDir\research_summary.json"
Write-Host "  Dashboard replay: $OutputDir\replay-data.json"
