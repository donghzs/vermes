<# ─────────────────────────────────────────────────────────────
   Vermes Windows Builder (Electron 架构 · 自带启验证)
   在真 Windows 机器上运行（仓库根目录）。无需 GitHub。
   用法:  cd <repo-root>;  powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
   产物:  electron\dist-electron\Vermes Setup x64.exe
# ───────────────────────────────────────────────────────────── #>
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot | Split-Path -Parent
Set-Location $repo
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Vermes Windows Builder (Electron)"    -ForegroundColor Cyan
Write-Host "  repo: $repo"                          -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# ── 0. 前置检查 ───────────────────────────────────────────────
if ($env:OS -notmatch 'Windows') { Write-Error "此脚本只能在 Windows 上运行"; exit 1 }

# Python: 必须用 3.12（3.13 在 Windows 上打包后端会崩）
$py = 'python'
try { $pv = & $py --version 2>&1 } catch { Write-Error "找不到 python，请先安装 Python 3.12"; exit 1 }
if ($pv -notmatch '3\.12\.') {
  Write-Warning "检测到 $pv —— Windows 后端要求 Python 3.12（3.13 已知不可用）。尝试 py -3.12 ..."
  if (Get-Command py -ErrorAction SilentlyContinue) { $py = 'py -3.12' } else { Write-Error "请安装 Python 3.12 后重试"; exit 1 }
}

# Node
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Write-Error "找不到 node，请先安装 Node.js 20"; exit 1 }

# ── 1. Python 依赖（含 web/vec/edge-tts/anthropic，否则后端启动即崩）──
Write-Host "[1/6] 安装 Python 依赖 (Python 3.12 + extras)..." -ForegroundColor Yellow
& $py -m pip install --upgrade pip
& $py -m pip install -e ".[web,vec,edge-tts,anthropic]" pyinstaller pywin32
if ($LASTEXITCODE -ne 0) { Write-Error "pip install 失败"; exit 1 }

# ── 2. 前端构建 + 同步到 web_dist ──────────────────────────────
Write-Host "[2/6] 构建前端并同步到 hermes_cli/web_dist..." -ForegroundColor Yellow
Push-Location frontend
& npm install
& npm run build
Pop-Location
if (Test-Path hermes_cli/web_dist) { Remove-Item -Recurse -Force hermes_cli/web_dist }
Copy-Item -Recurse frontend/dist hermes_cli/web_dist
Write-Host "  web_dist 同步完成 ($( (Get-ChildItem hermes_cli/web_dist -Recurse | Measure-Object).Count ) 个文件)"

# ── 3. PyInstaller 打包后端 ───────────────────────────────────
Write-Host "[3/6] PyInstaller 打包后端 (vermes-backend.spec)..." -ForegroundColor Yellow
& $py -m PyInstaller vermes-backend.spec --noconfirm
if (-not (Test-Path dist/vermes-backend/vermes-backend.exe)) { Write-Error "后端 exe 未生成"; exit 1 }

# ── 4. 注入 VC++ 运行时 ───────────────────────────────────────
Write-Host "[4/6] 注入 VC++ 运行时 DLL..." -ForegroundColor Yellow
$BD = "dist/vermes-backend"
$VC = @("vcruntime140.dll","vcruntime140_1.dll","msvcp140.dll","msvcp140_1.dll","msvcp140_2.dll","vcruntime140_threads.dll")
$cnt = 0
foreach ($d in $VC) {
  $s = Join-Path $env:SystemRoot System32 $d
  if (Test-Path $s) { Copy-Item $s (Join-Path $BD $d) -Force; if (Test-Path (Join-Path $BD _internal)) { Copy-Item $s (Join-Path $BD _internal $d) -Force }; $cnt++ }
}
Write-Host "  注入 $cnt 个 VC++ DLL"

# ── 5. 启动验证（证明在 Windows 上真的能跑）──────────────────
Write-Host "[5/6] 启动后端并验证 /health ..." -ForegroundColor Yellow
$p = Start-Process -FilePath "$BD/vermes-backend.exe" -ArgumentList "--port","9119" `
     -PassThru -RedirectStandardOutput backend.smoke.out.log -RedirectStandardError backend.smoke.err.log
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
  try {
    $r = Invoke-RestMethod -Uri "http://127.0.0.1:9119/health" -TimeoutSec 2 -ErrorAction Stop
    if ($r.status -eq 'ok') { Write-Host "  HEALTH OK: version=$($r.version)" -ForegroundColor Green; $ok = $true; break }
  } catch { }
  Start-Sleep -Seconds 1
}
Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
if (-not $ok) {
  Write-Error "后端 90s 内未就绪，构建无效！日志:"; Get-Content backend.smoke.out.log, backend.smoke.err.log -ErrorAction SilentlyContinue
  exit 1
}
Write-Host "  后端启动验证通过 ✓" -ForegroundColor Green

# ── 6. Electron NSIS 安装包 ───────────────────────────────────
Write-Host "[6/6] 构建 Electron NSIS 安装包..." -ForegroundColor Yellow
Push-Location electron
& npm install
& npx electron-builder --win --publish=never
Pop-Location
$inst = Get-ChildItem dist-electron -Filter "Vermes Setup *.exe" | Select-Object -First 1
if (-not $inst) { Write-Error "未生成安装包"; exit 1 }

Write-Host "========================================" -ForegroundColor Green
Write-Host "  构建成功且已验证！" -ForegroundColor Green
Write-Host "  安装包: $($inst.FullName)" -ForegroundColor Green
Write-Host "  大小:   $([math]::Round($inst.Length/1MB,1)) MB" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
