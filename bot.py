import os
import re
import logging
import asyncio
from typing import Optional, List, Dict

import aiosqlite
from cachetools import TTLCache
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    InputMediaDocument,
    InputMediaAudio,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError

logging.basicConfig(
    format="%(asctime)s - [%(levelname)s] - %(name)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
DB_PATH = os.getenv("DB_PATH", "bot_database.db")

AD_KEYWORDS = [
    r"usdt", r"博彩", r"兼职", r"代开发票", r"外汇", r"裸聊",
    r"telegram.*channel", r"t\.me\/"
]
AD_REGEX = re.compile("|".join(AD_KEYWORDS), re.IGNORECASE)

SESSION_MAP = TTLCache(maxsize=50000, ttl=48 * 3600)
MEDIA_GROUP_CACHE: Dict[str, List[Update]] = {}
MEDIA_LOCK = asyncio.Lock()
REPLY_TARGET: Dict[int, int] = {}


class Database:
    @staticmethod
    async def init():
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("PRAGMA journal_mode = WAL;")
            await db.execute("PRAGMA synchronous = NORMAL;")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    is_blocked INTEGER DEFAULT 0,
                    is_verified INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()

    @staticmethod
    async def get_user(user_id: int):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT is_blocked, is_verified FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {"blocked": bool(row[0]), "verified": bool(row[1])}
                return {"blocked": False, "verified": False}

    @staticmethod
    async def upsert_user(user_id: int, username: str, full_name: str, is_verified: Optional[bool] = None):
        async with aiosqlite.connect(DB_PATH) as db:
            if is_verified is None:
                await db.execute("""
                    INSERT INTO users (user_id, username, full_name)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = excluded.username,
                        full_name = excluded.full_name,
                        updated_at = CURRENT_TIMESTAMP
                """, (user_id, username, full_name))
            else:
                await db.execute("""
                    INSERT INTO users (user_id, username, full_name, is_verified)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username = excluded.username,
                        full_name = excluded.full_name,
                        is_verified = excluded.is_verified,
                        updated_at = CURRENT_TIMESTAMP
                """, (user_id, username, full_name, 1 if is_verified else 0))
            await db.commit()

    @staticmethod
    async def set_blocked(user_id: int, blocked: bool):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET is_blocked = ? WHERE user_id = ?",
                (1 if blocked else 0, user_id)
            )
            await db.commit()


def check_is_ad(text: Optional[str]) -> bool:
    if not text:
        return False
    return bool(AD_REGEX.search(text))


def build_header(user) -> str:
    return (
        f"📩 <a href=\"tg://user?id={user.id}\">{user.full_name}</a>"
        f" (<code>{user.id}</code>)：\n\n"
    )


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id == ADMIN_ID:
        await update.message.reply_text("👑 管理员您好！点击【回复】按钮后直接发消息即可。")
        return

    u_info = await Database.get_user(user.id)
    if u_info["blocked"]:
        return

    await Database.upsert_user(user.id, user.username or "", user.full_name)
    if not u_info["verified"]:
        kb = [[InlineKeyboardButton("✅ 点击完成验证", callback_data=f"verify_{user.id}")]]
        await update.message.reply_text(
            f"你好，{user.first_name}！\n请点击下方按钮完成验证：",
            reply_markup=InlineKeyboardMarkup(kb)
        )
    else:
        await update.message.reply_text("你好！直接发消息即可，我们会尽快回复。")


async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    target_user_id = int(query.data.split("_")[1])
    if query.from_user.id != target_user_id:
        await query.answer("这不是给你的验证按钮！", show_alert=True)
        return
    await query.answer("验证通过！")
    await Database.upsert_user(
        query.from_user.id,
        query.from_user.username or "",
        query.from_user.full_name,
        is_verified=True
    )
    await query.edit_message_text("🎉 验证成功！现在可以直接发送消息了。")


async def reply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("无权限", show_alert=True)
        return

    target_user_id = int(query.data.split("_")[1])
    REPLY_TARGET[ADMIN_ID] = target_user_id
    # 只弹一个小提示，不发消息
    await query.answer(f"已锁定用户 {target_user_id}，直接发消息即可")


async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    msg = update.effective_message

    if user.id == ADMIN_ID:
        return

    u_info = await Database.get_user(user.id)
    if u_info["blocked"]:
        return

    if not u_info["verified"]:
        kb = [[InlineKeyboardButton("✅ 点击完成验证", callback_data=f"verify_{user.id}")]]
        await msg.reply_text("请先完成验证：", reply_markup=InlineKeyboardMarkup(kb))
        return

    content_text = msg.text or msg.caption or ""
    if check_is_ad(content_text):
        await msg.reply_text("您的消息包含敏感词汇，未能发送。")
        return

    if msg.media_group_id:
        async with MEDIA_LOCK:
            if msg.media_group_id not in MEDIA_GROUP_CACHE:
                MEDIA_GROUP_CACHE[msg.media_group_id] = []
                asyncio.create_task(
                    dispatch_media_group_to_admin(msg.media_group_id, user.id, context)
                )
            MEDIA_GROUP_CACHE[msg.media_group_id].append(update)
        return

    try:
        header = build_header(user)
        reply_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 回复", callback_data=f"reply_{user.id}")]
        ])

        if msg.text:
            fwd_msg = await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=header + msg.text_html,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_kb
            )
        elif msg.sticker:
            fwd_msg = await msg.forward(chat_id=ADMIN_ID)
            # 贴纸无法加按钮，单独发一条带按钮的
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=header.strip(),
                parse_mode=ParseMode.HTML,
                reply_markup=reply_kb
            )
        else:
            original_caption = msg.caption_html or ""
            new_caption = header + original_caption
            fwd_msg = await context.bot.copy_message(
                chat_id=ADMIN_ID,
                from_chat_id=msg.chat_id,
                message_id=msg.message_id,
                caption=new_caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_kb
            )

        SESSION_MAP[f"admin_{fwd_msg.message_id}"] = (user.id, msg.message_id)
        logger.info(f"用户 {user.id} 消息已转发给管理员")

    except TelegramError as e:
        logger.error(f"转发失败: {e}")


