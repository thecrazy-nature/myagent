[CmdletBinding()]
param(
    [string]$Version = '0.5.0',
    [string]$OutputDirectory = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repositoryRoot 'dist'
}
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null

$stagingParent = Join-Path ([IO.Path]::GetTempPath()) ("hermes-em-agent-release-{0}" -f [guid]::NewGuid().ToString('N'))
$packageName = "HermesEMAgent-$Version-windows"
$stagingRoot = Join-Path $stagingParent $packageName
New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null

try {
    $payloadItems = @(
        '.hermes', '.hermes.md', 'agent_interface', 'app', 'array_design', 'bridge',
        'em_focus_agent', 'governance', 'matlab_core', 'metasurface_design',
        'README.md', 'requirements-ui.txt', 'run-app.ps1', 'proxy-on.ps1', 'proxy-off.ps1'
    )
    foreach ($item in $payloadItems) {
        $source = Join-Path $repositoryRoot $item
        if (-not (Test-Path -LiteralPath $source)) {
            throw "Required release item is missing: $item"
        }
        Copy-Item -LiteralPath $source -Destination $stagingRoot -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install.ps1') -Destination $stagingRoot
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'uninstall.ps1') -Destination $stagingRoot
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'setup.cmd') -Destination $stagingRoot

    Get-ChildItem -LiteralPath $stagingRoot -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $stagingRoot -Recurse -File -Include '*.pyc', '*.pyo' | Remove-Item -Force

    $forbiddenPatterns = @(
        'sk-[A-Za-z0-9_-]{16,}',
        '(?i)Bearer\s+[A-Za-z0-9._-]{16,}',
        '(?i)(api[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*["''][^"'']{12,}["'']',
        '(?i)C:\\Users\\[^\\]+',
        '(?i)[A-Z]:\\(?:hermes-em-agent|matlabcode)'
    )
    $textExtensions = @('.md', '.txt', '.json', '.yaml', '.yml', '.py', '.ps1', '.cmd', '.m')
    $violations = @()
    foreach ($file in Get-ChildItem -LiteralPath $stagingRoot -Recurse -File) {
        if ($textExtensions -notcontains $file.Extension.ToLowerInvariant()) { continue }
        $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
        foreach ($pattern in $forbiddenPatterns) {
            if ($content -match $pattern) {
                $violations += "$($file.FullName): pattern $pattern"
            }
        }
    }
    if ($violations.Count -gt 0) {
        throw "Release privacy check failed:`n$($violations -join "`n")"
    }

    $archivePath = Join-Path $resolvedOutput "$packageName.zip"
    Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
    Compress-Archive -LiteralPath $stagingRoot -DestinationPath $archivePath -CompressionLevel Optimal
    $hash = Get-FileHash -LiteralPath $archivePath -Algorithm SHA256
    "$($hash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($archivePath))" |
        Set-Content -LiteralPath "$archivePath.sha256" -Encoding ASCII
    Write-Host "Release archive: $archivePath"
    Write-Host "SHA-256: $($hash.Hash.ToLowerInvariant())"
}
finally {
    if (Test-Path -LiteralPath $stagingParent) {
        Remove-Item -LiteralPath $stagingParent -Recurse -Force
    }
}
