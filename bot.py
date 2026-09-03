import asyncio
import html
import logging
import os
import random
import sqlite3
import time
import urllib.request
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================= 配置区域 (优先从环境变量读取) =================
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
DB_FILE = os.getenv("DB_FILE", "bot.db")
FRAUD_DB_URL = os.getenv("FRAUD_DB_URL", "https://raw.githubusercontent.com/wuyangdaily/nfd/refs/heads/main/data/fraud.db")

SPAM_KEYWORDS = [
    "代开", "发票", "上分", "下分", "棋牌", "博彩", "包赢", 
    "兼职", "刷单", "日赚", "公积金提取", "USDT承兑", "跑分", "洗钱"
]
# ==============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ----------------- 数据库层 -----------------
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        first_name TEXT,
        username TEXT,
        is_banned INTEGER DEFAULT 0,
        verified_until INTEGER DEFAULT 0,
        first_seen TEXT
    )''')
    c.execute("PRAGMA table_info(users)")
    cols = [info[1] for info in c.fetchall()]
    if "verified_until" not in cols:
        c.execute("ALTER TABLE users ADD COLUMN verified_until INTEGER DEFAULT 0")

    c.execute('''CREATE TABLE IF NOT EXISTS msg_map (
        user_id INTEGER,
        user_msg_id INTEGER,
        admin_msg_id INTEGER,
        media_group_id TEXT,
        created_at INTEGER
    )''')
    c.execute("PRAGMA table_info(msg_map)")
    mcols = [info[1] for info in c.fetchall()]
    if "media_group_id" not in mcols:
        c.execute("ALTER TABLE msg_map ADD COLUMN media_group_id TEXT DEFAULT ''")

    c.execute('CREATE INDEX IF NOT EXISTS idx_u ON msg_map(user_id, user_msg_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_a ON msg_map(admin_msg_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_mg ON msg_map(media_group_id)')
    
    c.execute('''CREATE TABLE IF NOT EXISTS kv_state (
        key TEXT PRIMARY KEY,
        val TEXT
    )''')
    conn.commit()
    conn.close()

def get_target():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT val FROM kv_state WHERE key="target"')
    row = c.fetchone()
    conn.close()
    return int(row[0]) if row and row[0] else None

def set_target(uid):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    val = str(uid) if uid else ""
    c.execute('INSERT OR REPLACE INTO kv_state (key, val) VALUES ("target", ?)', (val,))
    conn.commit()
    conn.close()

def save_user(u):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT user_id FROM users WHERE user_id=?', (u.id,))
    if not c.fetchone():
        c.execute('INSERT INTO users VALUES (?, ?, ?, 0, 0, ?)', 
                  (u.id, u.first_name, u.username, 0, 0, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    else:
        c.execute('UPDATE users SET first_name=?, username=? WHERE user_id=?', 
                  (u.first_name, u.username, u.id))
    conn.commit()
    conn.close()

def is_user_banned(uid):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT is_banned FROM users WHERE user_id=?', (uid,))
    row = c.fetchone()
    conn.close()
    return bool(row and row[0] == 1)

def set_ban_status(uid, status: int):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE users SET is_banned=? WHERE user_id=?', (status, uid))
    conn.commit()
    conn.close()

def is_verified(uid):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT verified_until FROM users WHERE user_id=?', (uid,))
    row = c.fetchone()
    conn.close()
    return bool(row and row[0] > int(time.time()))

def mark_verified(uid):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('UPDATE users SET verified_until=? WHERE user_id=?', (int(time.time()) + 86400, uid))
    conn.commit()
    conn.close()

def save_msg_pair(user_id, user_msg_id, admin_msg_id, media_group_id=""):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('INSERT INTO msg_map VALUES (?, ?, ?, ?, ?)', 
              (user_id, user_msg_id, admin_msg_id, str(media_group_id or ""), int(time.time())))
    c.execute('DELETE FROM msg_map WHERE created_at < ?', (int(time.time()) - 30 * 86400,))
    conn.commit()
    conn.close()

def query_by_admin_msg(admin_msg_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT user_id, user_msg_id FROM msg_map WHERE admin_msg_id=?', (admin_msg_id,))
    row = c.fetchone()
    conn.close()
    return (row[0], row[1]) if row else (None, None)

def query_by_user_msg(user_id, user_msg_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT admin_msg_id FROM msg_map WHERE user_id=? AND user_msg_id=?', (user_id, user_msg_id))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def get_related_pairs_for_del(admin_msg_id):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT user_id, media_group_id FROM msg_map WHERE admin_msg_id=?', (admin_msg_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return []
    
    uid, mg_id = row[0], row[1]
    if mg_id:
        c.execute('SELECT user_id, user_msg_id, admin_msg_id FROM msg_map WHERE media_group_id=?', (mg_id,))
    else:
        c.execute('SELECT user_id, user_msg_id, admin_msg_id FROM msg_map WHERE admin_msg_id=?', (admin_msg_id,))
    
    pairs = c.fetchall()
    if mg_id:
        c.execute('DELETE FROM msg_map WHERE media_group_id=?', (mg_id,))
    else:
        c.execute('DELETE FROM msg_map WHERE admin_msg_id=?', (admin_msg_id,))
    conn.commit()
    conn.close()
    return pairs

# ----------------- 异步防卡顿骗子库 -----------------
FRAUD_CACHE = {"data": set(), "last_update": 0}

def fetch_fraud_sync():
    req = urllib.request.Request(FRAUD_DB_URL, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=5) as resp:
        content = resp.read().decode('utf-8', errors='ignore')
        return set([line.strip() for line in content.splitlines() if line.strip()])

async def async_refresh_fraud_db():
    now = time.time()
    if now - FRAUD_CACHE["last_update"] > 3600:
        try:
            data = await asyncio.to_thread(fetch_fraud_sync)
            FRAUD_CACHE["data"] = data
            FRAUD_CACHE["last_update"] = now
        except Exception:
            pass

def check_fraud_db(uid: int) -> bool:
    return str(uid) in FRAUD_CACHE["data"]

# ----------------- UI 与 格式化 -----------------
def make_user_markup(user_id: int):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔒 锁定此人对话", callback_data=f"sel_{user_id}")
    ]])

def user_title_html(user):
    name_display = f"@{user.username}" if user.username else (user.first_name or "用户")
    fraud_warning = "⚠️ <b>[骗子名单]</b> " if check_fraud_db(user.id) else ""
    return f"{fraud_warning}<a href=\"tg://user?id={user.id}\"><b>{html.escape(name_display)}</b></a> <code>({user.id})</code>"

# ----------------- 防刷验证 -----------------
VERIFY_SESSIONS = {}
VERIFY_LOCKS = {}

def create_math_challenge():
    ops = ['+', '-', '×']
    op = random.choice(ops)
    if op == '+':
        a, b = random.randint(1, 30), random.randint(1, 30)
        ans = a + b
    elif op == '-':
        a, b = random.randint(10, 50), random.randint(1, 20)
        if a < b: a, b = b, a
        ans = a - b
    else:
        a, b = random.randint(2, 9), random.randint(2, 9)
        ans = a * b
    expr = f"{a} {op} {b}"
    options = {ans}
    while len(options) < 4:
        cand = ans + random.randint(-5, 5)
        if cand > 0 and cand != ans:
            options.add(cand)
    opt_list = list(options)
    random.shuffle(opt_list)
    return expr, ans, opt_list

async def send_verify_challenge(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    now = time.time()
    if chat_id in VERIFY_LOCKS and VERIFY_LOCKS[chat_id] > now:
        rem_min = int((VERIFY_LOCKS[chat_id] - now) // 60)
        await context.bot.send_message(chat_id=chat_id, text=f"操作频繁，请在 {rem_min} 分钟后再试。")
        return

    expr, correct, opt_list = create_math_challenge()
    row_btns = [InlineKeyboardButton(str(opt), callback_data=f"vf_{opt}") for opt in opt_list]
    keyboard = InlineKeyboardMarkup([row_btns])

    txt = f"🛡 <b>请点击答案验证：</b>\n👉 <b>{expr} = ?</b>"
    if chat_id in VERIFY_SESSIONS and VERIFY_SESSIONS[chat_id].get("msg_id"):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=VERIFY_SESSIONS[chat_id]["msg_id"])
        except Exception:
            pass

    sent = await context.bot.send_message(chat_id=chat_id, text=txt, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    attempts = VERIFY_SESSIONS.get(chat_id, {}).get("attempts", 0)
    VERIFY_SESSIONS[chat_id] = {"ans": correct, "expires": now + 60, "attempts": attempts, "msg_id": sent.message_id}

async def handle_verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    now = time.time()

    if not query.data.startswith("vf_"):
        return

    sel_val = int(query.data.split("_")[1])
    sess = VERIFY_SESSIONS.get(uid)
    if not sess or now > sess["expires"]:
        await query.answer("验证已超时", show_alert=True)
        await send_verify_challenge(uid, context)
        return

    if sel_val == sess["ans"]:
        mark_verified(uid)
        VERIFY_SESSIONS.pop(uid, None)
        await query.answer("验证成功！", show_alert=False)
        try:
            await context.bot.delete_message(chat_id=uid, message_id=query.message.message_id)
        except Exception:
            pass
        await context.bot.send_message(chat_id=uid, text="已通过验证，请直接发送内容。")
    else:
        sess["attempts"] += 1
        rem = 3 - sess["attempts"]
        if rem <= 0:
            VERIFY_LOCKS[uid] = now + 3600
            VERIFY_SESSIONS.pop(uid, None)
            await query.answer("次数超限，限制 1 小时", show_alert=True)
            try:
                await context.bot.edit_message_text(chat_id=uid, message_id=query.message.message_id, text="回答错误次数过多，请稍后再试。")
            except Exception:
                pass
        else:
            await query.answer(f"回答错误，还剩 {rem} 次", show_alert=True)
            await send_verify_challenge(uid, context)

# ----------------- 媒体组合并 -----------------
MEDIA_BUFFERS = {}

async def deliver_user_media_group(media_group_id: str, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(1.2)
    data = MEDIA_BUFFERS.pop(media_group_id, None)
    if not data or not data["items"]:
        return

    user = data["user"]
    user_header = user_title_html(user)
    orig_caption = data["items"][0].caption or ""
    
    if orig_caption:
        combined_caption = f"{user_header}:\n{orig_caption}"[:1000]
    else:
        combined_caption = user_header

    data["items"][0].caption = combined_caption
    data["items"][0].parse_mode = ParseMode.HTML

    sent_messages = await context.bot.send_media_group(chat_id=ADMIN_CHAT_ID, media=data["items"])
    for m, orig_id in zip(sent_messages, data["orig_ids"]):
        save_msg_pair(user.id, orig_id, m.message_id, media_group_id)

async def deliver_admin_media_group(media_group_id: str, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(1.2)
    data = MEDIA_BUFFERS.pop(media_group_id, None)
    if not data or not data["items"]:
        return

    target_id = data["target_id"]
    sent_messages = await context.bot.send_media_group(chat_id=target_id, media=data["items"])
    for m, orig_id in zip(sent_messages, data["orig_ids"]):
        save_msg_pair(target_id, m.message_id, orig_id, media_group_id)

# ----------------- 用户消息流 -----------------
async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message
    if not msg or user.id == ADMIN_CHAT_ID:
        return

    save_user(user)
    if is_user_banned(user.id):
        return

    if not is_verified(user.id):
        await send_verify_challenge(user.id, context)
        return

    asyncio.create_task(async_refresh_fraud_db())

    text_content = msg.text or msg.caption or ""
    for kw in SPAM_KEYWORDS:
        if kw in text_content:
            set_ban_status(user.id, 1)
            await msg.reply_text("违规内容，已被屏蔽。")
            return

    # 多图相册
    if msg.media_group_id:
        mg_id = msg.media_group_id
        if mg_id not in MEDIA_BUFFERS:
            MEDIA_BUFFERS[mg_id] = {"user": user, "items": [], "orig_ids": []}
            context.application.create_task(deliver_user_media_group(mg_id, context))

        if msg.photo:
            MEDIA_BUFFERS[mg_id]["items"].append(InputMediaPhoto(media=msg.photo[-1].file_id, caption=msg.caption))
        elif msg.video:
            MEDIA_BUFFERS[mg_id]["items"].append(InputMediaVideo(media=msg.video.file_id, caption=msg.caption))
        MEDIA_BUFFERS[mg_id]["orig_ids"].append(msg.message_id)
        return

    # 纯文字
    if msg.text:
        title = user_title_html(user)
        clean_text = f"{title}:\n{html.escape(msg.text)}"
        fwd = await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=clean_text,
            parse_mode=ParseMode.HTML,
            reply_markup=make_user_markup(user.id)
        )
        save_msg_pair(user.id, msg.message_id, fwd.message_id)
        return

    # 单张照片/视频
    if msg.photo or msg.video:
        title = user_title_html(user)
        cap = f"{title}\n{html.escape(msg.caption)}"[:1000] if msg.caption else title
        if msg.photo:
            fwd = await context.bot.send_photo(
                chat_id=ADMIN_CHAT_ID,
                photo=msg.photo[-1].file_id,
                caption=cap,
                parse_mode=ParseMode.HTML,
                reply_markup=make_user_markup(user.id)
            )
        else:
            fwd = await context.bot.send_video(
                chat_id=ADMIN_CHAT_ID,
                video=msg.video.file_id,
                caption=cap,
                parse_mode=ParseMode.HTML,
                reply_markup=make_user_markup(user.id)
            )
        save_msg_pair(user.id, msg.message_id, fwd.message_id)
        return

    # 其它类型
    fwd = await context.bot.copy_message(chat_id=ADMIN_CHAT_ID, from_chat_id=user.id, message_id=msg.message_id)
    title = user_title_html(user)
    tag = f"📎 {title}"
    await context.bot.send_message(
        chat_id=ADMIN_CHAT_ID,
        text=tag,
        parse_mode=ParseMode.HTML,
        reply_markup=make_user_markup(user.id)
    )
    if fwd:
        save_msg_pair(user.id, msg.message_id, fwd.message_id)

# ----------------- 管理员回复与极速撤回 -----------------
FAST_DEL_SYMBOLS = {"del", "d", "x", ".", "。", "垃圾桶", "删", "撤", "🗑"}

async def handle_admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or msg.chat_id != ADMIN_CHAT_ID:
        return

    # 批量撤回
    if msg.reply_to_message and msg.text and msg.text.strip().lower() in FAST_DEL_SYMBOLS:
        target_admin_msg_id = msg.reply_to_message.message_id
        pairs = get_related_pairs_for_del(target_admin_msg_id)
        
        try:
            await msg.delete()
        except Exception:
            pass

        if pairs:
            deleted_count = 0
            for uid, u_mid, a_mid in pairs:
                try:
                    await context.bot.delete_message(chat_id=uid, message_id=u_mid)
                    await context.bot.delete_message(chat_id=ADMIN_CHAT_ID, message_id=a_mid)
                    deleted_count += 1
                except Exception:
                    pass
            status_msg = await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"🗑 双方已撤回 ({deleted_count}条)")
            await asyncio.sleep(1.5)
            await status_msg.delete()
            return
        else:
            notice = await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text="未检索到对应消息")
            await asyncio.sleep(1.5)
            await notice.delete()
            return

    target_user_id = None
    reply_to_user_msg_id = None

    if msg.reply_to_message:
        uid, u_mid = query_by_admin_msg(msg.reply_to_message.message_id)
        if uid:
            target_user_id = uid
            reply_to_user_msg_id = u_mid

    if not target_user_id:
        target_user_id = get_target()

    if not target_user_id:
        await msg.reply_text("💡 请引用消息直接回复，或点击「锁定此人对话」。")
        return

    # 管理端多图相册
    if msg.media_group_id:
        mg_id = msg.media_group_id
        if mg_id not in MEDIA_BUFFERS:
            MEDIA_BUFFERS[mg_id] = {"target_id": target_user_id, "items": [], "orig_ids": []}
            context.application.create_task(deliver_admin_media_group(mg_id, context))

        if msg.photo:
            MEDIA_BUFFERS[mg_id]["items"].append(InputMediaPhoto(media=msg.photo[-1].file_id, caption=msg.caption))
        elif msg.video:
            MEDIA_BUFFERS[mg_id]["items"].append(InputMediaVideo(media=msg.video.file_id, caption=msg.caption))
        MEDIA_BUFFERS[mg_id]["orig_ids"].append(msg.message_id)
        return

    try:
        sent = await context.bot.copy_message(
            chat_id=target_user_id,
            from_chat_id=ADMIN_CHAT_ID,
            message_id=msg.message_id,
            reply_to_message_id=reply_to_user_msg_id
        )
        save_msg_pair(target_user_id, sent.message_id, msg.message_id)
    except Exception as e:
        await msg.reply_text(f"❌ 发送失败: {e}")

# ----------------- 消息编辑 -----------------
async def handle_edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    edit = update.edited_message
    if not edit or not edit.text:
        return

    if edit.chat_id != ADMIN_CHAT_ID:
        admin_mid = query_by_user_msg(edit.from_user.id, edit.message_id)
        if admin_mid:
            try:
                title = user_title_html(edit.from_user)
                clean_text = f"{title} (已修改):\n{html.escape(edit.text)}"
                await context.bot.edit_message_text(
                    chat_id=ADMIN_CHAT_ID,
                    message_id=admin_mid,
                    text=clean_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=make_user_markup(edit.from_user.id)
                )
            except Exception:
                pass
    else:
        uid, u_mid = query_by_admin_msg(edit.message_id)
        if uid and u_mid:
            try:
                await context.bot.edit_message_text(chat_id=uid, message_id=u_mid, text=edit.text)
            except Exception:
                pass

# ----------------- 指令与交互 -----------------
async def btn_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data.startswith("vf_"):
        await handle_verify_callback(update, context)
        return

    if query.from_user.id != ADMIN_CHAT_ID:
        await query.answer("无权限", show_alert=True)
        return

    if query.data.startswith("sel_"):
        uid = int(query.data.split("_")[1])
        set_target(uid)
        await query.answer(f"已锁定：{uid}")

async def cmd_unselect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id == ADMIN_CHAT_ID:
        set_target(None)
        await update.effective_message.reply_text("✅ 已退出锁定。")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id == ADMIN_CHAT_ID:
        await update.effective_message.reply_text(
            "<b>控制面板</b>\n\n"
            "• 回复对方: 引用任意消息直接回复（支持文字/语音/相册/圆视频/文件等）\n"
            "• 极速撤回: 引用任意消息回复 <code>.</code> 或 <code>del</code>（相册自动全撤）\n"
            "• 解除锁定: <code>/unselect</code>\n"
            "• 封禁: <code>/ban &lt;ID&gt;</code> | 解封: <code>/unban &lt;ID&gt;</code>\n"
            "• 统计: <code>/stats</code>",
            parse_mode=ParseMode.HTML
        )
    else:
        save_user(user)
        if not is_verified(user.id):
            await send_verify_challenge(user.id, context)
        else:
            await update.effective_message.reply_text("你好，请直接留言，看到后会回复。")

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID or not context.args:
        return
    try:
        uid = int(context.args[0])
        set_ban_status(uid, 1)
        await update.effective_message.reply_text(f"用户 <code>{uid}</code> 已拉黑。", parse_mode=ParseMode.HTML)
    except ValueError:
        await update.effective_message.reply_text("格式错误，例: /ban 123456")

async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID or not context.args:
        return
    try:
        uid = int(context.args[0])
        set_ban_status(uid, 0)
        await update.effective_message.reply_text(f"用户 <code>{uid}</code> 已解除拉黑。", parse_mode=ParseMode.HTML)
    except ValueError:
        await update.effective_message.reply_text("格式错误，例: /unban 123456")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    total_u = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM users WHERE is_banned=1')
    banned_u = c.fetchone()[0]
    conn.close()
    
    tgt = get_target()
    tgt_info = f"<code>{tgt}</code>" if tgt else "未锁定"
    await update.effective_message.reply_text(
        f"<b>数据概览</b>\n\n"
        f"• 累计用户: {total_u}\n"
        f"• 黑名单: {banned_u}\n"
        f"• 骗子库容: {len(FRAUD_CACHE['data'])}\n"
        f"• 当前锁定: {tgt_info}",
        parse_mode=ParseMode.HTML
    )

def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE" or ADMIN_CHAT_ID == 0:
        print("错误: 请先配置 BOT_TOKEN 和 ADMIN_CHAT_ID 环境变量！")
        return

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("unselect", cmd_unselect))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("stats", cmd_stats))
    
    app.add_handler(CallbackQueryHandler(btn_callback))
    app.add_handler(MessageHandler(filters.UpdateType.EDITED_MESSAGE, handle_edited_message))
    app.add_handler(MessageHandler(filters.Chat(ADMIN_CHAT_ID) & ~filters.COMMAND, handle_admin_reply))
    app.add_handler(MessageHandler(~filters.Chat(ADMIN_CHAT_ID) & ~filters.COMMAND, handle_user_message))

    app.run_polling()

if __name__ == "__main__":
    main()
