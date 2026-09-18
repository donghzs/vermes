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
    [string]$Root = "",
    [string]$Python = "",
    [switch]$SkipFrontend
)

# Root 默认从脚本所在目录推导（避免 WinRM 传参时 \v 被误转义为垂直制表符）
if (-not $Root) {
    $Root = Split-Path -Parent $PSScriptRoot
}

$ErrorActionPreference = "Stop"
$NODE = "C:\Users\Administrator\AppData\Local\hermes\node"
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
# build/ 目录混合了 PyInstaller 中间产物(vermes-backend)与 NSIS 自定义脚本(installer.nsh)。
# 只清 PyInstaller 中间产物，保留 installer.nsh（electron-builder include 依赖它）。
Remove-Item -Recurse -Force "$Root\build\vermes-backend" -ErrorAction SilentlyContinue
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
Write-Step 4 "同步前端到 vermes_cli/web_dist"
# vite outDir 已直出 ../vermes_cli/web_dist（见 frontend/vite.config.js build.outDir），
# 与 Mac build.sh 一致；此处保留 frontend/dist 复制分支仅为兼容历史产物，非必需。
if (Test-Path "$Root\frontend\dist") {
    Copy-Item -Force "$Root\frontend\dist\*" "$Root\vermes_cli\web_dist\" -Recurse
}
$js = Get-ChildItem "$Root\vermes_cli\web_dist\assets\index-*.js" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $js) { throw "web_dist 缺少 index-*.js，前端构建可能失败" }
Write-Host "  JS: $($js.Name)"

# ── 5. Python 依赖 + PyInstaller 后端 ──
Write-Step 5 "PyInstaller 后端"
# 用 cmd /c 包裹：避免 $ErrorActionPreference=Stop 下，pip 的 stderr warning
# （Ignoring invalid distribution ~ip）被 PowerShell 当 NativeCommandError 中止脚本
cmd /c "$Python -m pip install --upgrade pip --quiet 2>NUL"
Write-Host "  安装 Windows 渠道依赖 + sqlite_vec + numpy..."
# A13 系统级代理 127.0.0.1:7897 已失效（代理进程未跑），且出口 IP 曾被 fail2ban 拒。
# 必须 --proxy="" 绕过系统代理 + 阿里云镜像源，否则 pip 全量超时。
#
# ⚠️ 重要：绝不能把 30+ 包塞进一条命令！pilk（需 Rust 编译）、alibabacloud_dingtalk
#    （依赖链构建失败）任一失败会导致 pip 整个事务回滚——一个包都装不上，且被 2>NUL 吞错。
#    这就是 2.4.9 漏包的真正根因。改为分两批：核心批必成功（exit≠0 直接 throw），
#    渠道批逐包容错（失败的仅告警，不影响核心）。
$pipBase = '--proxy="" -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com'
# ── 5a. 核心批：主链路硬依赖，缺任一即构建失败必须中止 ──
$corePkgs = 'pyinstaller uvicorn fastapi starlette httpx pyyaml aiofiles pywin32 openai anthropic cryptography ruamel.yaml python-multipart sqlite-vec numpy pymupdf python-docx lxml psutil tiktoken'
Write-Host "  [5a] 核心依赖（必成功）..."
# 输出重定向到日志（避免 pip 的 'Ignoring invalid distribution ~ip' warning 在
# $ErrorActionPreference=Stop 下被当成 NativeCommandError 中止）；退出码取 cmd /c 的
cmd /c "$Python -m pip install $corePkgs $pipBase --quiet > $Root\core_pip.log 2>&1"
$coreExit = $LASTEXITCODE
if ($coreExit -ne 0) { Get-Content "$Root\core_pip.log" -Tail 25; throw "核心依赖安装失败 (exit $coreExit)，见 $Root\core_pip.log" }
Write-Host "      核心依赖 OK"
# ── 5b. 渠道批：可选渠道依赖，逐包容错（pilk 需 Rust、alibabacloud 依赖链易失败）──
$channelPkgs = @('tenacity','markdown','qrcode','lark-oapi==1.5.3','slack_bolt','slack_sdk','telegram','discord','mautrix','dingtalk_stream','coincurve','mutagen','pynacl','brotlicffi','aiohttp_socks','alibabacloud_dingtalk','pilk')
Write-Host "  [5b] 渠道依赖（容错）..."
foreach ($cp in $channelPkgs) {
    cmd /c "$Python -m pip install $cp $pipBase --quiet > $Root\chan_pip.log 2>&1"
    if ($LASTEXITCODE -ne 0) { Write-Host "      [warn] $cp 安装跳过（非致命）" } else { Write-Host "      [ok] $cp" }
}
# PyInstaller 日志写文件；用 cmd /c 包裹让 cmd.exe 处理重定向，避免 PowerShell 把 stderr 当 NativeCommandError 中止
cmd /c "$Python -m PyInstaller vermes-backend.spec --noconfirm > $Root\pyinstaller.log 2>&1"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败 (exit $LASTEXITCODE)，见 $Root\pyinstaller.log" }
Get-Content "$Root\pyinstaller.log" -Tail 8
$exe = Get-ChildItem "$Root\dist\vermes\vermes.exe" -ErrorAction SilentlyContinue
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
# ELECTRON_MIRROR：A13 直连 GitHub 443 超时，改走 npmmirror 镜像下载 electron 二进制
$env:ELECTRON_MIRROR = 'https://npmmirror.com/mirrors/electron/'
cmd /c "set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/&& .\node_modules\.bin\electron-builder.cmd --win --x64 --config.npmRebuild=false > $Root\eb_build.log 2>&1"
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
    "后端 exe"        = "$unpacked\resources\backend\vermes.exe"
    "vec0.dll"        = "$unpacked\resources\backend\_internal\sqlite_vec\vec0.dll"
    "前端 index.html" = "$unpacked\resources\backend\_internal\vermes_cli\web_dist\index.html"
    "memory_reflection" = "$unpacked\resources\backend\_internal\agent\memory_reflection.py"
}
foreach ($k in $checks.Keys) {
    $ok = Test-Path $checks[$k]
    Write-Host ("  [{0}] {1}" -f $(if($ok){'[OK]'}else{'[X]'}), "$k -> $($checks[$k])")
    if (-not $ok) { Write-Warning "缺失关键文件: $k" }
}

