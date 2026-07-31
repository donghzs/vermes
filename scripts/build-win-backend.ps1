<#
.SYNOPSIS
  Vermes Windows Backend Builder (Electron 版)
  在 Windows 上 PyInstaller 打包 Python 后端，供 macOS electron-builder 最终打包。

.DESCRIPTION
  用法 (PowerShell 管理员):
    .\scripts\build-win-backend.ps1

  流程:
    1. 检查依赖 (Python, Git, Node.js)
    2. Git Pull 最新代码
    3. 创建/激活 .venv 虚拟环境
    4. pip install -e . + pyinstaller
    5. 构建前端 (npm run build)
    6. 同步前端到 web_dist
    7. 清理旧构建
    8. PyInstaller 打包 (vermes-backend.spec)
    9. 输出到 dist/vermes-backend/
    10. 显示分片传输命令
#>

$ErrorActionPreference = "Stop"
$VERMES_DIR = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$BACKEND_DIR = Join-Path $VERMES_DIR "dist\vermes"
$BACKEND_EXE = Join-Path $BACKEND_DIR "vermes.exe"
$OUTPUT_DIR = Join-Path $VERMES_DIR "dist-electron"

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Vermes Windows Backend Builder v2.0.6" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ── Step 0: 检查依赖 ──
function Check-Command($cmd) {
    try {
        $null = Get-Command $cmd -ErrorAction Stop
        return $true
    } catch {
        return $false
    }
}

Write-Host "[Step 0] 检查依赖..." -ForegroundColor Yellow

