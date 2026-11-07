# Real mode on this laptop, for testing with your own phone over USB.
#   powershell -ExecutionPolicy Bypass -File backend\run-local-real.ps1
# - Real applicants: no demo people or sample documents.
# - Sign-in codes are random and printed HERE, in this terminal, instead of being sent by SMS
#   (console mode). Only ever run this on your own laptop.
# - Every step is saved as it happens in backend\var\real\nirnay.db (git-ignored), or in
#   Supabase if you set DATABASE_URL before running this.
# - The phone reaches the server through the USB cable (adb reverse), so keep it plugged in.
# - With -Lan, phones on the same Wi-Fi can reach it too (app built by app\build-wifi-apk.ps1).
#   Windows asks once whether Python may accept connections: allow Private networks only.
#   Anyone on your Wi-Fi can then open the sign-in screen; the codes still appear only here.
# - With -Twilio, sign-in codes go by real SMS through Twilio Verify instead. The script asks for
#   the Account SID and the Auth Token. Leave the Verify Service SID blank and it makes (or reuses)
#   a Verify service named "Nirnay" in your Twilio account. It checks the token with Twilio before
#   starting, and remembers the two SIDs (not secret) in backend\var\real\twilio-ids.txt. Once
#   Twilio accepts the token, it is remembered too, encrypted with Windows (DPAPI) so that only
#   your Windows account on this PC can read it, in backend\var\real\twilio-token.dpapi (git-ignored),
#   and nothing is asked next time. If Twilio later refuses it (a new token), the saved one is
#   forgotten and the script asks again. -ForgetTwilioToken deletes it. Never put the token in a
#   plain file, the repo or a chat. On a Twilio trial account, only numbers you have verified in
#   the Twilio console can receive the texts.
# - With -Bureau, the credit report is pulled with the applicant's PAN through Decentro (the app
#   never asks for a credit score). The script asks for the Decentro client_id and client_secret;
#   the secret is never saved. It uses Decentro's staging server unless DECENTRO_BASE_URL is set
#   to https://in.decentro.tech; staging reports are test data, so every decision is referred.
#   Without -Bureau no report is pulled and decisions wait for an officer to pull one.
# - With -DigiLocker, the app offers "Get my details from DigiLocker" (name, date of birth, PAN,
#   Aadhaar). It needs the lender registered as a DigiLocker requester: the script asks for the
#   client ID, the client secret (never saved) and the redirect URI registered with DigiLocker,
#   which must reach this server (for example through an HTTPS tunnel to port 8000).
# - With -Cashfree, DigiLocker goes through Cashfree Secure ID (a DigiLocker partner) instead: the
#   app offers DigiLocker on the details and documents screens, and the PAN and e-Aadhaar come from
#   the applicant's own DigiLocker. The script asks for the Cashfree client ID, the client secret
#   (never saved) and the public key file for Cashfree's 2FA (Secure ID dashboard, Developers >
#   Two-Factor Authentication; leave it blank if this PC's IP is whitelisted there). It remembers
#   the client ID and the key file's path (not secret) in backend\var\real\cashfree-ids.txt. It uses
#   Cashfree's sandbox unless CASHFREE_VERIFICATION_URL is set to https://api.cashfree.com/verification.
# - With -Port 8002, the server listens on another port (when 8000 is busy, for example with the
#   demo engine). The phone app still calls 8000, which adb reverse forwards to this port.
param([switch]$Lan, [switch]$Twilio, [switch]$Bureau, [switch]$DigiLocker, [switch]$Cashfree, [int]$Port = 8000,
      [switch]$ForgetTwilioToken)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

function Stop-With([string]$why) {
    Write-Host ""
    Write-Host "Stopped: $why" -ForegroundColor Red
    Write-Host "Run the same command again to retry."
    exit 1
}

function Read-Secret([string]$prompt) {
    $s = Read-Host $prompt -AsSecureString
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b).Trim() }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

