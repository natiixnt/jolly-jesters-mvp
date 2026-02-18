[CmdletBinding()]
param(
  [int]$IntervalSeconds = 15,
  [int]$GraceSeconds = 60,
  [switch]$Once,
  [string]$ComposeDir,
  [string]$DockerDesktopPath = "C:\Program Files\Docker\Docker\Docker Desktop.exe",
  [int]$DockerStartupTimeoutSeconds = 120,
  [int]$DockerStartupPollSeconds = 3
)

$ErrorActionPreference = "Stop"

if (-not $ComposeDir) {
  $ComposeDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Ensure-Docker {
  try {
    docker version | Out-Null
    return $true
  } catch {
    try {
      $svc = Get-Service -Name "com.docker.service" -ErrorAction Stop
      if ($svc.Status -ne "Running") {
        Start-Service -Name "com.docker.service" -ErrorAction Stop
      }
    } catch {
      # ignore (no perms or service missing)
    }

    if (Test-Path $DockerDesktopPath) {
      $proc = Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue
      if (-not $proc) {
        Start-Process -FilePath $DockerDesktopPath | Out-Null
      }
    }

    $startedAt = Get-Date
    while (((Get-Date) - $startedAt).TotalSeconds -lt $DockerStartupTimeoutSeconds) {
      try {
        docker version | Out-Null
        return $true
      } catch {
        Start-Sleep -Seconds $DockerStartupPollSeconds
      }
    }

    return $false
  }
}

function Get-ComposeServices {
  $json = ""
  try {
    $json = docker compose ps --format json 2>$null
  } catch {
    return @()
  }

  if (-not $json) {
    return @()
  }

  try {
    $parsed = $json | ConvertFrom-Json
    if ($null -eq $parsed) {
      return @()
    }
    return @($parsed)
  } catch {
    return @()
  }
}

function Test-ServiceDown([object]$svc) {
  $state = ""
  $status = ""
  $health = ""

  if ($null -ne $svc.State) { $state = ($svc.State.ToString()).ToLowerInvariant() }
  if ($null -ne $svc.Status) { $status = ($svc.Status.ToString()).ToLowerInvariant() }
  if ($null -ne $svc.Health) { $health = ($svc.Health.ToString()).ToLowerInvariant() }

  if ($state -and $state -ne "running") { return $true }
  if ($health -eq "unhealthy") { return $true }
  if ($status -match "exited|dead|restarting") { return $true }
  return $false
}

$lastRestart = Get-Date "1970-01-01T00:00:00Z"

do {
  if (-not (Ensure-Docker)) {
    Write-Host ("{0} docker_not_ready" -f (Get-Date -Format s))
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSeconds
    continue
  }

  Push-Location $ComposeDir
  try {
    $services = Get-ComposeServices
    $needRestart = $false
    $downServices = @()

    if ($services.Count -eq 0) {
      $needRestart = $true
    } else {
      foreach ($svc in $services) {
        if (Test-ServiceDown $svc) {
          $needRestart = $true
          $downServices += $svc.Service
        }
      }
    }

    $now = Get-Date
    if ($needRestart -and (($now - $lastRestart).TotalSeconds -ge $GraceSeconds)) {
      $names = if ($downServices.Count -gt 0) { $downServices -join "," } else { "unknown" }
      Write-Host ("{0} restart_needed services={1}" -f (Get-Date -Format s), $names)
      docker compose up -d --build | Out-Null
      $lastRestart = Get-Date
    } else {
      Write-Host ("{0} ok" -f (Get-Date -Format s))
    }
  } finally {
    Pop-Location
  }

  if ($Once) { break }
  Start-Sleep -Seconds $IntervalSeconds
} while ($true)
