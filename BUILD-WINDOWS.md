# Vermes Windows 打包说明

## 前置要求
- Windows 10/11
- Python 3.11+ 已安装
- Rust（可选，如果需要 Tauri 客户端）

## 打包步骤

### 方式一：源码打包（推荐）

1. **下载源码**
   ```
   git clone https://github.com/your-repo/vermes.git
   cd vermes
   ```

2. **创建虚拟环境**
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   pip install pyinstaller
   ```

3. **构建前端**
   ```
   cd frontend
   npm install
   npm run build
   cp -r dist\* ..\hermes_cli\web_dist\
   cd ..
   ```

4. **执行打包**
   ```
   .venv\Scripts\python -m PyInstaller vermes-gui.spec --noconfirm
   ```

5. **产物位置**
   - `dist\Vermes.exe`（单文件可执行）
   - 或 `dist\Vermes\`（目录形式）

### 方式二：直接下载预编译包
- macOS 用户：下载 `Vermes-1.0.0-macos-arm64.zip`
- Windows 用户：源码打包（暂无预编译包）

## 安全说明
- 打包脚本会自动移除个人密钥（`~/.vermes/.env`）
- 发布包不包含任何 API Key
- 用户首次启动需自行配置 Provider

## 验证打包产物
打包完成后验证：
```
# 检查是否包含密钥
grep -r "sk-[A-Za-z0-9]\{10,\}" dist\Vermes\ 2>nul
# 应返回空（无匹配）
```

## 已知问题
- Windows 打包产物体积约 150MB
- 需要管理员权限运行首次配置