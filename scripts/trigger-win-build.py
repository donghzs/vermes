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
        t.add(REPO_ROOT, arcname='vermes-electron', filter=filt)
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
    time.sleep(1)

    try:
        # 3a. 下载 + 解压到 Windows
        log("Windows 机下载 + 解压源码...")
        ps = f"""
        $ts = Get-Date -Format 'yyyyMMddHHmm'
        if (Test-Path '{args.root}') {{ Move-Item '{args.root}' '{args.root}.bak-$ts' -Force }}
        New-Item -ItemType Directory -Force -Path '{args.root}' | Out-Null
        curl.exe --noproxy * -s -o C:\\dl_src.tar.gz -w "DL: %{{http_code}} %{{size_download}}\\n" http://{args.mac_ip}:{args.port}/{os.path.basename(tar_path)}
        python -c @'
import tarfile, os
src=r'C:\\dl_src.tar.gz'; dst=r'{args.root}'
with tarfile.open(src,'r:gz') as t:
    for m in t.getmembers():
        if os.path.basename(m.name).startswith('._') or '/._' in m.name: continue
        t.extract(m, dst, set_attrs=False)
print('PY_EXTRACT_DONE')
'@
        """
        out, err = winrm_run(args.win_host, args.win_user, args.win_pass, ps, timeout=300)
        print(out.strip()[-800:])
        if 'PY_EXTRACT_DONE' not in out:
            log("❌ 解压失败"); log(err[-500:]); sys.exit(1)

        # 3b. npm install + 跑构建脚本（前台）
        log("npm install + 构建（前台阻塞）...")
        skip = " -SkipFrontend" if args.skip_frontend else ""
        ps2 = f"""
        Set-Location '{args.root}'
        $env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:http_proxy=''; $env:https_proxy=''
        cmd /c "set HTTP_PROXY=& set HTTPS_PROXY=& npm install --no-audit --no-fund 2>&1"
        Write-Host "NPM_DONE"
        $py = '{args.python}'
        $ps1 = '{args.root}\\scripts\\build-win-ci.ps1'
        $arg = "-Root '{args.root}' -Python '$py'{skip}"
        Invoke-Expression "& '$py' -Command \\"& {{ & '$ps1' {arg} }}\\""
        """
        # 直接 PowerShell 调 ps1 文件（必须 -ExecutionPolicy Bypass，否则 WinRM 下脚本被禁）
        ps2 = f"""
        Set-Location '{args.root}'
        $env:HTTP_PROXY=''; $env:HTTPS_PROXY=''; $env:http_proxy=''; $env:https_proxy=''
        cmd /c "set HTTP_PROXY=& set HTTPS_PROXY=& npm install --no-audit --no-fund 2>&1"
        Write-Host "NPM_DONE"
        $py = '{args.python}'
        $ps1 = '{args.root}\\scripts\\build-win-ci.ps1'
        cmd /c "powershell -ExecutionPolicy Bypass -NoProfile -File $ps1 -Root '{args.root}' -Python $py{skip} 2>&1"
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
        local_exe = '/tmp/winupload/Vermes-Setup-2.3.5.exe'
        # 先找 Windows 上的 exe 名
        ps3 = f"""
        $exe = Get-ChildItem '{args.root}\\dist-electron\\Vermes Setup*.exe' | Sort-Object LastWriteTime | Select-Object -First 1
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
        with open('/tmp/winupload/Vermes-Setup-2.3.5.exe', 'wb') as f:
            f.write(data)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')


if __name__ == '__main__':
    main()
