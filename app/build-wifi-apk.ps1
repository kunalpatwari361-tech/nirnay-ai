# Build the real-mode app for phones on the same Wi-Fi as this laptop.
#   powershell -ExecutionPolicy Bypass -File app\build-wifi-apk.ps1 [-Ip 192.168.29.103] [-Port 8002]
# The app will call http://<Ip>:<Port> (8000 unless -Port says otherwise). Start the server with:
#   powershell -ExecutionPolicy Bypass -File backend\run-local-real.ps1 -Lan [-Port 8002]
# Android blocks plain http:// except to addresses the app lists, so for this build only the
# laptop's Wi-Fi address is added to the network config; the file is restored afterwards.
# Output: app\dist\nirnay-real-wifi-<Ip>.apk (or -<Ip>-<Port>.apk for a port other than 8000). If the laptop's Wi-Fi address changes, build again
# (or reserve the address for this laptop in your router).
param([string]$Ip = "", [ValidateRange(1, 65535)][int]$Port = 8000)
$ErrorActionPreference = "Stop"
$app = $PSScriptRoot
if (-not $Ip) {
    $Ip = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "Wi-Fi*" | Where-Object { $_.IPAddress -notmatch '^169\.254' } |
           Select-Object -First 1).IPAddress
}
if ($Ip -notmatch '^(10|172\.(1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}$' -and $Ip -notmatch '^192\.168\.\d{1,3}\.\d{1,3}$') {
    throw "'$Ip' is not a private Wi-Fi address (10.x, 172.16-31.x or 192.168.x)."
}
$config = Join-Path $app "android\app\src\main\res\xml\network_security_config.xml"
$original = [System.IO.File]::ReadAllText($config)
$withIp = $original -replace '(<domain includeSubdomains="false">10\.0\.2\.2</domain>)',
    "`$1`r`n        <domain includeSubdomains=`"false`">$Ip</domain>"
try {
    [System.IO.File]::WriteAllText($config, $withIp, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "Building for http://${Ip}:$Port ..."
    Push-Location $app
    flutter build apk --release "--dart-define=NIRNAY_API_URL=http://${Ip}:$Port" "--dart-define=NIRNAY_REAL=true"
    if ($LASTEXITCODE -ne 0) { throw "flutter build failed" }
    New-Item -ItemType Directory -Force (Join-Path $app "dist") | Out-Null
    $name = if ($Port -eq 8000) { "nirnay-real-wifi-$Ip.apk" } else { "nirnay-real-wifi-$Ip-$Port.apk" }
    $out = Join-Path $app "dist\$name"
    Copy-Item (Join-Path $app "build\app\outputs\flutter-apk\app-release.apk") $out -Force
    Write-Host "APK: $out"
} finally {
    Pop-Location -ErrorAction SilentlyContinue
    [System.IO.File]::WriteAllText($config, $original, (New-Object System.Text.UTF8Encoding $false))
}
