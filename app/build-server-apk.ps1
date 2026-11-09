# Build the shareable real-mode app for a deployed engine (Render, or any https host).
#   powershell -ExecutionPolicy Bypass -File app\build-server-apk.ps1 -Url https://nirnay-engine.onrender.com
# The APK installs on any Android 7+ phone (arm64, arm32 or x86_64) and works on mobile data or any
# Wi-Fi, because it calls the server over https. The engine's health check must show real mode
# first, so an APK is never built against a sandbox or a wrong address.
# Output: app\dist\nirnay-<server host>.apk, signed with the release key (see backend\DEPLOY.md).
param([Parameter(Mandatory = $true)][string]$Url)
$ErrorActionPreference = "Stop"
$Url = $Url.Trim().TrimEnd('/')
if ($Url -notmatch '^https://[A-Za-z0-9.-]+(:\d+)?$') {
    throw "Use the server's https:// address with nothing after it, for example https://nirnay-engine.onrender.com"
}
Write-Host "Checking $Url/v1/health ... (a sleeping server can take a minute to wake)"
# The header skips the warning page a Microsoft dev tunnel shows to browsers (the app isn't one).
$health = Invoke-RestMethod "$Url/v1/health" -TimeoutSec 120 -Headers @{ "X-Tunnel-Skip-AntiPhishing-Page" = "true" }
if ($health.mode -ne "real") { throw "The engine at $Url is in '$($health.mode)' mode, not real mode." }
Write-Host "Engine:   $($health.engine), database $($health.database), SMS $($health.sms)"
if ($health.database -ne "postgresql") { Write-Warning "The database isn't PostgreSQL: applications are lost when the server restarts." }
Push-Location $PSScriptRoot
try {
    flutter build apk --release "--dart-define=NIRNAY_API_URL=$Url" "--dart-define=NIRNAY_REAL=true"
    if ($LASTEXITCODE -ne 0) { throw "flutter build failed" }
    New-Item -ItemType Directory -Force (Join-Path $PSScriptRoot "dist") | Out-Null
    $out = Join-Path $PSScriptRoot ("dist\nirnay-" + ([Uri]$Url).Host + ".apk")
    Copy-Item (Join-Path $PSScriptRoot "build\app\outputs\flutter-apk\app-release.apk") $out -Force
    Write-Host "APK:      $out"
} finally {
    Pop-Location
}
