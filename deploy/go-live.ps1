<#
Puts KalJahaz online at https://kaljahaz.pages.dev, with this PC as the backend.

  deploy\go-live.ps1

Start the server first (py server.py). This then:
  1. starts cloudflared, which gives a random https://<words>.trycloudflare.com address
  2. waits until that address answers from outside
  3. writes it into web\config.js
  4. deploys web\ to the Cloudflare Pages project "kaljahaz"

A quick tunnel is free but its address changes every time it starts, so run this again after any
restart of the PC, the server or the tunnel. Leave the minimized cloudflared window running; closing
it takes the site's forecasts offline. Needs cloudflared (winget install --id Cloudflare.cloudflared)
and wrangler logged in to your Cloudflare account (npx wrangler login).
#>
param([string]$Project = "kaljahaz", [string]$Branch = "main", [int]$Port = 8000)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$cloudflared = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $cloudflared) {
    foreach ($p in "$env:ProgramFiles\cloudflared\cloudflared.exe", "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe") {
        if (Test-Path $p) { $cloudflared = $p }
    }
}
if (-not $cloudflared) { throw "cloudflared is not installed. Run: winget install --id Cloudflare.cloudflared" }

try { Invoke-WebRequest "http://127.0.0.1:$Port/api/status" -UseBasicParsing -TimeoutSec 10 | Out-Null }
catch { throw "Nothing answers on port $Port. Start the server first: py server.py" }

# the previous tunnel's address is dead once a new one starts, so never run two
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force

New-Item -ItemType Directory -Force "$root\logs" | Out-Null
$log = Join-Path "$root\logs" ("tunnel-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))
Get-ChildItem (Join-Path "$root\logs" "tunnel-*.log") -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -Skip 5 | Remove-Item -ErrorAction SilentlyContinue
# HTTP/2 over TCP 443: hotspots and campus networks often block cloudflared's default QUIC over UDP
$protocol = if ($env:TUNNEL_PROTOCOL) { $env:TUNNEL_PROTOCOL } else { "http2" }
Start-Process $cloudflared -ArgumentList "tunnel --no-autoupdate --protocol $protocol --url http://127.0.0.1:$Port --logfile `"$log`"" `
    -WindowStyle Minimized

Write-Host "Waiting for the tunnel address..."
$url = $null
for ($i = 0; $i -lt 60 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    if (Test-Path $log) {
        $m = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" | Select-Object -Last 1
        if ($m) { $url = $m.Matches[0].Value }
    }
}
if (-not $url) { throw "cloudflared gave no address within a minute. Its log: $log" }
Write-Host "Tunnel: $url"

$registered = $false
for ($i = 0; $i -lt 60 -and -not $registered; $i++) {
    if (Select-String -Path $log -Pattern "Registered tunnel connection" -Quiet) { $registered = $true }
    else { Start-Sleep -Seconds 1 }
}
if (-not $registered) { throw "cloudflared got $url but never connected ($protocol). The network may block it. Log: $log" }

# ask from outside, as a visitor would, through Cloudflare's DNS (Windows caches a first "no such name")
$curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
if ($curl) {
    $probe = & $curl -s -o NUL -w "%{http_code}" -m 8 --doh-url https://1.1.1.1/dns-query https://www.cloudflare.com/cdn-cgi/trace 2>$null
    if ($probe -ne "200") { $curl = $null }
}
$live = $false
for ($i = 0; $i -lt 45 -and -not $live; $i++) {
    if ($curl) {
        $code = & $curl -s -o NUL -w "%{http_code}" -m 8 --doh-url https://1.1.1.1/dns-query "$url/api/status" 2>$null
        $live = $code -eq "200"
    } else {
        try { Invoke-WebRequest "$url/api/status" -UseBasicParsing -TimeoutSec 8 | Out-Null; $live = $true } catch { }
    }
    if (-not $live) { Start-Sleep -Seconds 2 }
}
if (-not $live) { throw "$url is connected but never answered from outside, so the site was left as it was. Log: $log" }

$config = "$root\web\config.js"
$text = [IO.File]::ReadAllText($config)
$text = [regex]::Replace($text, '\? "https://[^"]*" :', "? `"$url`" :")
[IO.File]::WriteAllText($config, $text)
Write-Host "web\config.js now points at $url"

npx --yes wrangler pages deploy web --project-name $Project --branch $Branch --commit-dirty=true
Write-Host ""
Write-Host "Live at https://$Project.pages.dev (backend: $url). Keep the cloudflared window open."
