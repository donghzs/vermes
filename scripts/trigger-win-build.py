#!/usr/bin/env python3
"""
Vermes Windows 构建触发器（在 Mac 控制机上运行）

一条龙：
  1. 把当前仓库（git 工作区）打成 tar.gz（排除 node_modules/dist/__pycache__/AppleDouble）
  2. Mac 起 HTTP server 供 Windows 机拉取
  3. WinRM 执行：curl --noproxy 下载 → Python tarfile 解压 → npm install → build-win-ci.ps1
  4. 回传 exe 到 Mac /tmp/winupload/
  5. 打印 sha256 + size，提示更新 version.json

用法：
  python3 scripts/trigger-win-build.py \
      --win-host 192.168.1.7 \
      --win-user Administrator \
      --win-pass '<A11-admin-password>' \
      --python 'C:\\Users\\Administrator\\AppData\\Local\\Programs\\Python\\Python312\\python.exe'

所有机器相关参数都从命令行传入 —— 换机器只改 IP / 路径，脚本逻辑不变。
依赖：pip install pywinrm
"""

import argparse
import http.server
import os
import socketserver
import subprocess
import sys
import tarfile
import tempfile
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(msg):
    print(f"[trigger] {msg}", flush=True)


# ── 1. 打包源码 ──
def make_tar(dest_path: str):
    log("打包源码（排除 node_modules/dist/__pycache__/._*）...")
    exclude_dirs = {'.git', 'node_modules', 'dist', 'dist-electron', 'build',
                    '__pycache__', '.mypy_cache', '.pytest_cache', '.venv',
                    'UNKNOWN.egg-info', 'VERMES_agent.egg-info', 'vermes.egg-info',
                    'downloads', 'website', 'archive', '.github'}
    exclude_files = {'.DS_Store', 'Thumbs.db'}

    def filt(member: tarfile.TarInfo):
        name = member.name
        base = os.path.basename(name)
        if base.startswith('._'):
            return None
        parts = name.split('/')
        for p in parts:
            if p in exclude_dirs:
                return None
        if base in exclude_files:
            return None
        return member

    with tarfile.open(dest_path, 'w:gz') as t:
        # 逐文件添加，arcname 剥掉仓库根目录名，使 tar 内容直接在根
        # （解压到 C:\Projects\vermes-electron 后文件就在该目录下，无需再剥一层）
        for root, dirs, files in os.walk(REPO_ROOT):
            # 过滤排除目录
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for fn in files:
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, REPO_ROOT)
                if os.path.basename(rel).startswith('._'):
                    continue
                if os.path.basename(rel) in exclude_files:
                    continue
                t.add(full, arcname=rel, filter=filt)
    size = os.path.getsize(dest_path)
    log(f"tar 完成: {dest_path} ({size/1e6:.1f} MB)")
    return size


# ── 2. HTTP server 线程 ──
class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == '/' + os.path.basename(HTTP_FILE):
            with open(HTTP_FILE, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)


HTTP_FILE = None


def start_server(port: int):
    socketserver.TCPServer.allow_reuse_address = True
    for p in range(port, port + 20):
        try:
            httpd = socketserver.TCPServer(('0.0.0.0', p), _Handler)
            return httpd, p
        except OSError:
            continue
    raise RuntimeError(f"no free port in {port}..{port+19}")


def start_recv_server(port: int):
    socketserver.TCPServer.allow_reuse_address = True
    for p in range(port, port + 20):
        try:
            httpd = socketserver.TCPServer(('0.0.0.0', p), _RecvHandler)
            return httpd, p
        except OSError:
            continue
    raise RuntimeError(f"no free recv port in {port}..{port+19}")


