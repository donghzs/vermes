#!/usr/bin/env python3
"""
Vermes Backend (Headless) — Electron 后端专用入口。
无 pywebview / 无 GUI 依赖，仅启动 FastAPI + uvicorn。
用法: backend_main [--port 9119]
"""
import sys
import os

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from hermes_cli.web_server import app


def main():
    port = 9119
    for i, arg in enumerate(sys.argv):
        if arg == "--port" and i + 1 < len(sys.argv):
            try:
                port = int(sys.argv[i + 1])
            except ValueError:
                pass

    print(f"[Vermes Backend] 启动 FastAPI, port={port}")
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        log_level="info",
        lifespan="off",
    )


if __name__ == "__main__":
    main()
