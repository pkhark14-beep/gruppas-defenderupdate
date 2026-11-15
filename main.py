import os
import sqlite3
import logging
import asyncio
import requests
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН_БОТА")
AI_API_KEY = os.getenv("AI_API_KEY", "ТВОЙ_API_КЛЮЧ_ИИ")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://pkhark14-beep.github.io/gruppas-defenderupdate/")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect("gruppas.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            owner_id INTEGER,
            currency_name TEXT DEFAULT 'G-Coins',
            ai_enabled INTEGER DEFAULT 1,
            is_configured INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            code TEXT,
            description TEXT,
            mute_minutes INTEGER DEFAULT 60
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- МОДЕЛИ ДАННЫХ ---
class ChatSetupModel(BaseModel):
    chat_id: int
    user_id: int
    currency_name: str
    ai_enabled: bool

class RuleCreateModel(BaseModel):
    chat_id: int
    user_id: int
    code: str
    description: str
    mute_minutes: int = 60

# --- API ЭНДПОИНТЫ ---
@app.get("/")
def read_root():
    return {"status": "Gruppas OS Backend is online!", "database": "connected"}

@app.get("/api/chat/{chat_id}")
def get_chat_config(chat_id: int, user_id: int):
    conn = sqlite3.connect("gruppas.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    chat = cur.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
    if not chat:
        conn.close()
        raise HTTPException(status_code=404, detail="Чат не найден. Пропишите /setup в группе.")
    
    rules = cur.execute("SELECT * FROM rules WHERE chat_id = ?", (chat_id,)).fetchall()
    conn.close()
    
    return {
        "chat": dict(chat),
        "is_admin": (chat["owner_id"] == user_id),
        "rules": [dict(r) for r in rules]
    }

@app.post("/api/chat/setup")
def save_chat_setup(data: ChatSetupModel):
    conn = sqlite3.connect("gruppas.db")
    cur = conn.cursor()
    chat = cur.execute("SELECT owner_id FROM chats WHERE chat_id = ?", (data.chat_id,)).fetchone()
    if not chat or chat[0] != data.user_id:
        conn.close()
        raise HTTPException(status_code=403, detail="Нет прав администратора")
    
    cur.execute("""
        UPDATE chats 
        SET currency_name = ?, ai_enabled = ?, is_configured = 1 
        WHERE chat_id = ?
    """, (data.currency_name, int(data.ai_enabled), data.chat_id))
    conn.commit()
    conn.close()
    return {"status": "ok"}

@app.post("/api/rules/add")
def add_chat_rule(data: RuleCreateModel):
    conn = sqlite3.connect("gruppas.db")
    cur = conn.cursor()
    chat = cur.execute("SELECT owner_id FROM chats WHERE chat_id = ?", (data.chat_id,)).fetchone()
    if not chat or chat[0] != data.user_id:
        conn.close()
        raise HTTPException(status_code=403, detail="Нет прав доступа")
    
    cur.execute(
        "INSERT INTO rules (chat_id, code, description, mute_minutes) VALUES (?, ?, ?, ?)",
        (data.chat_id, data.code, data.description, data.mute_minutes)
    )
    conn.commit()
    conn.close()
    return {"status": "rule_added"}

# --- КОМАНДЫ TELEGRAM БОТА ---
@dp.message(Command("setup"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_group_setup(message: types.Message):
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ["creator", "administrator"]:
        return await message.reply("Настраивать экосистему чата могут только администраторы!")

    conn = sqlite3.connect("gruppas.db")
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO chats (chat_id, title, owner_id) 
        VALUES (?, ?, ?)
    """, (message.chat.id, message.chat.title, message.from_user.id))
    conn.commit()
    conn.close()

    bot_info = await bot.get_me()
    setup_link = f"https://t.me/{bot_info.username}?start=setup_{message.chat.id}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Настроить чат в Mini App", url=setup_link)]
    ])
    await message.answer(
        f"🛠 <b>Инициализация Gruppas OS для «{message.chat.title}»</b>\n\n"
        f"Администратор: {message.from_user.mention_html()}\n"
        f"Нажмите кнопку ниже, чтобы запустить мастер настройки:",
        parse_mode="HTML",
        reply_markup=kb
    )

@dp.message(CommandStart(deep_link=True))
async def cmd_start_deeplink(message: types.Message, command: CommandObject):
    args = command.args
    if args and args.startswith("setup_"):
        chat_id = args.replace("setup_", "")
        app_url = f"{WEBAPP_URL}?chat_id={chat_id}"
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Открыть мастер настройки", web_app=WebAppInfo(url=app_url))]
        ])
        await message.answer(
            "Панель конфигурации чата готова. Нажмите кнопку ниже:",
            reply_markup=kb
        )
    else:
        await message.answer("Добро пожаловать в Gruppas OS! Добавьте бота в группу и напишите /setup.")

@dp.message(Command("start"))
async def cmd_start_regular(message: types.Message):
    await message.answer("Привет! Добавьте меня в групповой чат, выдайте права администратора и отправьте /setup.")

async def start_telegram_bot():
    await dp.start_polling(bot)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(start_telegram_bot())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
