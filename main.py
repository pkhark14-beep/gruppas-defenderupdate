import os
import sqlite3
import logging
import asyncio
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from fastapi import FastAPI
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS players (
            chat_id INTEGER,
            user_id INTEGER,
            username TEXT,
            balance INTEGER DEFAULT 100,
            PRIMARY KEY (chat_id, user_id)
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

class RuleDeleteModel(BaseModel):
    rule_id: int
    chat_id: int

# --- API ЭНДПОИНТЫ ---
@app.get("/")
@app.head("/")
def read_root():
    return {"status": "Gruppas OS Backend is online!", "database": "connected"}

@app.get("/api/chat/verify-admin/{chat_id}")
async def verify_chat_admin(chat_id: int, user_id: int):
    try:
        bot_info = await bot.get_me()
        member = await bot.get_chat_member(chat_id, bot_info.id)
        is_bot_admin = member.status in ["creator", "administrator"]
        return {
            "is_admin": is_bot_admin,
            "status": "ready" if is_bot_admin else "waiting"
        }
    except Exception as e:
        return {"is_admin": False, "status": "waiting", "error": str(e)}

@app.get("/api/chat/{chat_id}")
async def get_chat_config(chat_id: int, user_id: int):
    conn = sqlite3.connect("gruppas.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    chat = cur.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
    if not chat:
        cur.execute("""
            INSERT OR IGNORE INTO chats (chat_id, title, owner_id, currency_name, ai_enabled, is_configured)
            VALUES (?, ?, ?, 'G-Coins', 1, 0)
        """, (chat_id, f"Чат #{chat_id}", user_id))
        conn.commit()
        chat = cur.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
    
    is_admin = False
    if chat and chat['owner_id'] == user_id:
        is_admin = True
    else:
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            if member.status in ["creator", "administrator"]:
                is_admin = True
        except Exception:
            if chat and chat['owner_id'] == user_id:
                is_admin = True

    player = cur.execute("SELECT * FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)).fetchone()
    if not player:
        cur.execute("INSERT OR IGNORE INTO players (chat_id, user_id, balance) VALUES (?, ?, 100)", (chat_id, user_id))
        conn.commit()
        player = cur.execute("SELECT * FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)).fetchone()

    rules = cur.execute("SELECT * FROM rules WHERE chat_id = ?", (chat_id,)).fetchall()
    conn.close()
    
    return {
        "chat": dict(chat) if chat else {},
        "is_admin": is_admin,
        "player": dict(player) if player else {},
        "rules": [dict(r) for r in rules]
    }

@app.post("/api/chat/setup")
async def save_chat_setup(data: ChatSetupModel):
    conn = sqlite3.connect("gruppas.db")
    cur = conn.cursor()
    
    chat = cur.execute("SELECT owner_id, title, is_configured FROM chats WHERE chat_id = ?", (data.chat_id,)).fetchone()
    was_configured = chat[2] if chat else 0
    
    if not chat:
        cur.execute("""
            INSERT INTO chats (chat_id, title, owner_id, currency_name, ai_enabled, is_configured)
            VALUES (?, ?, ?, ?, ?, 1)
        """, (data.chat_id, f"Чат #{data.chat_id}", data.user_id, data.currency_name, int(data.ai_enabled)))
        chat_title = f"Чат #{data.chat_id}"
    else:
        chat_title = chat[1] or f"Чат #{data.chat_id}"
        cur.execute("""
            UPDATE chats 
            SET currency_name = ?, ai_enabled = ?, is_configured = 1
            WHERE chat_id = ?
        """, (data.currency_name, int(data.ai_enabled), data.chat_id))
        
    conn.commit()
    conn.close()

    if not was_configured:
        try:
            ai_status = "🤖 Включен" if data.ai_enabled else "🛡 Выключен"
            await bot.send_message(
                data.chat_id,
                f"🎉 <b>Gruppas OS успешно настроена для чата «{chat_title}»!</b>\n\n"
                f"💰 Валюта: <b>{data.currency_name}</b>\n"
                f"⚖️ ИИ-модератор: {ai_status}\n\n"
                f"<i>Игроки могут открыть свой профиль командой /profile, а админы — через /start в ЛС.</i>",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Ошибка отправки в чат: {e}")

    return {"status": "ok"}

@app.post("/api/rules/add")
def add_chat_rule(data: RuleCreateModel):
    conn = sqlite3.connect("gruppas.db")
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO rules (chat_id, code, description, mute_minutes) VALUES (?, ?, ?, ?)",
        (data.chat_id, data.code, data.description, data.mute_minutes)
    )
    conn.commit()
    conn.close()
    return {"status": "rule_added"}

@app.post("/api/rules/delete")
def delete_chat_rule(data: RuleDeleteModel):
    conn = sqlite3.connect("gruppas.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM rules WHERE id = ? AND chat_id = ?", (data.rule_id, data.chat_id))
    conn.commit()
    conn.close()
    return {"status": "rule_deleted"}

# --- КОМАНДЫ TELEGRAM БОТА ---
@dp.message(Command("setup"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_group_setup(message: types.Message):
    conn = sqlite3.connect("gruppas.db")
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO chats (chat_id, title, owner_id) 
        VALUES (?, ?, ?)
    """, (message.chat.id, message.chat.title, message.from_user.id))
    conn.commit()
    conn.close()

    bot_info = await bot.get_me()
    setup_link = f"https://t.me/{bot_info.username}?start=setup_{message.chat.id}"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Верифицировать и настроить чат", url=setup_link)]
    ])
    await message.answer(
        f"🛠 <b>Инициализация Gruppas OS для «{message.chat.title}»</b>\n\n"
        f"Администратор: {message.from_user.mention_html()}\n"
        f"Нажмите кнопку ниже для проверки прав и запуска мастера:",
        parse_mode="HTML",
        reply_markup=kb
    )

@dp.message(Command("profile"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_group_profile(message: types.Message):
    app_url = f"{WEBAPP_URL}?chat_id={message.chat.id}&v={int(time.time())}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 Открыть приложение Gruppas OS", web_app=WebAppInfo(url=app_url))]
    ])
    await message.answer(
        f"👤 <b>Экосистема Gruppas OS</b>\n\n"
        f"Участник: {message.from_user.mention_html()}\n"
        f"Нажмите кнопку ниже, чтобы открыть приложение:",
        parse_mode="HTML",
        reply_markup=kb
    )

@dp.message(Command("start"), F.chat.type == "private")
async def cmd_start_private(message: types.Message):
    user_id = message.from_user.id
    conn = sqlite3.connect("gruppas.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    configured_chats = cur.execute("SELECT * FROM chats WHERE is_configured = 1").fetchall()
    
    accessible_chats = []
    for chat in configured_chats:
        chat_id = chat['chat_id']
        is_owner = (chat['owner_id'] == user_id)
        in_players = cur.execute("SELECT 1 FROM players WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)).fetchone()
        
        if is_owner or in_players:
            accessible_chats.append(chat)
        else:
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                if member.status not in ["left", "kicked"]:
                    cur.execute("INSERT OR IGNORE INTO players (chat_id, user_id, balance) VALUES (?, ?, 100)", (chat_id, user_id))
                    conn.commit()
                    accessible_chats.append(chat)
            except Exception:
                pass
                
    conn.close()
    
    if accessible_chats:
        kb_buttons = []
        for chat in accessible_chats:
            panel_url = f"{WEBAPP_URL}?chat_id={chat['chat_id']}&v={int(time.time())}"
            chat_title = chat["title"] or f"Чат #{chat['chat_id']}"
            kb_buttons.append([
                InlineKeyboardButton(text=f"📱 Открыть: {chat_title}", web_app=WebAppInfo(url=panel_url))
            ])
            
        kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
        await message.answer(
            f"🎛 <b>Gruppas OS — Центр управления</b>\n\n"
            f"Привет, <b>{message.from_user.full_name}</b>! Доступные вам чаты:",
            parse_mode="HTML",
            reply_markup=kb
        )
    else:
        await message.answer(
            "👋 Добро пожаловать в <b>Gruppas OS</b>!\n\n"
            "Вы пока не состоите ни в одном настроенном чате. Добавьте меня в групповой чат, выдайте права администратора и отправьте команду <b>/setup</b>.",
            parse_mode="HTML"
        )

@dp.message(CommandStart(deep_link=True))
async def cmd_start_deeplink(message: types.Message, command: CommandObject):
    args = command.args
    if args and args.startswith("setup_"):
        chat_id = args.replace("setup_", "")
        app_url = f"{WEBAPP_URL}?chat_id={chat_id}&v={int(time.time())}"
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚀 Запустить мастер настройки", web_app=WebAppInfo(url=app_url))]
        ])
        await message.answer(
            "Панель верификации и конфигурации чата готова. Нажмите кнопку ниже:",
            reply_markup=kb
        )
    else:
        await cmd_start_private(message)

async def start_telegram_bot():
    await dp.start_polling(bot)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(start_telegram_bot())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
