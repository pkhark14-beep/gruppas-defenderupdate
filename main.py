import os
import sqlite3
import logging
import time
import json
import aiohttp
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo, ChatPermissions
from aiogram.fsm.storage.memory import MemoryStorage

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BOT_TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН_БОТА")
AI_API_KEY = os.getenv("AI_API_KEY", "ТВОЙ_API_КЛЮЧ_GROK")
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://pkhark14-beep.github.io/gruppas-defenderupdate/")
RENDER_URL = os.getenv("RENDER_URL", "https://gruppas-defenderupdate.onrender.com")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db():
    conn = sqlite3.connect("gruppas.db", timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                title TEXT,
                owner_id INTEGER,
                currency_name TEXT DEFAULT 'G-Coins',
                rules_text TEXT DEFAULT '',
                ai_enabled INTEGER DEFAULT 1,
                is_configured INTEGER DEFAULT 0,
                bot_is_admin INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_members (
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                full_name TEXT,
                role TEXT DEFAULT 'member',
                is_prem INTEGER DEFAULT 0,
                balance INTEGER DEFAULT 100,
                warns INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id)
            )
        """)
        conn.commit()

init_db()

def upsert_member_with_conn(conn: sqlite3.Connection, chat_id: int, user: types.User, role: str = None):
    if not user or user.is_bot:
        return
    cur = conn.cursor()
    existing = cur.execute(
        "SELECT role FROM chat_members WHERE chat_id = ? AND user_id = ?",
        (chat_id, user.id)
    ).fetchone()
    
    if existing:
        new_role = role if role else existing["role"]
        cur.execute("""
            UPDATE chat_members 
            SET username = ?, full_name = ?, role = ?
            WHERE chat_id = ? AND user_id = ?
        """, (user.username or "", user.full_name, new_role, chat_id, user.id))
    else:
        cur.execute("""
            INSERT INTO chat_members (chat_id, user_id, username, full_name, role, balance)
            VALUES (?, ?, ?, ?, ?, 100)
        """, (chat_id, user.id, user.username or "", user.full_name, role or "member"))

def upsert_member(chat_id: int, user: types.User, role: str = None):
    if not user or user.is_bot:
        return
    with get_db() as conn:
        upsert_member_with_conn(conn, chat_id, user, role)
        conn.commit()

async def check_message_with_grok(rules_text: str, user_name: str, is_prem: bool, message_text: str) -> dict:
    if not AI_API_KEY or AI_API_KEY == "ТВОЙ_API_КЛЮЧ_GROK":
        logging.warning("AI_API_KEY не установлен!")
        return {"violated": False}

    system_prompt = (
        "Ты — строгий и мгновенный ИИ-модератор Grok в системе Gruppas OS. "
        "Твоя задача — проверять сообщения участников по правилам этого чата.\n\n"
        f"ПРАВИЛА ЧАТА И НАКАЗАНИЯ:\n{rules_text}\n\n"
        "ИНСТРУКЦИЯ ДЛЯ АНАЛИЗА:\n"
        "1. Сопоставь текст участника с правилами выше.\n"
        "2. Если есть нарушение (мат, оскорбления, спам, запрещенные действия из правил):\n"
        "   - Извлеки из текста правил точное время наказания в минутах (например: '1 час' -> 60, '2 часа' -> 120, 'сутки' -> 1440, 'навсегда' -> 0).\n"
        "   - Если время в правилах не указано, ставь 60.\n"
        f"3. Статус пользователя: {'Gruppas Prem (учитывай привилегии, если прописаны в правилах)' if is_prem else 'Обычный участник'}.\n"
        "4. Верни СТРОГО валидный JSON без markdown-разметки:\n"
        '{"violated": true, "rule_code": "пункт правила", "reason": "краткая причина", "mute_minutes": число_минут}\n'
        "5. Если нарушений нет, верни СТРОГО:\n"
        '{"violated": false}'
    )

    payload = {
        "model": "grok-3-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Участник {user_name} написал: {message_text}"}
        ],
        "temperature": 0.1
    }

    headers = {
        "Authorization": f"Bearer {AI_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.x.ai/v1/chat/completions", json=payload, headers=headers, timeout=10) as resp:
                resp_text = await resp.text()
                if resp.status == 200:
                    data = json.loads(resp_text)
                    raw_reply = data["choices"][0]["message"]["content"].strip()
                    raw_reply = raw_reply.replace("```json", "").replace("```", "").strip()
                    logging.info(f"Ответ Grok: {raw_reply}")
                    return json.loads(raw_reply)
                else:
                    logging.error(f"Ошибка API Grok ({resp.status}): {resp_text}")
    except Exception as e:
        logging.error(f"Исключение при вызове Grok: {e}")

    return {"violated": False}

class ChatSetupModel(BaseModel):
    chat_id: int
    user_id: int
    currency_name: str
    ai_enabled: bool
    rules_text: str = ""

@app.get("/")
@app.head("/")
def read_root():
    return {"status": "Gruppas OS Backend + Grok AI is online!", "database": "connected"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        json_data = await request.json()
        update = types.Update(**json_data)
        await dp.feed_update(bot, update)
        return {"status": "ok"}
    except Exception as e:
        logging.error(f"Ошибка вебхука: {e}")
        return {"status": "error"}

@app.get("/api/chat/{chat_id}")
async def get_chat_config(chat_id: int, user_id: int):
    with get_db() as conn:
        cur = conn.cursor()
        chat = cur.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        member = cur.execute("SELECT * FROM chat_members WHERE chat_id = ? AND user_id = ?", (chat_id, user_id)).fetchone()
        
        is_admin = False
        if chat and chat["owner_id"] == user_id:
            is_admin = True
        elif member and member["role"] in ("creator", "administrator"):
            is_admin = True

        members_list = cur.execute("SELECT * FROM chat_members WHERE chat_id = ?", (chat_id,)).fetchall()
        
        return {
            "chat": dict(chat) if chat else {},
            "is_admin": is_admin,
            "player": dict(member) if member else {"balance": 100, "role": "member", "is_prem": 0},
            "members": [dict(m) for m in members_list],
            "rules": []
        }

@app.post("/api/chat/setup")
async def save_chat_setup(data: ChatSetupModel):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO chats (chat_id, owner_id, currency_name, ai_enabled, rules_text, is_configured)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(chat_id) DO UPDATE SET
                currency_name = excluded.currency_name,
                ai_enabled = excluded.ai_enabled,
                rules_text = excluded.rules_text,
                is_configured = 1
        """, (data.chat_id, data.user_id, data.currency_name, int(data.ai_enabled), data.rules_text))
        conn.commit()
    logging.info(f"Чат {data.chat_id} настроен!")
    return {"status": "ok"}

