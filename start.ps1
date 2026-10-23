# One command: build and start Agent Factory in Docker, wait until it answers, open it.   .\start.ps1
$ErrorActionPreference = "Stop"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Write-Error "Docker is not installed (install Docker Desktop)."; exit 1 }
docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Error "Docker is installed but not running: start Docker Desktop and run this again."; exit 1 }
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$port = if ($env:AGENT_FACTORY_PORT) { $env:AGENT_FACTORY_PORT } else { "8000" }
Write-Host -NoNewline "Waiting for http://localhost:$port "
for ($i = 0; $i -lt 90; $i++) {
  try { Invoke-WebRequest -UseBasicParsing "http://localhost:$port/health" -TimeoutSec 3 | Out-Null; $ready = $true; break } catch { Write-Host -NoNewline "."; Start-Sleep 2 }
}
Write-Host ""
if (-not $ready) { Write-Error "It did not answer in 3 minutes. See: docker compose logs api"; exit 1 }
Write-Host "Ready: http://localhost:$port/app     (stop: docker compose stop     logs: docker compose logs -f api)"
Start-Process "http://localhost:$port/app"
