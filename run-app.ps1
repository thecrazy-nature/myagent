$ErrorActionPreference = 'Stop'

$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$expectedProxy = 'http://127.0.0.1:7897'
$expectedNoProxy = 'localhost,127.0.0.1,::1'
if (
    $env:HTTP_PROXY -ne $expectedProxy -or
    $env:HTTPS_PROXY -ne $expectedProxy -or
    $env:NO_PROXY -ne $expectedNoProxy
) {
    throw "Hermes network proxy is unavailable. Start Clash and run .\proxy-on.ps1 first."
}

$tcpClient = [System.Net.Sockets.TcpClient]::new()
try {
    $connection = $tcpClient.ConnectAsync('127.0.0.1', 7897)
    if (-not $connection.Wait(2000) -or -not $tcpClient.Connected) {
        throw 'Clash proxy 127.0.0.1:7897 is not accepting connections.'
    }
}
catch {
    throw 'Hermes network proxy is unavailable. Start Clash and run .\proxy-on.ps1 first.'
}
finally {
    $tcpClient.Dispose()
}

$hermesPython = if ($env:HERMES_PYTHON) {
    $env:HERMES_PYTHON
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
