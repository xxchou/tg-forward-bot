# 🤖 Telegram 智能客服与消息转接机器人

```markdown

基于 `python-telegram-bot (v20+)` 打造的高性能客服转发机器人，专为个人或团队对外联络设计。

## ✨ 特性

- 💬 **全消息类型支持**：完美支持纯文本、多图合并相册（Media Group）、语音、圆视频、各种格式文件、表情等。
- ⚡ **无感交互**：
  - **直接引用回复**：管理员只需在 Telegram 里对某条消息点击“引用回复”，对端用户即可无缝收到消息。
  - **极速双向撤回**：管理员引用消息回复 `.`、`del`、`d` 或 `撤`，机器人会自动同时从双方聊天记录中彻底删除该消息（支持多图相册一键全删）。
  - **锁定单人对话**：点击消息下方的「锁定此人对话」，后续无需频繁引用即可连续向该用户发消息。
- 🛡️ **安全与风控**：
  - **动态数学算术验证**：未验证的新用户需先点击回答算术题，防止脚本机器人爆破刷屏。
  - **实时云端骗子库联动**：自动同步黑名单库，遇到恶意号进线会显示醒目的 `⚠️ [骗子名单]` 标签。
  - **广告违规过滤**：命中常见赌博、刷单、违规引流关键词，自动拦截并一键拉黑。
- ✏️ **修改同步**：任意一方编辑修改已经发送的文本消息，对端展示内容会自动同步变更。

---

## 🚀 部署指引

### 1. 准备环境与拉取代码
```bash
git clone https://github.com/xxchou/tg-forward-bot.git
cd tg-forward-bot
pip install -r requirements.txt
```

### 2. 获取所需凭证
- **BOT_TOKEN**: 通过 Telegram 官方 [@BotFather](https://t.me/BotFather) 申请机器人获取。
- **ADMIN_CHAT_ID**: 你的 Telegram 纯数字账号 ID（可通过联系 [@userinfobot](https://t.me/userinfobot) 查看）。

### 3. 运行机器人

#### 方式 A：临时调试（直接传入环境变量）
```bash
BOT_TOKEN="你的Token" ADMIN_CHAT_ID=你的数字ID python bot.py
```

#### 方式 B：使用 PM2 常驻后台（推荐）
```bash
# 安装 PM2（如未安装）
npm install pm2 -g

# 启动并持久化
BOT_TOKEN="你的Token" ADMIN_CHAT_ID=你的数字ID pm2 start bot.py --name "tg-forward-bot" --interpreter python3
pm2 save
pm2 startup
```

---

## 📖 管理员指令

| 指令 | 说明 |
| :--- | :--- |
| `/start` | 查看快捷控制面板与指令说明 |
| `/unselect` | 解除当前锁定的用户会话 |
| `/ban <ID>` | 封禁指定的用户数字 ID |
| `/unban <ID>` | 解除封禁指定的用户数字 ID |
| `/stats` | 查看当前系统的用户总数与黑名单概况 |
