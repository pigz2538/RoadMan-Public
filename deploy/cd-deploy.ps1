# RoadMan 一键 CD 部署脚本
# 流程：git 提交 → 推送双远端 → 本地 docker 重建 → 打包 → 传输服务器 → 服务器重建 → HTTPS/隧道保障 → 汇总汇报
# 用法: powershell -ExecutionPolicy Bypass -File deploy/cd-deploy.ps1 [-Commit "提交信息"] [-SkipPush] [-SkipLocal] [-SkipServer]

param(
  [string]$Commit = "",
  [switch]$SkipPush,
  [switch]$SkipLocal,
  [switch]$SkipServer
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Server = '10.10.51.136'
$ServerUser = 'z2538'
$ServerDir = '/home/z2538/Desktop/RoadMan'
$ExtUrl = 'https://roadman.pigz2538.top:55309'
$Tar = Join-Path $env:TEMP 'roadman-cd.tar'
$ServerScript = 'deploy/server-deploy.sh'
$SudoPass = $env:ROADMAN_SUDO_PASS
$Results = [System.Collections.Generic.List[string]]::new()

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "[OK] $msg" -ForegroundColor Green; $Results.Add("[OK] $msg") }
function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; $Results.Add("[FAIL] $msg") }

Set-Location $Root

# ---------- 1. git 提交 ----------
if (-not $SkipPush) {
  Step "1/6 git 提交"
  if (-not $Commit) { $Commit = Read-Host "提交信息" }
  if (-not $Commit) { throw "提交信息不能为空" }
  git add -A
  git commit -m $Commit
  if ($?) { Ok "已提交: $Commit" } else { throw "git commit 失败" }
}

# ---------- 2. 推送双远端 ----------
if (-not $SkipPush) {
  Step "2/6 推送双远端"
  git push origin main 2>&1 | Out-Null
  if ($LASTEXITCODE -eq 0) { Ok "已推送 RoadMan + RoadMan-Public (main)" } else { throw "git push 失败" }
}

# ---------- 3. 本地 docker 重建 ----------
if (-not $SkipLocal) {
  Step "3/6 本地 docker 重建"
  docker compose build 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "本地 docker build 失败" }
  docker compose up -d 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "本地 docker up 失败" }
  Start-Sleep -Seconds 5
  $fe = (curl.exe -s -o NUL -w "%{http_code}" http://localhost:8080)
  $be = (curl.exe -s -o NUL -w "%{http_code}" http://localhost:8000/health)
  if ($fe -eq '200' -and $be -eq '200') { Ok "本地容器已重建 (frontend $fe / backend $be)" }
  else { Fail "本地容器状态异常 (frontend $fe / backend $be)" }
}

# ---------- 4. 打包 ----------
Step "4/6 打包代码"
if (Test-Path $Tar) { Remove-Item $Tar -Force }
tar -cf $Tar --exclude=".git" --exclude="node_modules" --exclude="dist" --exclude="test-results" --exclude="roadman.db" --exclude="*.log" --exclude=".env" --exclude="roadman-certs" --exclude=".codegraph" --exclude=".omo" --exclude="tmp" --exclude="artifacts" .
if ($LASTEXITCODE -ne 0) { throw "打包失败" }
Ok "打包完成 ($([math]::Round((Get-Item $Tar).Length/1MB,1)) MB)"

# ---------- 5. 传输 + 服务器重建 ----------
if (-not $SkipServer) {
  Step "5/6 传输到服务器并重建"
  if (-not $SudoPass) { $SudoPass = Read-Host "服务器 sudo 密码" }
  scp -o ConnectTimeout=10 $Tar "${ServerUser}@${Server}:${ServerDir}/roadman-cd.tar"
  if ($LASTEXITCODE -ne 0) { throw "scp 传输失败" }
  scp -o ConnectTimeout=10 $ServerScript "${ServerUser}@${Server}:/tmp/server-deploy.sh"
  if ($LASTEXITCODE -ne 0) { throw "scp 脚本传输失败" }
  $out = ssh -o ConnectTimeout=10 ${ServerUser}@${Server} "echo $SudoPass | sudo -S sh /tmp/server-deploy.sh ${ServerDir}/roadman-cd.tar" 2>&1
  $out | ForEach-Object { Write-Host $_ }
  if ($out -match 'RESULT: 外网隧道 OK') { Ok "服务器重建完成，外网隧道正常" }
  elseif ($out -match 'RESULT: 外网隧道仍不可达') { Fail "服务器重建完成，但外网隧道仍不可达" }
  else { Fail "服务器部署脚本执行异常，请检查输出" }
}

# ---------- 6. 汇总 ----------
Step "6/6 汇总"
Write-Host ""
Write-Host "========== RoadMan CD 部署结果 ==========" -ForegroundColor Cyan
$Results | ForEach-Object { Write-Host $_ }
$failed = $Results | Where-Object { $_ -like '[FAIL]*' }
if ($failed) {
  Write-Host "`n存在失败项，请检查上方输出。" -ForegroundColor Red
  exit 1
} else {
  Write-Host "`n全部完成：推送、本地 docker、服务器 docker、外网隧道均已就绪。" -ForegroundColor Green
  exit 0
}
