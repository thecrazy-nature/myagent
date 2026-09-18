[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'HermesEMAgent'),
    [string]$MatlabExecutable = '',
    [string]$HermesPython = '',
    [switch]$InstallHermes,
    [switch]$NoDesktopShortcut
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-PayloadRoot {
    $direct = $PSScriptRoot
    if ((Test-Path (Join-Path $direct 'app')) -and (Test-Path (Join-Path $direct 'run-app.ps1'))) {
        return [IO.Path]::GetFullPath($direct)
    }
    $repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    if ((Test-Path (Join-Path $repository 'app')) -and (Test-Path (Join-Path $repository 'run-app.ps1'))) {
        return $repository
    }
    throw 'The application payload was not found beside the installer or in the repository root.'
}

function Get-HermesPython {
    param([string]$ConfiguredPath)
    $candidates = @()
    if ($ConfiguredPath) { $candidates += $ConfiguredPath }
    if ($env:HERMES_PYTHON) { $candidates += $env:HERMES_PYTHON }
    $candidates += (Join-Path $env:LOCALAPPDATA 'hermes\hermes-agent\venv\Scripts\python.exe')
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    return $null
}

function Install-HermesRuntime {
    $uri = 'https://hermes-agent.nousresearch.com/install.ps1'
    $temporaryInstaller = Join-Path ([IO.Path]::GetTempPath()) ("hermes-install-{0}.ps1" -f [guid]::NewGuid().ToString('N'))
    try {
        Write-Host "Downloading the official Hermes installer from $uri"
        Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $temporaryInstaller
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $temporaryInstaller
        if ($LASTEXITCODE -ne 0) {
            throw "Hermes installer exited with code $LASTEXITCODE."
        }
    }
    finally {
        Remove-Item -LiteralPath $temporaryInstaller -Force -ErrorAction SilentlyContinue
    }
}

function Get-UvExecutable {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'hermes\bin\uv.exe'),
        (Join-Path $env:USERPROFILE '.local\bin\uv.exe')
    )
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    throw 'uv was not found. Re-run with -InstallHermes or install Hermes first.'
}

function Resolve-MatlabExecutable {
    param([string]$ConfiguredPath)
    $candidates = @()
    if ($ConfiguredPath) { $candidates += $ConfiguredPath }
    if ($env:MATLAB_EXECUTABLE) { $candidates += $env:MATLAB_EXECUTABLE }
    $command = Get-Command matlab -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    return ''
}

function New-ApplicationShortcut {
    param(
        [string]$ShortcutPath,
        [string]$ApplicationRoot
    )
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
    $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}\run-app.ps1"' -f $ApplicationRoot
    $shortcut.WorkingDirectory = $ApplicationRoot
    $shortcut.Description = 'Hermes Electromagnetic Research Agent'
    $shortcut.Save()
}

$payloadRoot = Get-PayloadRoot
$resolvedInstallRoot = [IO.Path]::GetFullPath($InstallRoot)
if ($resolvedInstallRoot -eq [IO.Path]::GetPathRoot($resolvedInstallRoot)) {
    throw 'InstallRoot cannot be a drive root.'
}
if ($resolvedInstallRoot -eq $payloadRoot) {
    throw 'InstallRoot must differ from the source payload directory.'
}

$resolvedHermesPython = Get-HermesPython -ConfiguredPath $HermesPython
if (-not $resolvedHermesPython -and $InstallHermes) {
    Install-HermesRuntime
    $resolvedHermesPython = Get-HermesPython -ConfiguredPath $HermesPython
}
if (-not $resolvedHermesPython) {
    throw 'Hermes Python was not found. Re-run with -InstallHermes or pass -HermesPython.'
}

$payloadItems = @(
    '.hermes', '.hermes.md', 'agent_interface', 'app', 'array_design', 'bridge',
    'em_focus_agent', 'governance', 'matlab_core', 'metasurface_design',
    'README.md', 'requirements-ui.txt', 'run-app.ps1', 'proxy-on.ps1', 'proxy-off.ps1'
)
foreach ($item in $payloadItems) {
    if (-not (Test-Path -LiteralPath (Join-Path $payloadRoot $item))) {
        throw "Required payload item is missing: $item"
    }
}

New-Item -ItemType Directory -Path $resolvedInstallRoot -Force | Out-Null
foreach ($item in $payloadItems) {
    Copy-Item -LiteralPath (Join-Path $payloadRoot $item) -Destination $resolvedInstallRoot -Recurse -Force
}

$installerFolder = if (Test-Path (Join-Path $payloadRoot 'uninstall.ps1')) {
    $payloadRoot
}
else {
    Join-Path $payloadRoot 'packaging\windows'
}
Copy-Item -LiteralPath (Join-Path $installerFolder 'uninstall.ps1') -Destination $resolvedInstallRoot -Force

$resolvedMatlab = Resolve-MatlabExecutable -ConfiguredPath $MatlabExecutable
$settings = [ordered]@{
    schema_version = 1
    matlab_executable = $resolvedMatlab
    hermes_python = $resolvedHermesPython
}
$settings | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $resolvedInstallRoot 'app-settings.local.json') -Encoding UTF8

$marker = [ordered]@{
    product = 'HermesEMAgent'
    installed_at = [DateTimeOffset]::Now.ToString('o')
    source = 'public-release-payload'
}
$marker | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $resolvedInstallRoot '.hermes-em-agent-install.json') -Encoding UTF8

$uv = Get-UvExecutable
Write-Host 'Installing UI dependencies into the Hermes Python environment...'
& $uv pip install --python $resolvedHermesPython -r (Join-Path $resolvedInstallRoot 'requirements-ui.txt')
if ($LASTEXITCODE -ne 0) {
    throw "UI dependency installation failed with code $LASTEXITCODE."
}

$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
New-ApplicationShortcut -ShortcutPath (Join-Path $startMenu 'Hermes EM Agent.lnk') -ApplicationRoot $resolvedInstallRoot
if (-not $NoDesktopShortcut) {
    New-ApplicationShortcut -ShortcutPath (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Hermes EM Agent.lnk') -ApplicationRoot $resolvedInstallRoot
}

Write-Host "Installed Hermes EM Agent to: $resolvedInstallRoot"
if ($resolvedMatlab) {
    Write-Host "MATLAB: $resolvedMatlab"
}
else {
    Write-Warning 'MATLAB was not found. Install MATLAB or edit app-settings.local.json before running simulations.'
}
Write-Host 'If Hermes has not been authenticated yet, run hermes once and complete its model setup.'
Write-Host 'Launch the application from the shortcut or run run-app.ps1 in the installation folder.'
