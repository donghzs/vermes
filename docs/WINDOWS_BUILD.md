# Vermes Windows 版本标准构建流程

> 适用版本：Vermes 2.3.5+（Electron 壳 + PyInstaller 后端）
> 维护状态：✅ 主构建脚本 `build-win-ci.ps1` 已通过 2026-07-27 实战验证（A11 机器，208MB 产物，sha256 见 version.json）。
> ⚠️ Mac 端一键触发脚本 `trigger-win-build.py` 为**实验性**，今天实战翻车多次（详见第 3 节），**不要依赖**，仅作参考。
> 设计目标：**换一台局域网 Windows 电脑、换个环境，照本流程 + 脚本即可快速重建**

---

## 0. 架构一句话

Windows 包 = **Electron 壳**（前端 GUI + 进程管理）+ **PyInstaller 后端**（Python Agent 运行时）。
两者**必须在 Windows 机器上本地构建**（不能 Mac 交叉编译 Windows 后端），最后由 `electron-builder --win` 把后端作为 `extraResources` 塞进 NSIS 安装包。

```
Mac (源码真相源)
  │  git / tar 推送
  ▼
Windows 构建机 (A11: 192.168.1.7)
  ├─ PyInstaller vermes-backend.spec  → dist/vermes-backend/vermes-backend.exe  (后端)
  └─ electron-builder --win --x64       → dist-electron/Vermes Setup x.y.z.exe   (安装包)
  │  scp / HTTP 回传
  ▼
vbit.top/vermes/downloads/  ← 用户下载
```

**关键约束（踩坑得来）**：
1. 后端**只能**在 Windows 上 PyInstaller（Mac 上打不出可用的 Windows 后端 exe）
2. `extraResources` 全仓库**只能有 1 处**指向 `dist/vermes-backend`，多了会 EBUSY 并发锁死
3. `electron-builder` **必须在前台一次性跑完**，不能 `Start-Process` 后台（WinRM 会话结束会杀掉子进程）
4. `package.json` 严禁用 PowerShell `ConvertTo-Json` 改写（会损坏 JSON，emoji/转义炸）

---

## 1. 环境准备（新机器一次性）

### 1.1 Windows 构建机
| 组件 | 要求 | 备注 |
|------|------|------|
| OS | Windows 10/11 64bit | 实测 10.0.19045 |
| Python | 3.12.x | 路径如 `C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe` |
| Node.js | ≥ 20（含 npm） | 装到 `C:\Program Files\nodejs` |
| Git | 任意 | 用于 git 拉取（可选，也可 tar 推） |
| PowerShell | 管理员 | 执行策略默认禁 `npm.ps1`，脚本内已用 `cmd /c` 绕过 |
| 网络 | 局域网可达 Mac | 用于源码推送 / 产物回传 |

> ⚠️ **Python 版本一致性**：A11 用 3.12，后端 `_internal` 约 177MB。若换 3.11 体积略不同但功能一致。换机器时**记录实际 Python 路径**并在脚本里改 `$PYTHON` 变量即可。

### 1.2 Mac 控制机（触发构建）
| 组件 | 用途 |
|------|------|
| `pywinrm` (`pip install pywinrm`) | WinRM 远程执行 PowerShell |
| `sshpass` | 备选 SSH 回传产物到 vbit.top |
| Python 3 | 跑触发脚本 |

---

## 2. 标准构建流程（在 Windows 机上跑 `scripts/build-win-ci.ps1`）

> 脚本已内置全部坑防护。以下步骤等价于脚本逻辑，供人工对照。

### Step 0 — 清理旧产物
```
Remove-Item -Recurse -Force dist, dist-electron, build, frontend\dist
```

### Step 1 — 校验工具链
- `node --version` / `npm --version`
- `python --version`（必须能找到 vermes-backend.spec 里的依赖）

### Step 2 — 前端依赖 + 构建
```
cd frontend
cmd /c "npm install"     # 用 install 不用 ci（ci 要求 lock 严格匹配，跨机易 EUSAGE）
cmd /c "npm run build"   # 产出 frontend/dist/
```

