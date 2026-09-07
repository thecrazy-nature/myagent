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

& $hermesPython -c "import hermes_cli, streamlit"
if ($LASTEXITCODE -ne 0) {
    throw "Hermes or Streamlit is unavailable. Install UI dependencies with: uv pip install --python `"$hermesPython`" -r requirements-ui.txt"
}

Write-Host 'Starting Electromagnetic Focusing Research Agent...'
Write-Host 'Open http://localhost:8501'
& $hermesPython -m streamlit run app\streamlit_app.py `
    --server.address localhost `
    --server.port 8501 `
    --server.headless true `
    --browser.gatherUsageStats false
