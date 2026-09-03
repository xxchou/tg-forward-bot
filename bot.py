import asyncio
import logging
import os
import re
import html
from typing import Optional

from dotenv import load_dotenv

# 加载 .env 环境变量
load_dotenv()

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery
)
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
import aiosqlite
from cachetools import TTLCache

# ================= 配置与初始化 =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")

if not BOT_TOKEN or not ADMIN_ID_RAW:
    raise SystemExit("错误：未检测到 BOT_TOKEN 或 ADMIN_ID，请检查 .env 文件！")

ADMIN_ID = int(ADMIN_ID_RAW)
DB_PATH = "data/cards.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

# 防刷点击内存缓存：每位用户 2 秒内只能点一次翻页/交互按键
click_cache = TTLCache(maxsize=10000, ttl=2.0)

PAGE_SIZE = 5

# ================= 数据库异步驱动 (WAL 模式) =================
async def init_db():
    """初始化数据库及表结构，开启高并发 WAL 模式"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_number TEXT NOT NULL,
                name TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("CREATE INDEX IF NOT EXISTS idx_cards_number ON cards(card_number);")
        await db.commit()
    logger.info("数据库初始化完成 (WAL 模式已就绪)")

async def db_add_cards(cards: list[tuple[str, str]]) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT INTO cards (card_number, name) VALUES (?, ?)",
            cards
        )
        await db.commit()
        return len(cards)

async def db_search_count(keyword: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM cards WHERE card_number LIKE ? OR name LIKE ?",
            (f"%{keyword}%", f"%{keyword}%")
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def db_search_paged(keyword: str, page: int, page_size: int = PAGE_SIZE) -> list[tuple]:
    offset = (page - 1) * page_size
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT card_number, name FROM cards WHERE card_number LIKE ? OR name LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (f"%{keyword}%", f"%{keyword}%", page_size, offset)
        ) as cursor:
            return await cursor.fetchall()

# ================= 辅助函数 =================
def sanitize_input(text: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)
    return cleaned.strip()

def build_pagination_keyboard(keyword: str, current_page: int, total_pages: int) -> Optional[InlineKeyboardMarkup]:
    if total_pages <= 1:
        return None
    buttons = []
    if current_page > 1:
        buttons.append(InlineKeyboardButton(text="⬅️ 上一页", callback_data=f"page:{keyword}:{current_page - 1}"))
    buttons.append(InlineKeyboardButton(text=f"📄 {current_page}/{total_pages}", callback_data="noop"))
    if current_page < total_pages:
        buttons.append(InlineKeyboardButton(text="下一页 ➡️", callback_data=f"page:{keyword}:{current_page + 1}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])

# ================= 业务路由处理 =================
@dp.message(CommandStart())
async def cmd_start(message: Message):
    welcome_text = (
        "👋 <b>欢迎使用卡密/数据查询机器人！</b>\n\n"
        "🔍 <b>查询方式：</b> 直接向我发送关键词（卡号或名称）即可。\n"
    )
    if message.from_user.id == ADMIN_ID:
        welcome_text += (
            "\n⚙️ <b>管理员指令：</b>\n"
            "• 批量导入卡密：<code>/add 卡号1----卡名1 卡号2 卡名2</code>\n"
            "  <i>(支持换行、空格、制表符、四连短横线等分隔符)</i>"
        )
    await message.answer(welcome_text)

@dp.message(Command("add"))
async def cmd_add_cards(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    content = message.text[len("/add"):].strip()
    if not content:
        await message.answer("⚠️ 请在 <code>/add</code> 后附带需要导入的内容！\n格式示例：<code>卡号----名称</code> 或 <code>卡号 名称</code>")
        return

    lines = content.splitlines()
    cards_to_insert = []

    for line in lines:
        line = sanitize_input(line)
        if not line:
            continue
        parts = re.split(r"----+|\t+|\s{2,}|\s+", line)
        parts = [p.strip() for p in parts if p.strip()]

        if len(parts) >= 2:
            card_num = parts[0]
            name = " ".join(parts[1:])
            cards_to_insert.append((card_num, name))
        elif len(parts) == 1:
            cards_to_insert.append((parts[0], "未命名"))

    if not cards_to_insert:
        await message.answer("⚠️ 未识别到有效数据，请检查格式。")
        return

    added_count = await db_add_cards(cards_to_insert)
    await message.answer(f"✅ 成功录入 <b>{added_count}</b> 条数据！")

@dp.message(F.text)
async def handle_search(message: Message):
    keyword = sanitize_input(message.text)
    if not keyword or keyword.startswith("/"):
        return

    total_count = await db_search_count(keyword)
    if total_count == 0:
        safe_kw = html.escape(keyword)
        await message.answer(f"🔍 未查询到包含 <code>{safe_kw}</code> 的相关记录。")
        return

    total_pages = (total_count + PAGE_SIZE - 1) // PAGE_SIZE
    records = await db_search_paged(keyword, page=1)

    response = [f"🔎 查询：<code>{html.escape(keyword)}</code> (共 {total_count} 条)"]
    response.append("━━━━━━━━━━━━━━━━━━")
    for num, name in records:
        response.append(f"💳 卡号: <code>{html.escape(num)}</code>\n🏷️ 名称: {html.escape(name)}\n")

    keyboard = build_pagination_keyboard(keyword, current_page=1, total_pages=total_pages)
    await message.answer("\n".join(response), reply_markup=keyboard)

@dp.callback_query(F.data.startswith("page:"))
async def handle_pagination(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id in click_cache:
        await callback.answer("⏳ 点击太快了，请慢一点~", show_alert=False)
        return
    click_cache[user_id] = True

    _, keyword, target_page_str = callback.data.split(":")
    page = int(target_page_str)

    total_count = await db_search_count(keyword)
    total_pages = (total_count + PAGE_SIZE - 1) // PAGE_SIZE

    if page < 1 or page > total_pages:
        await callback.answer("页面不存在")
        return

    records = await db_search_paged(keyword, page=page)
    response = [f"🔎 查询：<code>{html.escape(keyword)}</code> (共 {total_count} 条)"]
    response.append("━━━━━━━━━━━━━━━━━━")
    for num, name in records:
        response.append(f"💳 卡号: <code>{html.escape(num)}</code>\n🏷️ 名称: {html.escape(name)}\n")

    keyboard = build_pagination_keyboard(keyword, current_page=page, total_pages=total_pages)

    try:
        await callback.message.edit_text("\n".join(response), reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer()

@dp.callback_query(F.data == "noop")
async def handle_noop(callback: CallbackQuery):
    await callback.answer()

# ================= 程序入口 =================
async def main():
    await init_db()
    logger.info("Bot 服务已就绪，开始 Polling 轮询...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot 服务已安全停止。")
