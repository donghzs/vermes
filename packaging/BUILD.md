# Vermes 打包指南

## 前置要求

- Python 3.11 或 3.12
- pip

## macOS 打包

```bash
# 首次打包（干净构建）
./build-macos.sh --clean

# 后续打包
./build-macos.sh

# 输出
dist/Vermes.app
```

## Windows 打包

```cmd
# 首次打包（干净构建）
build-windows.bat --clean

# 后续打包
build-windows.bat

# 输出
dist\vermes\vermes.exe
```

## 分发

### macOS
```bash
cd dist
zip -r Vermes-1.0.0-macos.zip Vermes.app
```

### Windows
```cmd
cd dist
# 使用 7-Zip 或右键 → 发送到 → 压缩(zipped)文件夹
# 创建 Vermes-1.0.0-windows.zip
```

## 注意事项

1. **首次打包耗时**：PyInstaller 需要分析所有依赖，首次打包可能需要 5-10 分钟
2. **杀毒软件**：Windows 上可能触发杀毒软件误报，需要在打包机上添加白名单
3. **签名**：生产环境需要对 .app 和 .exe 进行代码签名（避免"无法验证开发者"警告）
4. **体积**：打包后约 150-300MB（包含 Python 运行时 + 所有依赖）

## 打包后测试

### macOS
```bash
open dist/Vermes.app
# 首次运行需要在"系统偏好设置 → 安全性与隐私"中允许
```

### Windows
```cmd
dist\vermes\vermes.exe
# 首次运行可能需要"更多信息 → 仍要运行"
```

## 常见问题

### PyInstaller 找不到模块
检查 `vermes.spec` 中的 `hiddenimports` 列表，添加缺失的模块名。

### 打包后运行报错
```bash
# macOS: 查看控制台日志
open -a Console

# Windows: 在 CMD 中运行查看输出
dist\vermes\vermes.exe
```

### 体积过大
1. 检查是否有不必要的依赖被打包
2. 使用 UPX 压缩（已默认启用）
3. 排除测试/开发依赖（已在 spec 中配置）