if (-not (Check-Command "python")) {
    Write-Host "  ❌ Python 未安装！请安装 Python 3.11+" -ForegroundColor Red
    Write-Host "    下载: https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}
$pyVer = python --version
Write-Host "  ✅ $pyVer"

if (-not (Check-Command "git")) {
    Write-Host "  ⚠️ Git 未安装，跳过代码更新" -ForegroundColor Yellow
    $SKIP_GIT = $true
} else {
    Write-Host "  ✅ Git"
    $SKIP_GIT = $false
}

if (-not (Check-Command "npm")) {
    Write-Host "  ⚠️ npm 未安装，跳过前端构建" -ForegroundColor Yellow
    $SKIP_NPM = $true
} else {
    Write-Host "  ✅ npm"
    $SKIP_NPM = $false
}

# ── Step 1: Git Pull ──
if (-not $SKIP_GIT) {
    Write-Host ""
    Write-Host "[Step 1] Git 拉取最新代码..." -ForegroundColor Yellow
    Push-Location $VERMES_DIR
    try {
        git pull --ff-only
        Write-Host "  ✅ Git pull 完成"
    } catch {
        Write-Host "  ⚠️ Git pull 失败，使用本地代码继续" -ForegroundColor Yellow
    }
    Pop-Location
}

# ── Step 2: 创建 .venv ──
Write-Host ""
Write-Host "[Step 2] 设置 Python 虚拟环境..." -ForegroundColor Yellow
$VENV_DIR = Join-Path $VERMES_DIR ".venv"
if (-not (Test-Path $VENV_DIR)) {
    python -m venv $VENV_DIR
    Write-Host "  ✅ 虚拟环境已创建"
} else {
    Write-Host "  ✅ 虚拟环境已存在"
}

# 激活 .venv
$ACTIVATE = Join-Path $VENV_DIR "Scripts\Activate.ps1"
if (Test-Path $ACTIVATE) {
    & $ACTIVATE
    Write-Host "  ✅ .venv 已激活"
} else {
    Write-Host "  ❌ 找不到 Activate.ps1" -ForegroundColor Red
    exit 1
}

# ── Step 3: pip install ──
Write-Host ""
Write-Host "[Step 3] 安装 Python 依赖..." -ForegroundColor Yellow
pip install -e $VERMES_DIR
pip install pyinstaller
pip install pywin32  # Windows 服务支持
Write-Host "  ✅ pip 依赖已安装"

# ── Step 4: 前端构建 ──
if (-not $SKIP_NPM) {
    Write-Host ""
    Write-Host "[Step 4] 构建前端..." -ForegroundColor Yellow
    Push-Location (Join-Path $VERMES_DIR "frontend")
    try {
        npm install
        npm run build
        Write-Host "  ✅ 前端构建完成"
    } catch {
        Write-Host "  ⚠️ 前端构建失败，使用已有 web_dist" -ForegroundColor Yellow
    }
    Pop-Location

    # 同步到 web_dist
    $FRONTEND_DIST = Join-Path $VERMES_DIR "frontend\dist"
    $WEB_DIST = Join-Path $VERMES_DIR "vermes_cli\web_dist"
    if (Test-Path $FRONTEND_DIST) {
        if (Test-Path $WEB_DIST) {
            Remove-Item -Recurse -Force $WEB_DIST
        }
        Copy-Item -Recurse $FRONTEND_DIST $WEB_DIST
        $fileCount = (Get-ChildItem $WEB_DIST -Recurse | Measure-Object).Count
        Write-Host "  ✅ web_dist 已同步 ($fileCount files)"
    }
}

# ── Step 5: 清理旧构建 ──
Write-Host ""
Write-Host "[Step 5] 清理旧构建..." -ForegroundColor Yellow
foreach ($dir in @("build", "dist")) {
    $p = Join-Path $VERMES_DIR $dir
    if (Test-Path $p) {
        Remove-Item -Recurse -Force $p
        Write-Host "  清理 $dir/"
    }
}

# ── Step 6: PyInstaller 打包 ──
Write-Host ""
Write-Host "[Step 6] PyInstaller 打包后端 (vermes-backend.spec)..." -ForegroundColor Yellow
Push-Location $VERMES_DIR
try {
    python -m PyInstaller vermes-backend.spec --noconfirm
    Write-Host "  ✅ PyInstaller 构建完成"
} catch {
    Write-Host "  ❌ PyInstaller 构建失败！" -ForegroundColor Red
    Write-Host "     错误: $_" -ForegroundColor Red
    Pop-Location
    exit 1
}
Pop-Location

# ── Step 7: 验证输出 ──
Write-Host ""
Write-Host "[Step 7] 验证构建输出..." -ForegroundColor Yellow
if (Test-Path $BACKEND_EXE) {
    $fileSize = (Get-Item $BACKEND_EXE).Length / 1MB
    $totalSize = (Get-ChildItem $BACKEND_DIR -Recurse | Measure-Object Length -Sum).Sum / 1MB
    Write-Host "  ✅ vermes.exe: $([math]::Round($fileSize, 1)) MB" -ForegroundColor Green
    Write-Host "  ✅ 目录总大小: $([math]::Round($totalSize, 1)) MB" -ForegroundColor Green
    Write-Host "  路径: $BACKEND_DIR"
} else {
    Write-Host "  ❌ $BACKEND_EXE 不存在！" -ForegroundColor Red
    exit 1
}

# ── Step 8: 可选注入 VC++ DLL ──
Write-Host ""
Write-Host "[Step 8] 注入 VC++ 运行时 DLL..." -ForegroundColor Yellow
$VC_DLLS = @(
    "vcruntime140.dll", "vcruntime140_1.dll",
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll",
    "vcruntime140_threads.dll"
)
$SYSTEM32 = "$env:SystemRoot\System32"
$injected = 0
foreach ($dll in $VC_DLLS) {
    $src = Join-Path $SYSTEM32 $dll
    if (Test-Path $src) {
        Copy-Item $src (Join-Path $BACKEND_DIR $dll) -Force
        Copy-Item $src (Join-Path $BACKEND_DIR "_internal\$dll") -Force
        $injected++
    }
}
Write-Host "  ✅ 注入 $injected DLL（根目录 + _internal 双份）"

# ── Step 9: 显示传输命令 ──
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  ✅ Windows 后端构建完成！" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "📦 输出: $BACKEND_DIR" -ForegroundColor Cyan
Write-Host ""
Write-Host "⬆️  传输到 Mac 的命令：" -ForegroundColor Yellow
Write-Host ""
Write-Host "  # 在 Windows (A05/A11) 上执行分片：" -ForegroundColor Gray
Write-Host "  cd $VERMES_DIR" -ForegroundColor White
Write-Host "  split -b 30m dist\vermes\vermes.exe vermes-backend.part_" -ForegroundColor White
Write-Host "  # 逐块传: curl -T vermes-backend.part_aa http://<MAC_IP>:8000/" -ForegroundColor White
Write-Host ""
Write-Host "  # 在 Mac 上接收后拼接：" -ForegroundColor Gray
Write-Host "  cd ~/Projects/vermes-electron" -ForegroundColor White
Write-Host "  cat vermes-backend.part_* > dist/vermes-backend/vermes.exe" -ForegroundColor White
Write-Host "  chmod +x dist/vermes-backend/vermes.exe" -ForegroundColor White
Write-Host ""
Write-Host "📎 完整传输 backend 目录也可打包 zip:" -ForegroundColor Gray
Write-Host "  Compress-Archive -Path dist\vermes-backend\* -DestinationPath vermes-backend.zip" -ForegroundColor White
Write-Host "  # 然后用网盘/HTTP 传输到 Mac" -ForegroundColor White