> 实际脚本里这步已处理：npm install 的 stderr 通过 `2>NUL` 屏蔽，避免 PowerShell 把 warning 当 NativeCommandError 中止脚本。

### Step 3 — 同步前端到后端 web_dist
```
Copy-Item -Force frontend\dist\* hermes_cli\web_dist\ -Recurse
# 校验 hermes_cli/web_dist/assets/index-*.js 存在
```

### Step 4 — Python 依赖 + PyInstaller 后端
```
python -m pip install --upgrade pip
python -m pip install pyinstaller uvicorn fastapi starlette httpx pyyaml aiofiles
python -m PyInstaller vermes-backend.spec --noconfirm
# 产出 dist/vermes-backend/vermes-backend.exe
```

### Step 5 — **杀残留进程**（关键！防 EBUSY）
```
Get-Process -Name electron-builder,node,nsis,7z -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 2
```

### Step 6 — electron-builder NSIS（**前台阻塞跑**）
```
cd electron   # 或仓库根（package.json 在根）
cmd /c ".\node_modules\.bin\electron-builder.cmd --win --x64"
# 必须等它返回 exit 0，不能 Start-Process 后台
```

### Step 7 — 产物校验
```
dist-electron\Vermes Setup 2.3.5.exe   # 应 ~208MB（含后端）
# 校验内部：resources/backend/vermes-backend.exe
#           resources/backend/_internal/sqlite_vec/vec0.dll  ← Windows 向量检索
#           resources/backend/_internal/hermes_cli/web_dist/index.html
```

---

## 3. 推荐构建模式（重要：先读这个再决定怎么搞）

### 3.0 三种模式对比

| 模式 | 做法 | 靠谱度 | 说明 |
|------|------|--------|------|
| **A. A11 本地 git pull + 跑 ps1（推荐）** | 在 A11 上 `git pull` 拿最新源码 → 直接 `powershell -ExecutionPolicy Bypass -File scripts/build-win-ci.ps1` | ✅✅✅ 最稳 | A11 已配好 node_modules 缓存 + Python，几分钟出包。**下次构建首选这条** |
| **B. Mac agent 分步手动搞（次选）** | agent 用 WinRM 把源码推到 A11（或 A11 直接 git pull）→ 触发 ps1 → 回传 exe | ✅✅ 稳 | 比全自动省心，agent 可控每一步，出问题能立刻救 |
| **C. Mac 一键 trigger 全自动** | 跑 `trigger-win-build.py` 一条龙 | ❌ 不稳 | 今天实战翻车：端口冲突、WinRM 残留进程叠加、HTTP 小文件传输间歇 0 字节、解压时 Move 目录把 A11 搞乱。**不推荐依赖** |

### 3.1 为什么全自动 trigger 不靠谱（今天踩的）

1. **WinRM 跨机调用脆弱**：Mac 端 `pkill` 杀不掉 A11 上的 WinRM 服务端进程，导致多次 trigger 的进程在 A11 上**叠加**，互相抢目录/文件锁。
2. **HTTP 小文件传输间歇失败**：A11 用 `curl --noproxy` 拉 Mac 上的脚本/package.json，偶尔返回 0 字节（WinRM 网络怪象），构建脚本因此加载失败。
3. **解压时的 Move 目录有破坏性**：trigger 先 `Move-Item vermes-electron → .bak` 再解压，一旦中途失败，A11 主目录就丢了，需要人工救。
4. **npm install 在 WinRM 下慢且偶发卡住**（系统代理/网络环境不同）。

> 结论：**别追求一条命令全自动**。A11 已经有完整 git 仓库 + node_modules 缓存，下次构建只需要 `git pull` + 跑 `build-win-ci.ps1`，这是最省心最稳的。Mac 端 trigger 脚本留着当参考，但别当主力。

### 3.2 推荐的标准动作（模式 A，下次直接用）

在 A11（或 Mac 上用 WinRM 一条命令）执行：
```powershell
# A11 本地（RDP / 或 Mac WinRM 单行调用）
cd C:\Projects\vermes-electron
 git pull origin <分支>            # 拿 Mac 已 commit 的最新源码
powershell -ExecutionPolicy Bypass -File scripts\build-win-ci.ps1 -Python 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
# 产物在 dist-electron\Vermes Setup x.y.z.exe
```

