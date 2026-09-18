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
    $runtimeDirectories = @(
        '.hermes', 'agent_interface', 'app', 'array_design', 'bridge',
        'em_focus_agent', 'governance', 'matlab_core', 'metasurface_design'
    )
    $runtimeRootFiles = @(
        '.hermes.md', 'README.md', 'requirements-ui.txt', 'run-app.ps1',
        'proxy-on.ps1', 'proxy-off.ps1'
    )
    $trackedFiles = @(git -C $repositoryRoot ls-files)
    if ($LASTEXITCODE -ne 0) { throw 'Could not read the Git release manifest.' }
    $releaseFiles = @($trackedFiles | Where-Object {
        $path = $_.Replace('\', '/')
        ($runtimeRootFiles -contains $path) -or
        ($runtimeDirectories | Where-Object { $path.StartsWith($_ + '/', [StringComparison]::OrdinalIgnoreCase) })
    })
    if ($releaseFiles.Count -lt 25) {
        throw "The Git release manifest contains too few runtime files: $($releaseFiles.Count)"
    }
    foreach ($relativePath in $releaseFiles) {
        $source = Join-Path $repositoryRoot $relativePath
        $destination = Join-Path $stagingRoot $relativePath
        $destinationFolder = Split-Path -Parent $destination
        New-Item -ItemType Directory -Path $destinationFolder -Force | Out-Null
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install.ps1') -Destination $stagingRoot
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'uninstall.ps1') -Destination $stagingRoot
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'setup.cmd') -Destination $stagingRoot

    Get-ChildItem -LiteralPath $stagingRoot -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
    Get-ChildItem -LiteralPath $stagingRoot -Recurse -File |
        Where-Object { $_.Extension -in @('.pyc', '.pyo') } |
        Remove-Item -Force

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
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $fileEntries = @($archive.Entries | Where-Object { $_.Name })
        $requiredSuffixes = @(
            '/setup.cmd', '/install.ps1', '/run-app.ps1',
            '/app/streamlit_app.py', '/matlab_core/run_focus_core.m'
        )
        foreach ($suffix in $requiredSuffixes) {
            $normalizedSuffix = $suffix.Replace('/', '\')
            if (-not ($fileEntries.FullName | Where-Object { $_.EndsWith($normalizedSuffix, [StringComparison]::OrdinalIgnoreCase) })) {
                throw "Release archive is missing required file: $suffix"
            }
        }
        if ($fileEntries.Count -lt 25) {
            throw "Release archive contains too few files: $($fileEntries.Count)"
        }
    }
    finally {
        $archive.Dispose()
    }
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