@dp.my_chat_member()
async def on_bot_status_change(event: types.ChatMemberUpdated):
    if event.chat.type not in ("group", "supergroup"):
        return
    new_status = event.new_chat_member.status
    is_admin = 1 if new_status in ("administrator", "creator") else 0
    
    with get_db() as conn:
        if new_status in ("left", "kicked"):
            conn.execute("DELETE FROM chats WHERE chat_id = ?", (event.chat.id,))
        else:
            conn.execute("""
                INSERT INTO chats (chat_id, title, owner_id, bot_is_admin)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    title = excluded.title,
                    bot_is_admin = excluded.bot_is_admin
            """, (event.chat.id, event.chat.title, event.from_user.id, is_admin))
        conn.commit()

@dp.chat_member()
async def on_user_join_or_update(event: types.ChatMemberUpdated):
    if event.chat.type not in ("group", "supergroup"):
        return
    user = event.new_chat_member.user
    status = event.new_chat_member.status
    if status in ("member", "administrator", "creator"):
        role = status if status in ("administrator", "creator") else "member"
        upsert_member(event.chat.id, user, role=role)

@dp.message(Command("start"), F.chat.type == "private")
async def cmd_start_private(message: types.Message):
    user_id = message.from_user.id
    with get_db() as conn:
        chats = conn.execute("""
            SELECT DISTINCT c.*, cm.role FROM chats c
            LEFT JOIN chat_members cm ON c.chat_id = cm.chat_id AND cm.user_id = ?
            WHERE c.owner_id = ? OR cm.user_id = ?
        """, (user_id, user_id, user_id)).fetchall()

    if not chats:
        await message.answer(
            "👋 <b>Добро пожаловать в Gruppas OS!</b>\n\n"
            "Пока в базе нет чатов с вашим участием (возможно, сервер перезапускался).\n"
            "Напишите любое сообщение в вашей группе с ботом — и чат мгновенно появится здесь!",
            parse_mode="HTML"
        )
        return

    kb_rows = []
    for c in chats:
        title = c["title"] or f"Чат {c['chat_id']}"
        app_url = f"{WEBAPP_URL}?chat_id={c['chat_id']}&v={int(time.time())}"
        kb_rows.append([
            InlineKeyboardButton(text=f"📱 Открыть OS: {title}", web_app=WebAppInfo(url=app_url))
        ])

    kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
    await message.answer(
        f"🎛 <b>Gruppas OS — Центр управления</b>\n\n"
        f"Привет, <b>{message.from_user.full_name}</b>! Ваши чаты:",
        parse_mode="HTML",
        reply_markup=kb
    )