# ── 8b. 关键依赖不漏包硬校验 ──
# 主链路硬依赖（延迟 import 的 PDF/DOCX 解析、核心 SDK、加解密等）。
# 校验方式：检查 _internal 下真实模块目录/文件存在（PyInstaller 对 dist-info 复制不一致，
# 用 dist-info 做断言会误报；模块目录才是真的会被 import 的东西）。缺一直接 throw。
Write-Step "8b" "关键依赖不漏包校验"
$internalDir = "$unpacked\resources\backend\_internal"
# 映射：显示名 -> _internal 下相对路径（目录或 .py/.pyd 文件）
$requiredMods = [ordered]@{
    'openai'          = 'openai'                        # 核心 LLM SDK（process_bootstrap 硬依赖）
    'anthropic'       = 'anthropic'                     # Anthropic 协议端点
    'cryptography'    = 'cryptography'                  # 微信/企微/QQ AESGCM 加解密
    'numpy'           = 'numpy'                         # 数值库
    'ruamel.yaml'     = 'ruamel'                        # 配置原子写 utils.py
    'pymupdf'         = 'pymupdf'                       # PDF 解析 chat.py:293
    'fitz'            = 'fitz'                          # pymupdf 兼容壳（chat.py import fitz）
    'python-docx'     = 'docx'                          # Word 解析/导出 chat.py:307
    'lxml'            = 'lxml'                          # docx 硬依赖
    'psutil'          = 'psutil'                        # Windows 进程树 main.py
    'python-multipart'= 'multipart'                     # FastAPI Form()
    'sqlite-vec'      = 'sqlite_vec'                    # 向量检索 RAG
    'lark-oapi'       = 'lark_oapi'                     # 飞书渠道
    'tiktoken'        = 'tiktoken'                      # 分词（openai 可选依赖）
}
$missingMods = @()
foreach ($k in $requiredMods.Keys) {
    $rel = $requiredMods[$k]
    $candidates = @("$internalDir\$rel", "$internalDir\$rel.py", "$internalDir\$rel.pyc")
    $ok = $false
    foreach ($c in $candidates) { if (Test-Path $c) { $ok = $true; break } }
    if (-not $ok) { $missingMods += $k }
}
if ($missingMods.Count -gt 0) {
    Write-Host "  [X] 漏包: $($missingMods -join ', ')" -ForegroundColor Red
    throw "关键依赖漏包: $($missingMods -join ', ')"
}
Write-Host "  [OK] 关键依赖齐全 ($($requiredMods.Count) 个模块)" -ForegroundColor Green

Write-Host "`n═══════════════════════════════════════════════════"
Write-Host "  [OK] BUILD COMPLETE" -ForegroundColor Green
Write-Host "  产物: $($setup.FullName)"
Write-Host "  SHA256: $sha"
Write-Host "  MB: $mb"
Write-Host "═══════════════════════════════════════════════════"
# 输出 sha 供 Mac 端 trigger 脚本捕获
Write-Host "BUILD_SHA256=$sha"
Write-Host "BUILD_MB=$mb"
