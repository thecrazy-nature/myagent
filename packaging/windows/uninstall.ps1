[CmdletBinding()]
param(
    [string]$InstallRoot = $PSScriptRoot,
    [switch]$RemoveUserData
)

$ErrorActionPreference = 'Stop'
$resolvedRoot = [IO.Path]::GetFullPath($InstallRoot)
if ($resolvedRoot -eq [IO.Path]::GetPathRoot($resolvedRoot)) {
    throw 'Refusing to uninstall from a drive root.'
}
$marker = Join-Path $resolvedRoot '.hermes-em-agent-install.json'
if (-not (Test-Path -LiteralPath $marker -PathType Leaf)) {
    throw "Installation marker is missing; refusing to remove files from $resolvedRoot"
}

$shortcutPaths = @(
    (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Hermes EM Agent.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Hermes EM Agent.lnk')
)
foreach ($shortcut in $shortcutPaths) {
    Remove-Item -LiteralPath $shortcut -Force -ErrorAction SilentlyContinue
}

$installedItems = @(
    '.hermes', '.hermes.md', 'agent_interface', 'app', 'array_design', 'bridge',
    'em_focus_agent', 'governance', 'matlab_core', 'metasurface_design',
    'README.md', 'requirements-ui.txt', 'run-app.ps1', 'proxy-on.ps1', 'proxy-off.ps1'
)
foreach ($item in $installedItems) {
    $target = [IO.Path]::GetFullPath((Join-Path $resolvedRoot $item))
    if (-not $target.StartsWith($resolvedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe uninstall target: $target"
    }
    Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
}

Remove-Item -LiteralPath $marker -Force
if ($RemoveUserData) {
    foreach ($item in @('runs', 'app-settings.local.json')) {
        $target = [IO.Path]::GetFullPath((Join-Path $resolvedRoot $item))
        if (-not $target.StartsWith($resolvedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Unsafe user-data target: $target"
        }
        Remove-Item -LiteralPath $target -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host 'Application shortcuts and program files were removed.'
if (-not $RemoveUserData) {
    Write-Host "Local runs and settings were preserved in: $resolvedRoot"
}
Write-Host 'Hermes, MATLAB and CST were not removed because they are shared external applications.'

if ($RemoveUserData) {
    $self = [IO.Path]::GetFullPath($PSCommandPath)
    if ($self.StartsWith($resolvedRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $self -Force -ErrorAction SilentlyContinue
    }
    $remaining = @(Get-ChildItem -LiteralPath $resolvedRoot -Force -ErrorAction SilentlyContinue)
    if ($remaining.Count -eq 0) {
        Remove-Item -LiteralPath $resolvedRoot -Force -ErrorAction SilentlyContinue
    }
}
