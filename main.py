import os
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from fastapi import FastAPI
import uvicorn
import asyncio

# Логирование
logging.basicConfig(level=logging.INFO)

# Инициализация бота (токен подтянется из переменных окружения или можно вписать сюда для теста)
BOT_TOKEN = os.getenv("BOT_TOKEN", "ТВОЙ_ТОКЕН_БОТА")
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# FastAPI для связи с Mini App и удержания сервера 24/7
app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "Gruppas Ecosystem API is running!"}

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        f"Привет, {message.from_user.first_name}!\n"
        "Экосистема Gruppas Community активна. Открой Mini App для управления аккаунтом и банком."
    )

async def start_telegram_bot():
    await dp.start_polling(bot)

# Запуск бота в фоновом режиме вместе с FastAPI
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(start_telegram_bot())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
