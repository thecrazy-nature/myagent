param(
    [string]$MatlabExecutable = 'matlab',
    [string]$ProjectRoot = 'F:\hermes-em-agent'
)

$ErrorActionPreference = 'Stop'
$interface = (Join-Path $ProjectRoot 'agent_interface').Replace('\', '/')
foreach ($taskId in @('task_001', 'task_002', 'task_003')) {
    $taskRoot = Join-Path (Join-Path $ProjectRoot 'runs') $taskId
    $config = (Join-Path $taskRoot 'config.json').Replace('\', '/')
    $result = (Join-Path $taskRoot 'result.json').Replace('\', '/')
    $batch = "addpath('$interface','-begin'); agent_run_simulation('$config','$result');"
    & $MatlabExecutable -batch $batch
    if ($LASTEXITCODE -ne 0) {
        throw "MATLAB batch process failed for $taskId with exit code $LASTEXITCODE."
    }
    $payload = Get-Content -Raw -Encoding UTF8 -LiteralPath $result | ConvertFrom-Json
    if ($payload.status -ne 'success' -or $payload.task_id -ne $taskId) {
        throw "Unexpected result for ${taskId}: $($payload | ConvertTo-Json -Compress)"
    }
    Write-Output "$taskId PASS -> $result"
}
