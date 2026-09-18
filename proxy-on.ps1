param(
    [string]$ProxyUrl = $(
        if ($env:HERMES_EM_PROXY) { $env:HERMES_EM_PROXY }
        else { 'http://127.0.0.1:7897' }
    )
)

if ($ProxyUrl -notmatch '^https?://[^\s]+$') {
    throw 'ProxyUrl must be an HTTP or HTTPS URL, for example http://127.0.0.1:7897.'
}

$env:HTTP_PROXY = $ProxyUrl
$env:HTTPS_PROXY = $ProxyUrl
$env:NO_PROXY = 'localhost,127.0.0.1,::1'

Write-Host "Project proxy enabled for this PowerShell session ($ProxyUrl)."
