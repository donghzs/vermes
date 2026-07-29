# T7 发版 Checklist — Vermes 2.3.7（Mac + Win 真机端到端自测）

> 本文件是发版前的**硬卡点**。T7 验证的是"桌面更新真能用"——这块 CI 物理上验证不了（CI 只能守代码契约，验不了真机安装+重启换包），必须人工跑。
> 流程：先构建 Mac DMG → 本机测 → 过则发 Mac；顺势构建 Win → 测 → 过则发 Win。

## 一、构建产物自检（CI 可跑，已固化在 build.sh 末尾）
- [ ] `verify-build.sh` 全绿（web_dist / harness / scholarforge / gateway mixin / splash.html in asar / 版本号一致）
- [ ] `sqlite_vec` 运行时检查通过（vec0 扩展可加载）
- [ ] DMG 体积合理（arm64 ~260-270MB，非异常膨胀）

## 二、Mac 真机自测（你本机，覆盖安装到已装 2.3.6）
1. **启动与数据**
   - [ ] 双击 DMG → 拖入 Applications → 覆盖安装旧版不报错
   - [ ] 首次启动 splash 进度条显示正常（非黑屏、非卡测试模式）
   - [ ] 历史会话列表可见（web 会话排前面，不被大量空 telegram 会话淹没——已修）
   - [ ] 聊天记录 / 图片库未丢失（IndexedDB 分区门控已生效，非每次清图）

2. **核心聊天链路（任务触达门）**
   - [ ] 发消息 → Agent 回复触达正常（SSE 流式）
   - [ ] 工具调用 / 思考过程正常展示
   - [ ] 微信登录弹窗正常（不走系统浏览器跳走）

3. **桌面更新链路（本轮回填修复的核心）**
   - [ ] 启动后检查更新：桌面端走 **web 分支**（`fetch('https://vbit.top/vermes/version.json')`），不再卡死在 electron 死分支
   - [ ] 若有新版本：下载 → apply → 重启后版本号变更（原子替换 .app）
   - [ ] 日志确认：无 `update:check` 孤儿 IPC 报错（preload 已删 6 个孤儿 API）

4. **全渠道出入口**
   - [ ] 给某渠道会话（TG/飞书等）代发消息 → 回复经原渠道回手机（send-from-desktop 桥）
   - [ ] web 会话回写 state.db 正常

## 三、Win 真机自测（A11 出包，你远程测）
- [ ] A11 上 `git pull` + 跑 `build-win-ci.ps1` 产出 exe
- [ ] 安装 exe → 覆盖旧版不报错
- [ ] 启动 / 聊天 / 微信登录 / 更新检查 同 Mac 四项
- [ ] 中文环境无 UnicodeEncodeError（GBK 修复已固化）

## 四、发布动作（测试全过才做）
- [ ] 上传 DMG / exe 到 vbit.top/vermes/downloads/
- [ ] 更新 version.json（download_url / sha256 / size）
- [ ] 首页 / 产品页下载卡片版本号同步

## 阻断规则
任一"核心聊天链路"或"桌面更新链路"项失败 → **不发布**，回修复。

---
生成时间：2026-07-29（T7 配套发版卡点，对应 commit 00e17b22e 之后首个发布候选 2.3.7）
