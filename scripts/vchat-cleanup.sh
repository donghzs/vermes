#!/bin/bash
# vchat 全面下线 + 清理
# 在 vbit 服务器上以 root 或 sudo 执行

echo "=== 1. 停服务 ==="
kill $(lsof -ti:9221) 2>/dev/null || echo "无运行中服务"

echo "=== 2. 删 nginx /wechat 路由 ==="
sudo sed -i '/location \/wechat {/,/}/d' /etc/nginx/sites-enabled/vbit.top.conf
sudo nginx -t && sudo systemctl reload nginx

echo "=== 3. 删 vchat 数据 ==="
sudo rm -rf /opt/vchat /var/log/vchat /opt/vbit/data/users

echo "=== 4. 清理旧进程残留 ==="
pkill -f 'wechat_agent_service' 2>/dev/null || true
pkill -f 'uvicorn.*9221' 2>/dev/null || true

echo "=== 完成 ==="
echo "公众号配置也去 mp.weixin.qq.com 关掉服务器配置"