在 Mac 上用 WinRM 单行触发（不依赖 trigger 脚本）：
```bash
python3 - <<'PY'
import winrm
 s = winrm.Session('http://192.168.1.7:5985/wsman', auth=('Administrator','<A11-admin-password>'), transport='ntlm', operation_timeout_sec=900, read_timeout_sec=930)
 ps = '''cd C:\\Projects\\vermes-electron; git pull origin feature/shutong-provider; powershell -ExecutionPolicy Bypass -File scripts\\build-win-ci.ps1 -Python C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe 2>&1'''
 r = s.run_ps(ps)
 print(r.std_out.decode('gbk','replace'))
PY
```

> 注意 WinRM 调用若超 15 分钟易断，A11 上 node_modules 已缓存时整条约 3-5 分钟，问题不大。

## 4. （旧）Mac 触发脚本说明（实验性，勿依赖）

`scripts/trigger-win-build.py` —— 设计上在 Mac 上跑，自动推源码→构建→回传 exe。
**今天实战反复翻车（见 3.1），不建议作为构建主流程**。若要用，需先解决：端口冲突自动+1、WinRM 残留进程清理、HTTP 传输稳定性。

```bash
# 仅参考，不保证可用
python3 scripts/trigger-win-build.py \
  --win-host 192.168.1.7 \
  --win-user Administrator \
  --win-pass '<A11-admin-password>' \
  --python 'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe'
```

---

## 5. 发布到 vbit.top

产物回传 Mac 后：
```bash
# 1. 计算 sha256
shasum -a 256 /tmp/winupload/Vermes-Setup-2.3.5.exe

# 2. 更新 version.json 的 windows sha256（用 python json 改，别手改坏格式）
python3 -c "
import json
d=json.load(open('version.json'))
d['sha256']['windows']='<上面的sha>'
json.dump(d, open('version.json','w'), indent=2, ensure_ascii=False)
"

# 3. 上传
 scp /tmp/winupload/Vermes-Setup-2.3.x.exe <user>@<server-ip>:/var/www/html/vermes/downloads/
scp version.json <user>@<server-ip>:/var/www/html/vermes/

# 4. 验证
curl -sI https://vbit.top/vermes/downloads/Vermes-Setup-2.3.5.exe   # 200 + Content-Length 匹配
curl -s https://vbit.top/vermes/version.json | python3 -c "import json,sys;print(json.load(sys.stdin)['sha256']['windows'])"
```

---

## 6. 经验教训（踩过的坑，必读）