# ── 3. WinRM 执行 ──
def winrm_run(host, user, password, ps_script: str, timeout=600):
    import winrm
    s = winrm.Session(f"http://{host}:5985/wsman",
                      auth=(user, password), transport='ntlm',
                      operation_timeout_sec=timeout,
                      read_timeout_sec=timeout + 30)
    r = s.run_ps(ps_script)
    out = r.std_out.decode('gbk', 'replace')
    err = r.std_err.decode('gbk', 'replace')
    return out, err


# ── 主流程 ──
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--win-host', required=True, help='Windows 构建机 IP')
    ap.add_argument('--win-user', default='Administrator')
    ap.add_argument('--win-pass', required=True)
    ap.add_argument('--python', default=r'C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe')
    ap.add_argument('--mac-ip', default='192.168.1.4', help='Mac 局域网 IP，供 Windows 拉取')
    ap.add_argument('--port', type=int, default=9050)
    ap.add_argument('--root', default=r'C:\Projects\vermes-electron')
    ap.add_argument('--skip-frontend', action='store_true')
    args = ap.parse_args()

    global HTTP_FILE
    tmp = tempfile.gettempdir()
    tar_path = os.path.join(tmp, 'vermes_src_build.tar.gz')
    HTTP_FILE = tar_path

    # 1. tar
    make_tar(tar_path)

    # 2. server
    httpd, actual_port = start_server(args.port)
    log(f"HTTP server on :{actual_port}, Mac IP = {args.mac_ip}")
    # 后台线程启动 HTTP 服务（否则 server 不处理请求，A11 下载会超时）
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(1)

    try:
        # 3a. 下载 + 解压到 Windows
        log("Windows 机下载 + 解压源码...")
        dl_port = actual_port  # A11 实际访问的端口（start_server 可能因占用偏移）
        # 3a-1. 准备目录 + 下载（独立调用，避免 curl 输出干扰后续 python 解压）
        ps_prepare = f"""
        $Root = Join-Path 'C:\Projects' 'vermes-electron'
        $ts = Get-Date -Format 'yyyyMMddHHmm'
        if (Test-Path $Root) {{ Move-Item $Root ($Root + '.bak-' + $ts) -Force }}
        New-Item -ItemType Directory -Force -Path $Root | Out-Null
        curl.exe --noproxy * -s -o C:\dl_src.tar.gz http://{args.mac_ip}:{dl_port}/{os.path.basename(tar_path)}
        """
        winrm_run(args.win_host, args.win_user, args.win_pass, ps_prepare, timeout=300)

        # 3a-2. 解压（独立调用，用 Join-Path + 环境变量避免 \v 被转义为 VT）
        ps_extract = '''
        $dst = Join-Path 'C:\Projects' 'vermes-electron'
        $env:VERMES_DST = $dst
        $env:VERMES_SRC = 'C:\dl_src.tar.gz'
        python -c "import tarfile,os; src=os.environ['VERMES_SRC']; dst=os.environ['VERMES_DST']; tf=tarfile.open(src,'r:gz'); [tf.extract(m,dst,set_attrs=False) for m in tf.getmembers() if not (os.path.basename(m.name).startswith('._') or '/._' in m.name)]; print('PY_EXTRACT_DONE')"
        '''
        out, err = winrm_run(args.win_host, args.win_user, args.win_pass, ps_extract, timeout=300)
        print(out.strip()[-800:])
        # 解压成功后单独验证 package.json 存在（不依赖 stdout 字符串，CLIXML 可能损坏）
        verify = winrm_run(args.win_host, args.win_user, args.win_pass,
                           "if (Test-Path (Join-Path (Join-Path 'C:\\Projects' 'vermes-electron') 'package.json')) { Write-Host 'VERIFY_OK' } else { Write-Host 'VERIFY_FAIL' }",
                           timeout=60)[0]
        if 'VERIFY_OK' not in verify:
            log("❌ 解压失败或文件缺失"); log(err[-500:]); sys.exit(1)

        # 3b. npm install + 跑构建脚本（前台）
        log("npm install + 构建（前台阻塞）...")
        skip = " -SkipFrontend" if args.skip_frontend else ""
        # 直接 PowerShell 调 ps1 文件（必须 -ExecutionPolicy Bypass，否则 WinRM 下脚本被禁）
        ps2 = f"""
        $Root = Join-Path 'C:\Projects' 'vermes-electron'
        Set-Location $Root
        $env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:http_proxy=''; $env:https_proxy=''
        cmd /c "set HTTP_PROXY=& set HTTPS_PROXY=& npm install --no-audit --no-fund 2>&1"
        Write-Host "NPM_DONE"
        $py = '{args.python}'
        $ps1 = Join-Path $Root 'scripts\\build-win-ci.ps1'
        & powershell -ExecutionPolicy Bypass -NoProfile -File $ps1 -Python $py{skip}
        """
        out, err = winrm_run(args.win_host, args.win_user, args.win_pass, ps2, timeout=900)
        print(out.strip()[-2000:])
        if 'BUILD COMPLETE' not in out and 'BUILD_SHA256=' not in out:
            log("❌ 构建可能失败，查看上方日志"); log(err[-500:]); sys.exit(1)

        # 提取 sha
        sha = None
        mb = None
        for line in out.splitlines():
            if line.startswith('BUILD_SHA256='):
                sha = line.split('=', 1)[1].strip()
            if line.startswith('BUILD_MB='):
                mb = line.split('=', 1)[1].strip()
        log(f"构建产物 SHA256={sha}  MB={mb}")

        # 4. 回传 exe 到 Mac
        log("回传 exe 到 Mac /tmp/winupload/ ...")
        os.makedirs('/tmp/winupload', exist_ok=True)
        local_exe = '/tmp/winupload/Vermes-Setup-2.4.1.exe'
        # 先找 Windows 上的 exe 名
        ps3 = f"""
        $exe = Get-ChildItem (Join-Path $Root 'dist-electron\\Vermes Setup*.exe') | Sort-Object LastWriteTime | Select-Object -First 1
        $bytes = [System.IO.File]::ReadAllBytes($exe.FullName)
        $req = [System.Net.HttpWebRequest]::Create('http://{args.mac_ip}:{args.port+10}/')
        $req.Method='POST'; $req.ContentType='application/octet-stream'; $req.ContentLength=$bytes.Length; $req.Proxy=$null
        $s=$req.GetRequestStream(); $s.Write($bytes,0,$bytes.Length); $s.Close()
        $resp=$req.GetResponse(); $sr=New-Object System.IO.StreamReader($resp.GetResponseStream()); Write-Host ('UPLOAD '+($sr.ReadToEnd())+' bytes='+$bytes.Length)
        """
        # 启动 Mac 接收服务（端口+10）
        recv, actual_recv_port = start_recv_server(args.port + 10)
        rt = threading.Thread(target=recv.serve_forever, daemon=True)
        rt.start()
        time.sleep(1)
        out, err = winrm_run(args.win_host, args.win_user, args.win_pass, ps3, timeout=300)
        print(out.strip()[-300:])
        recv.shutdown()
        if os.path.exists(local_exe):
            real_sha = subprocess.check_output(['shasum', '-a', '256', local_exe]).decode().split()[0]
            log(f"✅ 已回传: {local_exe}")
            log(f"   Mac 侧 sha256: {real_sha}")
            if sha and real_sha.upper() != sha.upper():
                log("⚠️  Windows 报告 sha 与 Mac 接收 sha 不一致，可能传输损坏")
            log("下一步：更新 version.json 的 windows sha256，然后 scp 到 vbit.top")
        else:
            log("❌ 回传失败")

    finally:
        httpd.shutdown()


class _RecvHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        data = self.rfile.read(length)
        with open('/tmp/winupload/Vermes-Setup-2.4.1.exe', 'wb') as f:
            f.write(data)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')


if __name__ == '__main__':
    main()
