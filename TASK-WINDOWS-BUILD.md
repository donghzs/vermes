# Windows 版构建任务

## 版本: v1.1.4

## 任务
在 A11 (192.168.1.6, Windows 10, administrator/REDACTED_PASSWORD) 上构建 Vermes Windows 版安装包。

## 步骤
1. 将源码打包传输到 A11
2. 在 A11 上安装 Python 3.11+ 和依赖
3. 先构建前端: `cd frontend && npm install && npm run build`
4. 将 dist/ 复制到 hermes_cli/web_dist/
5. 运行 `build-windows.bat` 构建 .exe
6. 构建产物: `Vermes-v1.1.4-windows-x64.zip`
7. 将构建产物传回 MacBook 或上传到服务器

## 注意事项
- PyInstaller 不能交叉编译，必须在 Windows 上构建
- A11 SSH 22 端口可能未开启，需要先检查
- 如 SSH 不通，需要用户在 A11 上手动操作
- 构建完成后通知上传到 vbit.top: scp 到 REDACTED_USER@REDACTED_SERVER_IP:/var/www/html/vermes/downloads/

## 项目路径
- 源码: ~/Projects/vermes/
- 构建脚本: build-windows.bat 或 vermes-onefile.spec
- 前端: ~/Projects/vermes/frontend/
- 版本号: hermes_cli/__init__.py 中 __version__ = "1.1.4"

## 已完成 (macOS)
- DMG 55MB + ZIP 72MB 已上传到 https://vbit.top/vermes/downloads/
- 产品页下载链接已指向 v1.1.4
- version.json 已更新

## Windows 版上传目标
- 服务器: REDACTED_USER@REDACTED_SERVER_IP, 密码 REDACTED_PASSWORD
- 路径: /var/www/html/vermes/downloads/Vermes-v1.1.4-windows-x64.zip
- 产品页: /var/www/html/vermes/index.html (Windows 下载链接需更新)
