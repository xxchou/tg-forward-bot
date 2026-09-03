# 🤖 Telegram 智能客服与消息转接机器人

# Telegram 卡密 / 数据管理机器人

基于 Python 3.10+、aiogram v3 与 aiosqlite 构建的高性能 Telegram 检索机器人。

## 🌟 特性
- 异步高并发架构，SQLite 默认启用 WAL 模式，防止数据库死锁
- 极速检索，支持卡号、名称双向模糊查询
- 内置分页展示，配备内存防抖（防高频连击刷屏）
- 支持复杂分隔符批量导入 (`----`、空格、Tab、多空格等)
- 严格遵循 PEP 668 环境隔离与 `.env` 敏感配置分离

## 🚀 部署指南

### 1. 克隆项目并创建虚拟环境
```bash
git clone <你的仓库地址>
cd tg-bot
python3 -m venv venv
```

### 2. 安装依赖
```bash
./venv/bin/pip install -r requirements.txt
```

### 3. 配置环境变量
复制模板文件：
```bash
cp config.example.env .env
```
编辑 `.env` 填入真实凭证：
```env
BOT_TOKEN=你的BotToken
ADMIN_ID=你的Telegram数字ID
```

### 4. 运行
- **前台测试运行：**
  ```bash
  ./venv/bin/python bot.py
  ```

- **后台持久化运行 (推荐 systemd)：**
  创建服务文件 `/etc/systemd/system/tgbot.service`：
  ```ini
  [Unit]
  Description=Telegram Bot Service
  After=network.target

  [Service]
  Type=simple
  User=root
  WorkingDirectory=/root/tg-bot
  ExecStart=/root/tg-bot/venv/bin/python /root/tg-bot/bot.py
  Restart=always
  RestartSec=5

  [Install]
  WantedBy=multi-user.target
  ```
  加载并启动：
  ```bash
  systemctl daemon-reload
  systemctl enable --now tgbot
  ```
