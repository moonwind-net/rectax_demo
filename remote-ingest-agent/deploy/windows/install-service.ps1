Param(
    [string]$ServiceName = "RecTaxIngestAgent",
    [string]$AgentDir = "",
    [string]$PythonExe = "",
    [string]$ConfigPath = "",
    [string]$EnvFile = ""
)

$ErrorActionPreference = "Stop"

if (-not $AgentDir) {
    $AgentDir = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
}
if (-not $PythonExe) {
    $cmd = Get-Command python -ErrorAction Stop
    $PythonExe = $cmd.Source
}
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $AgentDir "config.yaml"
}
if (-not $EnvFile) {
    $EnvFile = Join-Path $AgentDir ".env"
}

if (-not (Test-Path $AgentDir)) {
    throw "AgentDir not found: $AgentDir"
}

Write-Host "Using Python: $PythonExe"
& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r (Join-Path $AgentDir "requirements.txt")

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    Write-Host "Service exists. Recreating: $ServiceName"
    Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
    & sc.exe delete $ServiceName | Out-Null
    Start-Sleep -Seconds 2
}

$binPath = "`"$PythonExe`" `"$AgentDir\agent.py`" --config `"$ConfigPath`" --env-file `"$EnvFile`""
New-Service -Name $ServiceName -BinaryPathName $binPath -DisplayName $ServiceName -StartupType Automatic
Start-Service -Name $ServiceName
Get-Service -Name $ServiceName

Write-Host "Installed and started service: $ServiceName"
