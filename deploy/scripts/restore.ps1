param(
  [Parameter(Mandatory = $true)][string]$BackupFile,
  [switch]$ConfirmRestore
)

$ErrorActionPreference = "Stop"
if (-not $ConfirmRestore) { throw "恢复会覆盖当前数据库；请核对备份后使用 -ConfirmRestore。" }
$resolvedRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$resolvedBackup = (Resolve-Path -LiteralPath $BackupFile).Path
Get-Content -Raw -LiteralPath $resolvedBackup | docker compose -f (Join-Path $resolvedRoot "docker-compose.yml") exec -T postgres psql -v ON_ERROR_STOP=1 -U roadman -d roadman
Write-Output "数据库恢复完成：$resolvedBackup"