$env:NIRNAY_SANDBOX = "0"
if (-not $env:NIRNAY_VAR_DIR) { $env:NIRNAY_VAR_DIR = Join-Path $root "backend\var\real" }
New-Item -ItemType Directory -Force $env:NIRNAY_VAR_DIR | Out-Null
# The Twilio Auth Token, encrypted with DPAPI for this Windows account on this PC (never plain text).
$tokenFile = Join-Path $env:NIRNAY_VAR_DIR "twilio-token.dpapi"
if ($ForgetTwilioToken -and (Test-Path $tokenFile)) {
    Remove-Item $tokenFile -Force
    Write-Host "Twilio:    the saved Auth Token is deleted."
}
if ($Twilio) {
    $idsFile = Join-Path $env:NIRNAY_VAR_DIR "twilio-ids.txt"
    $saved = @{}
    if (Test-Path $idsFile) {
        foreach ($line in Get-Content $idsFile) {
            $kv = $line -split '=', 2
            if ($kv.Count -eq 2 -and $kv[1].Trim()) { $saved[$kv[0].Trim()] = $kv[1].Trim() }
        }
    }
    if (-not $env:TWILIO_ACCOUNT_SID -and $saved["TWILIO_ACCOUNT_SID"]) { $env:TWILIO_ACCOUNT_SID = $saved["TWILIO_ACCOUNT_SID"] }
    if (-not $env:TWILIO_VERIFY_SERVICE_SID -and $saved["TWILIO_VERIFY_SERVICE_SID"]) { $env:TWILIO_VERIFY_SERVICE_SID = $saved["TWILIO_VERIFY_SERVICE_SID"] }

    if ($env:TWILIO_ACCOUNT_SID) { Write-Host "Twilio Account SID: $env:TWILIO_ACCOUNT_SID" }
    else { $env:TWILIO_ACCOUNT_SID = (Read-Host "Twilio Account SID (starts with AC)").Trim() }
    if ($env:TWILIO_ACCOUNT_SID -notmatch '^AC[0-9a-fA-F]{32}$') {
        Remove-Item Env:TWILIO_ACCOUNT_SID
        Stop-With "the Account SID should be AC followed by 32 letters and digits (Twilio console home page)."
    }
    $secret = $null; $fromSaved = $false
    if (-not $env:TWILIO_AUTH_TOKEN -and (Test-Path $tokenFile)) {
        try {
            $secret = (Get-Content $tokenFile -Raw).Trim() | ConvertTo-SecureString
            $fromSaved = $true
        } catch {
            # Saved by another Windows account or PC, or damaged: forget it and ask instead.
            Remove-Item $tokenFile -Force -ErrorAction SilentlyContinue
            Write-Host "Twilio:    the saved Auth Token couldn't be read here, so it's forgotten. Type it once more."
        }
    }
    if (-not $env:TWILIO_AUTH_TOKEN -and -not $secret) {
        $secret = Read-Host "Twilio Auth Token (typing is hidden)" -AsSecureString
    }
    if ($secret) {
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
        try { $env:TWILIO_AUTH_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim() }
        finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    }
    if ($fromSaved) { Write-Host "Twilio:    using the saved Auth Token (-ForgetTwilioToken deletes it)." }
    if ($env:TWILIO_AUTH_TOKEN.Length -lt 32) {
        if ($fromSaved) { Remove-Item $tokenFile -Force -ErrorAction SilentlyContinue }
        Stop-With "the Auth Token looks too short (it is 32 letters and digits)."
    }
    function Stop-Refused([string]$why) {
        if ($fromSaved) {
            Remove-Item $tokenFile -Force -ErrorAction SilentlyContinue
            $why += " The saved Auth Token is forgotten, so the next run asks for the new one."
        }
        Stop-With $why
    }

    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $pair = "$($env:TWILIO_ACCOUNT_SID):$($env:TWILIO_AUTH_TOKEN)"
    $auth = @{ Authorization = "Basic " + [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes($pair)) }
    $verify = "https://verify.twilio.com/v2/Services"

    if (-not $env:TWILIO_VERIFY_SERVICE_SID) {
        $answer = (Read-Host "Twilio Verify Service SID (starts with VA). Leave blank to use one named Nirnay, made now if needed").Trim()
        if ($answer) {
            $env:TWILIO_VERIFY_SERVICE_SID = $answer
        } else {
            try {
                $list = Invoke-RestMethod -Uri "${verify}?PageSize=50" -Headers $auth
                $svc = $list.services | Where-Object { $_.friendly_name -eq "Nirnay" } | Select-Object -First 1
                if ($svc) { Write-Host "Verify service: using your existing 'Nirnay' service." }
                else {
                    $svc = Invoke-RestMethod -Method Post -Uri $verify -Headers $auth -Body @{ FriendlyName = "Nirnay" }
                    Write-Host "Verify service: made a new one named 'Nirnay' in your Twilio account."
                }
                $env:TWILIO_VERIFY_SERVICE_SID = $svc.sid
            } catch {
                Stop-Refused "Twilio did not accept the Account SID and Auth Token ($($_.Exception.Message)). Check both in the Twilio console."
            }
        }
    }
    if ($env:TWILIO_VERIFY_SERVICE_SID -notmatch '^VA[0-9a-fA-F]{32}$') {
        Stop-With "the Verify Service SID should be VA followed by 32 letters and digits. Leave it blank next time and the script makes one."
    }
    # One call to Twilio before starting, so a wrong token shows up now, not at the first sign-in.
    try { $null = Invoke-RestMethod -Uri "$verify/$($env:TWILIO_VERIFY_SERVICE_SID)" -Headers $auth }
    catch { Stop-Refused "Twilio refused the details ($($_.Exception.Message)). Check the Auth Token, and that the Verify service belongs to this account." }
    Set-Content -Path $idsFile -Encoding ASCII -Value @(
        "# Twilio IDs for backend\run-local-real.ps1 -Twilio. Not secret; the Auth Token is kept apart, encrypted (twilio-token.dpapi).",
        "TWILIO_ACCOUNT_SID=$($env:TWILIO_ACCOUNT_SID)",
        "TWILIO_VERIFY_SERVICE_SID=$($env:TWILIO_VERIFY_SERVICE_SID)")
    Write-Host "Twilio:    details accepted. Verify service $($env:TWILIO_VERIFY_SERVICE_SID)"
    if ($secret -and -not $fromSaved) {
        # Only after Twilio accepted it, so a mistyped token is never remembered.
        Set-Content -Path $tokenFile -Encoding ASCII -Value ($secret | ConvertFrom-SecureString)
        Write-Host "Twilio:    Auth Token remembered for next time, encrypted for your Windows account (-ForgetTwilioToken deletes it)."
    }
    $env:NIRNAY_SMS_PROVIDER = "twilio"
    Remove-Item Env:NIRNAY_ALLOW_CONSOLE_OTP -ErrorAction SilentlyContinue
} else {
    $env:NIRNAY_SMS_PROVIDER = "console"
    $env:NIRNAY_ALLOW_CONSOLE_OTP = "1"
}
if ($Bureau) {
    if (-not $env:DECENTRO_CLIENT_ID) { $env:DECENTRO_CLIENT_ID = (Read-Host "Decentro client_id").Trim() }
    if (-not $env:DECENTRO_CLIENT_SECRET) { $env:DECENTRO_CLIENT_SECRET = Read-Secret "Decentro client_secret (typing is hidden)" }
    if (-not $env:DECENTRO_CLIENT_ID -or -not $env:DECENTRO_CLIENT_SECRET) {
        Stop-With "both the Decentro client_id and client_secret are needed (Decentro dashboard, API keys)."
    }
    if (-not $env:DECENTRO_BASE_URL) { $env:DECENTRO_BASE_URL = "https://in.staging.decentro.tech" }
    $env:NIRNAY_BUREAU = "decentro"
    $kind = if ($env:DECENTRO_BASE_URL -match 'staging') { "staging server (test reports: every decision is referred)" } else { "LIVE server" }
    Write-Host "Credit:    reports pulled with the PAN through Decentro, $kind"
} else {
    Write-Host "Credit:    no bureau connected, so reports aren't pulled and decisions wait for an officer (add -Bureau)"
}
if ($DigiLocker) {
    if (-not $env:DIGILOCKER_CLIENT_ID) { $env:DIGILOCKER_CLIENT_ID = (Read-Host "DigiLocker client ID").Trim() }
    if (-not $env:DIGILOCKER_CLIENT_SECRET) { $env:DIGILOCKER_CLIENT_SECRET = Read-Secret "DigiLocker client secret (typing is hidden)" }
    if (-not $env:DIGILOCKER_REDIRECT_URI) {
        $env:DIGILOCKER_REDIRECT_URI = (Read-Host "DigiLocker redirect URI, exactly as registered (ends in /v1/digilocker/callback)").Trim()
    }
    if (-not $env:DIGILOCKER_CLIENT_ID -or -not $env:DIGILOCKER_CLIENT_SECRET) { Stop-With "both the DigiLocker client ID and secret are needed." }
    if ($env:DIGILOCKER_REDIRECT_URI -notmatch '^(https://|http://(localhost|127\.0\.0\.1)(:\d+)?/).*/?v1/digilocker/callback$') {
        Stop-With "the redirect URI should be the https address registered with DigiLocker, ending in /v1/digilocker/callback."
    }
    Write-Host "DigiLocker: connected. $env:DIGILOCKER_REDIRECT_URI must reach this server."
}
if ($Cashfree) {
    $cfFile = Join-Path $env:NIRNAY_VAR_DIR "cashfree-ids.txt"
    $cf = @{}
    if (Test-Path $cfFile) {
        foreach ($line in Get-Content $cfFile) {
            $kv = $line -split '=', 2
            if ($kv.Count -eq 2 -and $kv[1].Trim()) { $cf[$kv[0].Trim()] = $kv[1].Trim() }
        }
    }
    if (-not $env:CASHFREE_CLIENT_ID -and $cf["CASHFREE_CLIENT_ID"]) { $env:CASHFREE_CLIENT_ID = $cf["CASHFREE_CLIENT_ID"] }
    if ($env:CASHFREE_CLIENT_ID) { Write-Host "Cashfree client ID: $env:CASHFREE_CLIENT_ID" }
    else { $env:CASHFREE_CLIENT_ID = (Read-Host "Cashfree client ID (Secure ID dashboard, Developers > API Keys)").Trim() }
    if (-not $env:CASHFREE_CLIENT_SECRET) { $env:CASHFREE_CLIENT_SECRET = Read-Secret "Cashfree client secret (typing is hidden)" }
    if (-not $env:CASHFREE_CLIENT_ID -or -not $env:CASHFREE_CLIENT_SECRET) {
        Stop-With "both the Cashfree client ID and client secret are needed (Secure ID dashboard, Developers > API Keys)."
    }
    if (-not $env:CASHFREE_PUBLIC_KEY_FILE) {
        $known = $cf["CASHFREE_PUBLIC_KEY_FILE"]
        $hint = if ($known) { " Enter keeps $known" } else { " Leave blank if this PC's IP is whitelisted" }
        $answer = (Read-Host "Cashfree 2FA public key file (.pem).$hint").Trim().Trim('"')
        $env:CASHFREE_PUBLIC_KEY_FILE = if ($answer) { $answer } elseif ($known) { $known } else { "" }
    }
    if ($env:CASHFREE_PUBLIC_KEY_FILE -and -not (Test-Path $env:CASHFREE_PUBLIC_KEY_FILE)) {
        Stop-With "no file at $env:CASHFREE_PUBLIC_KEY_FILE (download the public key from the Secure ID dashboard)."
    }
    Set-Content -Path $cfFile -Encoding utf8 -Value @("CASHFREE_CLIENT_ID=$env:CASHFREE_CLIENT_ID", "CASHFREE_PUBLIC_KEY_FILE=$env:CASHFREE_PUBLIC_KEY_FILE")
    if (-not $env:CASHFREE_VERIFICATION_URL) { $env:CASHFREE_VERIFICATION_URL = "https://sandbox.cashfree.com/verification" }
    $where = if ($env:CASHFREE_VERIFICATION_URL -match 'sandbox') { "Cashfree's sandbox (the real DigiLocker sign-in)" } else { "Cashfree LIVE" }
    $twofa = if ($env:CASHFREE_PUBLIC_KEY_FILE) { "calls signed with the public key" } else { "this PC's IP must be whitelisted" }
    Write-Host "DigiLocker: through $where; $twofa. If a link fails, Cashfree's reason appears below."
}
if (-not $env:NIRNAY_STAFF_KEY) { $env:NIRNAY_STAFF_KEY = [guid]::NewGuid().ToString("N") }

$adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
if (Test-Path $adb) {
    # adb writes "daemon not running; starting now" to stderr; Windows PowerShell would treat
    # that as an error under "Stop", so run it through cmd and judge by the exit code only.
    cmd /c "`"$adb`" reverse tcp:8000 tcp:$Port >nul 2>&1"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Phone link: adb reverse, the phone's port 8000 -> this laptop's port $Port (keep the USB cable in)"
    } else {
        Write-Host "Phone link: NO PHONE FOUND. Plug it in with USB debugging on, then run: adb reverse tcp:8000 tcp:$Port"
    }
}
$db = if ($env:DATABASE_URL) { "Supabase / PostgreSQL (DATABASE_URL)" } else { Join-Path $env:NIRNAY_VAR_DIR "nirnay.db" }
Write-Host "Database:  $db"
Write-Host "Staff key: $env:NIRNAY_STAFF_KEY   (for /v1/staff/... calls, header X-Staff-Key)"
$hostAddr = "127.0.0.1"
if ($Lan) {
    $hostAddr = "0.0.0.0"
    $wifi = (Get-NetIPAddress -AddressFamily IPv4 -InterfaceAlias "Wi-Fi*" -ErrorAction SilentlyContinue |
             Where-Object { $_.IPAddress -notmatch '^169\.254' } | Select-Object -First 1).IPAddress
    Write-Host "Wi-Fi:     phones on this Wi-Fi use http://${wifi}:$Port (build the app with app\build-wifi-apk.ps1 -Ip $wifi)"
}
if ($Twilio) {
    Write-Host "Sign-in:   codes go by SMS through Twilio Verify. If one is refused, Twilio's reason appears below."
} else {
    Write-Host "Sign-in codes will appear below."
}
Write-Host "Stop the server with Ctrl+C."
Write-Host "Server:    http://${hostAddr}:$Port (this window shows only sign-in codes and errors)"
# No request log, and only errors from the web server: other programs on this laptop that poll
# the port would otherwise bury the sign-in codes under their 404s and WebSocket warnings.
& (Join-Path $root ".venv\Scripts\python.exe") -m uvicorn backend.app:app --host $hostAddr --port $Port --no-access-log --log-level error
