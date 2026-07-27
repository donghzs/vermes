<#
.SYNOPSIS
  Vermes Windows 标准构建脚本（在 Windows 构建机上运行）
  由 Mac 端 trigger-win-build.py 通过 WinRM 调用，或本地管理员 PowerShell 直接跑。

.DESCRIPTION
  流程：清理 → 前端构建 → 同步 web_dist → PyInstaller 后端 → 杀残留进程 → electron-builder NSIS
  已内置全部踩坑防护（见 docs/WINDOWS_BUILD.md 第 5 节）：
    - extraResources 唯一性校验（防 EBUSY）
    - package.json JSON 有效性校验（防 ConvertTo-Json 损坏）
    - 前台阻塞执行 electron-builder（防 WinRM 会话切断）
    - cmd /c 绕过 PowerShell 执行策略（防 npm.ps1 被禁）
    - 构建前杀光 electron-builder/node/nsis/7z 残留（防文件锁）

.PARAMETER Root
  仓库根目录。默认 C:\Projects\vermes-electron
.PARAMETER Python
  Python 解释器路径。默认自动探测 Python312 / Python311 / python
.PARAMETER SkipFrontend
  跳过前端构建（若已构建好 web_dist 可加速）

.EXAMPLE
  .\scripts\build-win-ci.ps1
  .\scripts\build-win-ci.ps1 -Python 'C:\Python312\python.exe' -SkipFrontend
#>

