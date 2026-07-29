# Vermes Android App — buildozer.spec
# 放到 ~/vermes/buildozer.spec 后运行: buildozer android debug

[app]

# 应用信息
title = Vermes
package.name = vermes
package.domain = cn.vermes
source.dir = .
source.include_exts = py,png,jpg,jpeg,gif,svg,html,css,js,json,yaml,yml,txt,md,xml,ico,icns,env,example

# 版本
version = 2.0.4
version.regex = __version__ = ["'](.*)["']
version.filename = vermes_cli/__init__.py

# 最低 SDK 版本
android.api = 34
android.minapi = 26
android.sdk = 34
android.ndk = 27.0.12077973

# 权限
android.permissions = INTERNET, ACCESS_NETWORK_STATE, ACCESS_WIFI_STATE, VIBRATE, RECORD_AUDIO, CAMERA, READ_EXTERNAL_STORAGE, WRITE_EXTERNAL_STORAGE, POST_NOTIFICATIONS

# 图标
android.icon = packaging/vermes_icon_256.png
android.presplash_color = #0a0a0a

# Python 相关
requirements = python3, openai, httpx, pydantic, pyyaml, rich, jinja2, croniter, psutil, certifi, python-dotenv, prompt-toolkit

# 排除不需要的包
android.exclude_glibc = 1
android.add_src = .

# 操作系统 API 级别
android.gradle_dep_command = 1
android.accept_sdk_license = 1

# 调试
android.debug = 1

# 架构
android.archs = arm64-v8a

# 启动脚本
presplash.filename = packaging/vermes-256.png
icon.filename = packaging/vermes_icon_256.png

# 隐藏控制台
android.wakelock = 1

[buildozer]

# 日志级别
log_level = 2

# 构建目录
build_dir = ./buildozer-build

# 二进制缓存目录
bin_dir = ./bin