@dp.message(Command("profile"), F.chat.type.in_({"group", "supergroup"}))
async def cmd_group_profile(message: types.Message):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO chats (chat_id, title, owner_id)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title
        """, (message.chat.id, message.chat.title or f"Чат {message.chat.id}", message.from_user.id))
        upsert_member_with_conn(conn, message.chat.id, message.from_user)
        conn.commit()

    app_url = f"{WEBAPP_URL}?chat_id={message.chat.id}&v={int(time.time())}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Открыть Gruppas OS", web_app=WebAppInfo(url=app_url))]
    ])
    await message.answer(
        f"👤 <b>Профиль и настройка чата</b>\nУчастник: {message.from_user.mention_html()}",
        parse_mode="HTML",
        reply_markup=kb
    )

@dp.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_messages(message: types.Message):
    if not message.from_user or message.from_user.is_bot:
        return
        
    chat_id = message.chat.id
    user = message.from_user
    
    with get_db() as conn:
        conn.execute("""
            INSERT INTO chats (chat_id, title, owner_id)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET title = excluded.title
        """, (chat_id, message.chat.title or f"Чат {chat_id}", user.id))
        upsert_member_with_conn(conn, chat_id, user)
        conn.commit()
    
    if not message.text:
        return
        
    with get_db() as conn:
        chat = conn.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
        member = conn.execute("SELECT * FROM chat_members WHERE chat_id = ? AND user_id = ?", (chat_id, user.id)).fetchone()
    
    if not chat or not chat["is_configured"] or not chat["ai_enabled"] or not chat["rules_text"]:
        return

    # Проверяем реальный статус пользователя в Telegram (создатель/админ чата пропускаются)
    try:
        tg_member = await bot.get_chat_member(chat_id, user.id)
        if tg_member.status in ("creator", "administrator"):
            return
    except Exception as e:
        logging.error(f"Ошибка проверки статуса в Telegram: {e}")

    is_prem = bool(member["is_prem"]) if member else False
    
    verdict = await check_message_with_grok(
        rules_text=chat["rules_text"],
        user_name=user.full_name,
        is_prem=is_prem,
        message_text=message.text
    )
    
    if verdict.get("violated"):
        mute_mins = int(verdict.get("mute_minutes", 60))
        rule_code = verdict.get("rule_code", "Нарушение правил")
        reason = verdict.get("reason", "Нарушение правил чата")
        
        try:
            await message.delete()
        except Exception as e:
            logging.warning(f"Не удалось удалить сообщение: {e}")
            
        try:
            until_date = datetime.now(timezone.utc) + timedelta(minutes=mute_mins) if mute_mins > 0 else None
            await bot.restrict_chat_member(
                chat_id=chat_id,
                user_id=user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_date
            )
            
            duration_str = f"{mute_mins} мин." if mute_mins > 0 else "навсегда"
            prem_badge = " 👑 [Gruppas Prem]" if is_prem else ""
            
            # Отчет о наказании в чат
            await bot.send_message(
                chat_id,
                f"⚖️ <b>Grok AI Модератор</b>\n\n"
                f"👤 Нарушитель: {user.mention_html()}{prem_badge}\n"
                f"📜 Пункт: <b>{rule_code}</b>\n"
                f"💬 Причина: {reason}\n"
                f"🔇 Наказание: <b>мут на {duration_str}</b>",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Не удалось замутить участника: {e}")
            # Сообщение об ошибке, если у бота нет прав или прав на блокировку
            await bot.send_message(
                chat_id,
                f"⚠️ <b>Ошибка модерации:</b> не удалось применить наказание к {user.mention_html()}.\n"
                f"Причина: <i>У меня отсутствуют права администратора или права на блокировку пользователей в этом чате!</i>",
                parse_mode="HTML"
            )

@app.on_event("startup")
async def startup_event():
    webhook_url = f"{RENDER_URL}/webhook"
    await bot.set_webhook(
        webhook_url,
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "my_chat_member", "chat_member"]
    )
    logging.info(f"Вебхук установлен: {webhook_url}")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
