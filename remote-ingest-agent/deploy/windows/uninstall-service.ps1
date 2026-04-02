Param(
    [string]$ServiceName = "RecTaxIngestAgent"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) {
    Write-Host "Service not found: $ServiceName"
    exit 0
}

Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
& sc.exe delete $ServiceName | Out-Null
Start-Sleep -Seconds 2

Write-Host "Uninstalled service: $ServiceName"
