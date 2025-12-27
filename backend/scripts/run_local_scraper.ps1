param(
    [Parameter(Position = 0)]
    [string]$Port
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..\\..")
$BackendDir = Join-Path $RepoRoot "backend"

Set-Location $RepoRoot

if (-not $Port -or $Port.Trim() -eq "") {
    $Port = $env:LOCAL_SCRAPER_PORT
}
if (-not $Port -or $Port.Trim() -eq "") {
    $Port = "5050"
}
if (-not ($Port -match "^\d+$")) {
    Write-Error "Invalid port: $Port"
    exit 1
}

$HostAddr = $env:LOCAL_SCRAPER_HOST
if (-not $HostAddr -or $HostAddr.Trim() -eq "") {
    $HostAddr = "0.0.0.0"
}

$VenvDir = Join-Path $BackendDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\\python.exe"
if (-not (Test-Path $VenvPython)) {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Error "python not found. Install Python 3 and re-run."
        exit 1
    }
    python -m venv $VenvDir
}

& $VenvPython -m pip install -r (Join-Path $BackendDir "requirements.txt")

$env:SELENIUM_HEADED = "true"
if (-not $env:SELENIUM_USER_DATA_DIR) {
    $env:SELENIUM_USER_DATA_DIR = Join-Path $env:USERPROFILE ".local-scraper-profile"
}
if (-not $env:SELENIUM_PROFILE_DIR) {
    $env:SELENIUM_PROFILE_DIR = "Default"
}

try {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, [int]$Port)
    $listener.Start()
    $listener.Stop()
} catch {
    Write-Error "Port $Port is already in use."
    exit 1
}

$UpdateEnv = $env:LOCAL_SCRAPER_UPDATE_ENV
if ($UpdateEnv -eq "1") {
    $EnvFile = $env:LOCAL_SCRAPER_ENV_FILE
    if (-not $EnvFile -or $EnvFile.Trim() -eq "") {
        $EnvFile = Join-Path $BackendDir ".env"
    }
    if (Test-Path $EnvFile) {
        $lines = Get-Content $EnvFile
        if (-not $lines) {
            $lines = @()
        }
        $url = "http://host.docker.internal:$Port"
        function Upsert([string]$Key, [string]$Value, [string[]]$InputLines) {
            $prefix = "$Key="
            $found = $false
            for ($i = 0; $i -lt $InputLines.Count; $i++) {
                if ($InputLines[$i].StartsWith($prefix)) {
                    $InputLines[$i] = "$prefix$Value"
                    $found = $true
                    break
                }
            }
            if (-not $found) {
                $InputLines += "$prefix$Value"
            }
            return $InputLines
        }
        $lines = Upsert "LOCAL_SCRAPER_URL" $url $lines
        $lines = Upsert "LOCAL_SCRAPER_ENABLED" "true" $lines
        Set-Content -Path $EnvFile -Value $lines
        Write-Host "[local_scraper] Updated $EnvFile with LOCAL_SCRAPER_URL=$url"
    } else {
        Write-Host "[local_scraper] Env file not found at $EnvFile; skipping update."
    }
}

Write-Host "[local_scraper] Starting local scraper on $HostAddr`:$Port"
Write-Host "[local_scraper] Docker URL: http://host.docker.internal:$Port"
& $VenvPython -m uvicorn host_scraper:app --host $HostAddr --port $Port
exit $LASTEXITCODE