[CmdletBinding()]
param(
    [string]$Root = "C:\Projects\vermes-electron",
    [string]$Python = "",
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$NODE = "C:\Program Files\nodejs"
$env:PATH = "$NODE;$env:PATH"

function Write-Step($n, $msg) {
    Write-Host "`n═══ Step $n : $msg ═══" -ForegroundColor Cyan
}

# ── 0. 参数/工具校验 ──
Write-Step 0 "环境校验"
if (-not (Test-Path $Root)) { throw "ROOT 不存在: $Root" }
Set-Location $Root

# Python 自动探测
if (-not $Python) {
    $candidates = @(
        "C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe",
        "C:\Users\Administrator\AppData\Local\Programs\Python\Python311\python.exe",
        (Get-Command python -ErrorAction SilentlyContinue).Source
    ) | Where-Object { $_ -and (Test-Path $_) }
    if (-not $candidates) { throw "未找到 Python，请用 -Python 指定" }
    $Python = $candidates[0]
}
Write-Host "  Python : $Python"
& $Python --version
& "$NODE\npm.cmd" --version

# package.json 有效性 + extraResources 唯一性
Write-Step "0b" "package.json 校验"
$pjRaw = Get-Content "$Root\package.json" -Raw -Encoding UTF8
try { $pj = $pjRaw | ConvertFrom-Json } catch { throw "package.json 不是合法 JSON: $_" }
$erCount = 0
if ($pj.build.extraResources) { $erCount++ }
if ($pj.build.win -and $pj.build.win.extraResources) { $erCount++ }
if ($pj.build.mac -and $pj.build.mac.extraResources) { $erCount++ }
if ($erCount -ne 1) {
    throw "extraResources 出现 $erCount 次（必须恰好 1 次，否则 electron-builder 并发复制同文件 → EBUSY）。请删除 mac/win 内重复项，只留顶层。"
}
Write-Host "  extraResources 唯一性 OK ($erCount)"

# ── 1. 杀残留进程（防文件锁）──
Write-Step 1 "清理残留进程"
@('electron-builder','node','nsis','7z') | ForEach-Object {
    Get-Process -Name $_ -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

# ── 2. 清理旧产物 ──
Write-Step 2 "清理旧构建产物"
Remove-Item -Recurse -Force "$Root\dist" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$Root\dist-electron" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$Root\build" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$Root\frontend\dist" -ErrorAction SilentlyContinue

# ── 3. 前端依赖 + 构建 ──
if (-not $SkipFrontend) {
    Write-Step 3 "前端 npm install + build"
    Push-Location "$Root\frontend"
    # 用 npm install 而非 npm ci（npm ci 要求 package-lock.json 严格匹配，跨机易 EUSAGE）
    # 2>NUL 屏蔽 stderr，避免 PowerShell 把 npm warning 当 NativeCommandError 中止脚本
    cmd /c "npm install --no-audit --no-fund 2>NUL"
    if ($LASTEXITCODE -ne 0) { throw "npm install 失败 (exit $LASTEXITCODE)" }
    cmd /c "npm run build 2>NUL"
    if ($LASTEXITCODE -ne 0) { throw "npm run build 失败 (exit $LASTEXITCODE)" }
    Pop-Location
} else {
    Write-Step 3 "跳过前端构建 (SkipFrontend)"
}

# ── 4. 同步 web_dist ──
Write-Step 4 "同步前端到 hermes_cli/web_dist"
if (-not (Test-Path "$Root\frontend\dist")) { throw "frontend/dist 不存在，不能 SkipFrontend" }
Copy-Item -Force "$Root\frontend\dist\*" "$Root\hermes_cli\web_dist\" -Recurse
$js = Get-ChildItem "$Root\hermes_cli\web_dist\assets\index-*.js" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $js) { throw "web_dist 缺少 index-*.js，前端构建可能失败" }
Write-Host "  JS: $($js.Name)"

# ── 5. Python 依赖 + PyInstaller 后端 ──
Write-Step 5 "PyInstaller 后端"
& $Python -m pip install --upgrade pip --quiet 2>$null | Select-Object -Last 1
& $Python -m pip install pyinstaller uvicorn fastapi starlette httpx pyyaml aiofiles --quiet 2>$null | Select-Object -Last 1
# PyInstaller 日志写文件；用 cmd /c 包裹让 cmd.exe 处理重定向，避免 PowerShell 把 stderr 当 NativeCommandError 中止
cmd /c "$Python -m PyInstaller vermes-backend.spec --noconfirm > $Root\pyinstaller.log 2>&1"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败 (exit $LASTEXITCODE)，见 $Root\pyinstaller.log" }
Get-Content "$Root\pyinstaller.log" -Tail 8
$exe = Get-ChildItem "$Root\dist\vermes-backend\vermes-backend.exe" -ErrorAction SilentlyContinue
if (-not $exe) { throw "PyInstaller 失败：dist/vermes-backend/vermes-backend.exe 未生成" }
Write-Host "  后端 exe: $([math]::Round($exe.Length/1MB,1)) MB"

# ── 6. 再杀一次残留（PyInstaller 可能留句柄）──
Write-Step 6 "二次清理残留进程"
@('electron-builder','node','nsis','7z') | ForEach-Object {
    Get-Process -Name $_ -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2
Remove-Item -Recurse -Force "$Root\dist-electron\win-unpacked" -ErrorAction SilentlyContinue
Remove-Item -Force "$Root\dist-electron\Vermes Setup*.exe" -ErrorAction SilentlyContinue

# ── 7. electron-builder NSIS（前台阻塞！）──
Write-Step 7 "electron-builder --win --x64 (前台阻塞)"
# 关键：必须用 cmd /c 前台跑，不能 Start-Process（WinRM 会话结束会杀子进程）
# --config.npmRebuild=false 跳过 @electron/rebuild（后端是 Python，无 native node 模块需要重建；rebuild 会因 GBK 损坏的 package.json 报错）
cmd /c ".\node_modules\.bin\electron-builder.cmd --win --x64 --config.npmRebuild=false > $Root\eb_build.log 2>&1"
$exit = $LASTEXITCODE
if ($exit -ne 0) {
    Write-Host "  [X] electron-builder 失败 (exit $exit)，日志尾：" -ForegroundColor Red
    Get-Content "$Root\eb_build.log" -Tail 15
    throw "electron-builder failed"
}

# ── 8. 产物校验 ──
Write-Step 8 "产物校验"
$setup = Get-ChildItem "$Root\dist-electron\Vermes Setup*.exe" | Sort-Object LastWriteTime | Select-Object -First 1
if (-not $setup) { throw "未找到安装包" }
$mb = [math]::Round($setup.Length / 1MB, 1)
$sha = (Get-FileHash $setup.FullName -Algorithm SHA256).Hash
Write-Host "  安装包: $($setup.Name) ($mb MB)"
Write-Host "  SHA256: $sha"

# 内部关键文件
$unpacked = "$Root\dist-electron\win-unpacked"
$checks = @{
    "后端 exe"        = "$unpacked\resources\backend\vermes-backend.exe"
    "vec0.dll"        = "$unpacked\resources\backend\_internal\sqlite_vec\vec0.dll"
    "前端 index.html" = "$unpacked\resources\backend\_internal\hermes_cli\web_dist\index.html"
    "memory_reflection" = "$unpacked\resources\backend\_internal\agent\memory_reflection.py"
}
foreach ($k in $checks.Keys) {
    $ok = Test-Path $checks[$k]
    Write-Host ("  [{0}] {1}" -f $(if($ok){'[OK]'}else{'[X]'}), "$k -> $($checks[$k])")
    if (-not $ok) { Write-Warning "缺失关键文件: $k" }
}

Write-Host "`n═══════════════════════════════════════════════════"
Write-Host "  [OK] BUILD COMPLETE" -ForegroundColor Green
Write-Host "  产物: $($setup.FullName)"
Write-Host "  SHA256: $sha"
Write-Host "  MB: $mb"
Write-Host "═══════════════════════════════════════════════════"
# 输出 sha 供 Mac 端 trigger 脚本捕获
Write-Host "BUILD_SHA256=$sha"
Write-Host "BUILD_MB=$mb"