async def dispatch_media_group_to_admin(mg_id: str, user_id: int, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(0.8)
    async with MEDIA_LOCK:
        updates = MEDIA_GROUP_CACHE.pop(mg_id, [])

    if not updates:
        return

    first_msg = updates[0].effective_message
    user = updates[0].effective_user
    header = build_header(user)

    media_batch = []
    for idx, u in enumerate(updates):
        m = u.effective_message
        caption = (header + (m.caption_html or "")) if idx == 0 else (m.caption_html if m.caption else None)
        parse = ParseMode.HTML
        if m.photo:
            media_batch.append(InputMediaPhoto(media=m.photo[-1].file_id, caption=caption, parse_mode=parse))
        elif m.video:
            media_batch.append(InputMediaVideo(media=m.video.file_id, caption=caption, parse_mode=parse))
        elif m.document:
            media_batch.append(InputMediaDocument(media=m.document.file_id, caption=caption, parse_mode=parse))
        elif m.audio:
            media_batch.append(InputMediaAudio(media=m.audio.file_id, caption=caption, parse_mode=parse))

    try:
        sent_msgs = await context.bot.send_media_group(chat_id=ADMIN_ID, media=media_batch)
        SESSION_MAP[f"admin_{sent_msgs[0].message_id}"] = (user.id, first_msg.message_id)

        reply_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("💬 回复", callback_data=f"reply_{user.id}")]
        ])
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"⬆️ 来自 <code>{user.id}</code> 的相册",
            parse_mode=ParseMode.HTML,
            reply_markup=reply_kb
        )
    except TelegramError as e:
        logger.error(f"相册转发失败: {e}")


async def handle_admin_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if update.effective_user.id != ADMIN_ID:
        return

    # 方式一：长按回复
    if msg.reply_to_message:
        target_info = SESSION_MAP.get(f"admin_{msg.reply_to_message.message_id}")
        if target_info:
            await send_to_user(context, msg, target_info[0], target_info[1])
            return

    # 方式二：点击按钮锁定后直接发
    target_user_id = REPLY_TARGET.get(ADMIN_ID)
    if target_user_id:
        await send_to_user(context, msg, target_user_id, None)
        return


async def send_to_user(context, admin_msg, target_user_id: int, reply_to_msg_id: Optional[int]):
    try:
        if reply_to_msg_id:
            try:
                await context.bot.copy_message(
                    chat_id=target_user_id,
                    from_chat_id=ADMIN_ID,
                    message_id=admin_msg.message_id,
                    reply_to_message_id=reply_to_msg_id
                )
            except TelegramError:
                await context.bot.copy_message(
                    chat_id=target_user_id,
                    from_chat_id=ADMIN_ID,
                    message_id=admin_msg.message_id
                )
        else:
            await context.bot.copy_message(
                chat_id=target_user_id,
                from_chat_id=ADMIN_ID,
                message_id=admin_msg.message_id
            )
        logger.info(f"✅ 回复已送达用户 {target_user_id}")
    except TelegramError as e:
        logger.error(f"发送失败: {e}")
        await admin_msg.reply_text(f"❌ 发送失败：{e.message}")


async def block_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    msg = update.effective_message
    target_user_id = None

    if msg.reply_to_message:
        info = SESSION_MAP.get(f"admin_{msg.reply_to_message.message_id}")
        if info:
            target_user_id = info[0]
    if not target_user_id and context.args:
        try:
            target_user_id = int(context.args[0])
        except ValueError:
            pass
    if not target_user_id:
        target_user_id = REPLY_TARGET.get(ADMIN_ID)

    if not target_user_id:
        await msg.reply_text("用法：/block <用户ID> 或回复用户消息")
        return

    await Database.set_blocked(target_user_id, blocked=True)
    await msg.reply_text(f"🚫 用户 <code>{target_user_id}</code> 已拉黑。", parse_mode=ParseMode.HTML)


async def unblock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.effective_message.reply_text("用法：/unblock <用户ID>")
        return
    try:
        uid = int(context.args[0])
        await Database.set_blocked(uid, blocked=False)
        await update.effective_message.reply_text(f"✅ 用户 <code>{uid}</code> 已解封。", parse_mode=ParseMode.HTML)
    except ValueError:
        await update.effective_message.reply_text("请输入合法的用户ID。")


async def on_startup(app):
    await Database.init()


def main():
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("请先设置 BOT_TOKEN")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(on_startup).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("block", block_cmd))
    app.add_handler(CommandHandler("unblock", unblock_cmd))
    app.add_handler(CallbackQueryHandler(verify_callback, pattern=r"^verify_\d+"))
    app.add_handler(CallbackQueryHandler(reply_callback, pattern=r"^reply_\d+"))

    app.add_handler(MessageHandler(
        filters.Chat(ADMIN_ID) & ~filters.COMMAND, handle_admin_message
    ))
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & ~filters.Chat(ADMIN_ID) & ~filters.COMMAND, handle_user_message
    ))

    logger.info("Bot 已启动")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