| # | 现象 | 根因 | 解决 |
|---|------|------|------|
| **1** | `electron-builder` 报 `EBUSY: resource busy or locked` 复制 `vermes-backend.exe` 失败，产物 118MB（缺后端） | `package.json` 里 `build.mac/win/顶层` **三处都写了 extraResources** 指向同一 `dist/vermes-backend`，electron-builder 并发复制 3 遍到同一目标 `backend` 目录 → 文件锁冲突 | **只保留顶层 1 处 extraResources**，删 mac/win 内的重复项。脚本 `Test-Json` 会校验 extraResources 唯一性 |
| **2** | `read-package-json` 报 `Unexpected token '??` JSON 解析失败，构建 exit 1 | 用 PowerShell `ConvertTo-Json \| Set-Content` 改 `package.json`，把 emoji(`✅`)/转义搞坏 | **绝不用 PowerShell 改写 JSON**。Mac 端改好用 HTTP 推送覆盖；或脚本内用 `python -c "json.load"` 校验有效性 |
| **3** | `Start-Process electron-builder` 后台启动，但 exe 永远出不来，日志停在中间 | WinRM PowerShell 会话结束会**杀掉所有派生子进程**，后台 build 被切断 | **前台阻塞跑**：`cmd /c "electron-builder.cmd ..."` 等它返回。脚本用 `&` / `cmd /c` 前台执行 |
| **4** | PowerShell 直接 `npm install` 报 `npm.ps1 cannot be loaded because running scripts is disabled` | 系统 ExecutionPolicy 禁脚本 | 用 `cmd /c "npm install"` 绕过；或 `Set-ExecutionPolicy RemoteSigned -Force` |
| **5** | Windows 机有系统代理，`WebClient.DownloadFile` 下到 9628 字节（vbit 首页）而非源码 tar | WebClient 即使 `Proxy=$null` 仍走系统代理 | 用 `curl.exe --noproxy *` 直连 Mac HTTP server（已验证 370MB 完整下载） |
| **6** | 之前从 Mac 上 `electron-builder --win` 交叉打包，产物 583MB（后端被 `files:["dist/**/*"]` 打进 app.asar 又 extraResources 复制一份） | files 配置把 `dist/vermes-backend` 也收进 asar，造成双份 | `files` 只列 `electron/main.js, splash.html, package.json`；后端只走 `extraResources` |
| **7** | A11 IP 从 192.168.1.6（7/24）漂到 192.168.1.7（7/27，DHCP） | 局域网 DHCP 重分配 | **构建前先 ping/WinRM 探活确认 IP**，脚本参数化传入，不写死 |
| **8** | Windows 中文路径/日志 GBK 乱码导致 Python 启动失败 | 旧版 bootstrap 未设 UTF-8 | 确保 `hermes_bootstrap.py` 在入口设置 `sys.stdout.reconfigure(encoding='utf-8')`（2.3.5 已含） |
| **9** | `main.js` 启动报 `Cannot find module 'electron-updater'` | autoUpdater 死代码 require 了不在 dependencies 的包 | 已删除 main.js 中 autoUpdater 整块（require + 事件 + IPC handler）；实际更新走前端 version.json 比对 + 后端 /api/update/* |
| **11** | Mac 端 `trigger-win-build.py` 全自动一条龙反复翻车：端口冲突、WinRM 残留进程叠加、HTTP 小文件传输间歇 0 字节、解压 Move 目录把 A11 搞乱 | WinRM 跨机调用脆弱 + A11 上进程无法被 Mac 端 pkill 干净 | **放弃全自动 trigger，改用 A11 本地 `git pull` + 跑 `build-win-ci.ps1`**（模式 A，见第 3 节）。脚本本身可靠，传输链路不可靠 |
| **12** | PowerShell `2>NUL` 被当成设备重定向报错；`??` 运算符在 Windows PowerShell 5.1 不支持；emoji(`✅`)在 GBK 下损坏成 `??` 导致脚本语法错误 | Windows PowerShell 5.1 与 PS 7 差异 + GBK 编码 | 脚本一律：文件用 UTF-8 BOM；stderr 用 `2>$null`（PowerShell）或 `2>NUL`（cmd 内）；不用 emoji，用 `[OK]`/`[X]` 文本；外部命令用 `cmd /c` 包裹 |

---

## 7. 快速自检清单（构建前 30 秒）

- [ ] Windows 机 IP 可达（WinRM 5985 探活）
- [ ] `package.json` 里 `extraResources` 只出现 1 次（grep 计数 = 1）
- [ ] `vermes-backend.spec` 存在且 `hiddenimports` 含 `sqlite_vec` + Windows 平台 `pywin32`
- [ ] `frontend/package-lock.json` 存在（npm ci 需要）
- [ ] 当前 git 已 commit 所有修复（Mac 端是真相源）
- [ ] `version.txt` / `electron/version.txt` 版本号 = 目标版本

---

## 8. 文件索引

| 文件 | 作用 |
|------|------|
| `docs/WINDOWS_BUILD.md` | 本文件（标准流程 + 教训） |
| `scripts/build-win-ci.ps1` | Windows 机上跑的主构建脚本（含全部坑防护） |
| `scripts/trigger-win-build.py` | Mac 上触发：推源码→构建→回传 exe |
| `scripts/build-electron-win.sh` | 旧版 Mac 侧 electron-builder 包装（仅参考，勿用于 Windows 后端） |
| `vermes-backend.spec` | PyInstaller 后端打包配置 |
| `package.json` (`build.extraResources`) | 后端作为 extraResources 注入安装包 |
| `version.json` | 发布元数据 + 三平台 sha256 |
