@dp.message(Command("start"), F.chat.type == "private")
async def cmd_start_private(message: types.Message):
    """Приветствие в ЛС: если у пользователя есть чаты, сразу показываем панель с кнопками"""
    conn = sqlite3.connect("gruppas.db")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    # Ищем чаты, где этот пользователь владелец
    chats = cur.execute("SELECT * FROM chats WHERE owner_id = ?", (message.from_user.id,)).fetchall()
    conn.close()
    
    if chats:
        # Если чаты настроены — сразу выводим инлайн-кнопки панелей
        kb_buttons = []
        for chat in chats:
            panel_url = f"{WEBAPP_URL}?chat_id={chat['chat_id']}&v={int(time.time())}"
            chat_title = chat["title"] or f"Чат #{chat['chat_id']}"
            kb_buttons.append([
                InlineKeyboardButton(text=f"🛡 Управлять: {chat_title}", web_app=WebAppInfo(url=panel_url))
            ])
            
        kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
        await message.answer(
            f"🎛 <b>Панель управления Gruppas OS</b>\n\n"
            f"Привет, <b>{message.from_user.full_name}</b>! Ваши чаты успешно привязаны. "
            f"Выберите нужный чат для настройки ниже:",
            parse_mode="HTML",
            reply_markup=kb
        )
    else:
        # Если чатов еще нет — стандартное приглашение
        await message.answer(
            "👋 Добро пожаловать в <b>Gruppas OS</b>!\n\n"
            "Чтобы начать работу, добавьте меня в ваш групповой чат, выдайте права администратора и отправьте команду <b>/setup</b>.",
            parse_mode="HTML"
        )
