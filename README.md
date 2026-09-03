# 🤖 Telegram 智能客服与消息转接机器人

基于 `python-telegram-bot (v20+)` 打造的高性能客服转发机器人，专为个人或团队对外联络设计。

## ✨ 特性

- 💬 **全消息转发**：支持文本、相册合并发送（Media Group）、语音、圆视频、文件、表情等。
- ⚡ **无感交互**：
  - 管理员直接**引用回复**即可给指定用户发送。
  - **极速双向撤回**：管理员引用消息回复 `.`、`del` 或 `d`，即可一键将管理员端和用户端的消息同步删除（支持多图相册一键全删）。
  - **锁定对话**：点击按钮锁定某位用户，后续发送无需一直引用回复。
- 🛡️ **安全与风控**：
  - **数学题人机验证**：防恶意刷屏。
  - **动态骗子黑名单联动**：云端同步反诈数据，遇黑名单用户带醒目标签。
  - **关键词过滤**：自动拦截敏感广告与垃圾引流并自动封禁。
- ✏️ **消息同步修改**：用户或管理修改已发送的文本消息，对端同步展示更新。

---

## 🚀 部署指引

### 1. 准备环境与安装依赖
```bash
git clone https://github.com/你的用户名/你的仓库名.git
cd 你的仓库名
pip install -r requirements.txt
2. 配置环境变量
复制模板配置文件：
<BASH>
cp config.example.env .env
根据实际情况设置环境变量：

BOT_TOKEN: 从 @BotFather 获取的机器人 Token。
ADMIN_CHAT_ID: 你的 Telegram 数字 ID（可通过 @userinfobot 获取）。
3. 运行服务
<BASH>
# 直接前台运行测试
BOT_TOKEN="你的Token" ADMIN_CHAT_ID=你的ID python bot.py
# 或使用 PM2 / Systemd 守护进程常驻后台
📖 管理员指令
/start - 查看管理面板与快捷帮助
/unselect - 解除当前锁定的用户会话
/ban <ID> - 手动封禁指定用户
/unban <ID> - 解除用户封禁
/stats - 查看当前机器人运行与统计数据
