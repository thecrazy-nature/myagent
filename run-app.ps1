$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$settingsPath = Join-Path $projectRoot 'app-settings.local.json'
$localSettings = $null
if (Test-Path -LiteralPath $settingsPath -PathType Leaf) {
    try {
        $localSettings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        throw "Invalid local application settings: $settingsPath. $($_.Exception.Message)"
    }
}

if (-not $env:MATLAB_EXECUTABLE -and $localSettings.matlab_executable) {
    $env:MATLAB_EXECUTABLE = [string]$localSettings.matlab_executable
}

$hermesPython = if ($env:HERMES_PYTHON) {
    $env:HERMES_PYTHON
}
elseif ($localSettings.hermes_python) {
    [string]$localSettings.hermes_python
}
else {
    Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent\venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $hermesPython -PathType Leaf)) {
    throw "Hermes Python runtime was not found: $hermesPython"
}

& $hermesPython -c "import hermes_cli, streamlit, psutil, matplotlib"
if ($LASTEXITCODE -ne 0) {
    throw "Hermes or Streamlit is unavailable. Install UI dependencies with: uv pip install --python `"$hermesPython`" -r requirements-ui.txt"
}

Write-Host 'Starting durable Hermes/MATLAB background worker...'
$worker = Start-Process -FilePath $hermesPython `
    -ArgumentList @('-m', 'app.job_worker') `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -PassThru
Write-Host "Background worker launch PID: $($worker.Id)"
Write-Host 'Starting Electromagnetic Focusing Research Agent...'
Write-Host 'Open http://localhost:8501'
try {
    & $hermesPython -m streamlit run app\streamlit_app.py `
        --server.address localhost `
        --server.port 8501 `
        --server.headless true `
        --browser.gatherUsageStats false
}
finally {
    $unfinishedJobs = @(
        Get-ChildItem -LiteralPath (Join-Path $projectRoot 'runs\ui_jobs') `
            -Filter 'job_*.json' -File -ErrorAction SilentlyContinue |
        ForEach-Object {
            try {
                $job = Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
                if ($job.status -notin @('completed', 'failed', 'cancelled')) { $job }
            }
            catch { }
        }
    )
    if (-not $worker.HasExited -and $unfinishedJobs.Count -eq 0) {
        Stop-Process -Id $worker.Id
        Write-Host 'Stopped the idle background worker.'
    }
    elseif (-not $worker.HasExited) {
        Write-Host "The background worker remains active for unfinished jobs (PID $($worker.Id))."
    }
}
