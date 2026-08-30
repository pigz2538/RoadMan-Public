param(
    [switch]$Live,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$dockerRoot = $repoRoot.Replace("\", "/")

Set-Location $repoRoot
if (-not $SkipBuild) {
    docker compose build backend worker frontend
}

docker run --rm `
    -e PYTHONPATH=/app:/evaluation `
    -e ROADMAN_REPO_ROOT=/workspace `
    -v "${dockerRoot}/backend/app:/app/app:ro" `
    -v "${dockerRoot}/backend/tests:/app/tests:ro" `
    -v "${dockerRoot}/evaluation:/evaluation" `
    -v "${dockerRoot}:/workspace:ro" `
    roadman-backend `
    sh -c "pytest /app/tests /evaluation/test_range_accuracy.py /evaluation/test_safety_scenarios.py -q && python /evaluation/semifinal_readiness.py"

Push-Location frontend
try {
    npm run test
    npm run build
} finally {
    Pop-Location
}

if ($Live) {
    docker compose up -d --wait
    python deploy/api_smoke.py
    docker run --rm `
        -e PYTHONPATH=/app:/evaluation `
        -e ROADMAN_REPO_ROOT=/workspace `
        -v "${dockerRoot}/backend/app:/app/app:ro" `
        -v "${dockerRoot}/evaluation:/evaluation" `
        -v "${dockerRoot}:/workspace:ro" `
        roadman-backend `
        python /evaluation/semifinal_readiness.py --base-url http://host.docker.internal:8000
}

Write-Host "RoadMan semifinal checks completed."
