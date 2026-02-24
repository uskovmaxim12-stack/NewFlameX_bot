import os
import random
import string
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
import asyncpg
import aiorcon

# ===== НАСТРОЙКИ =====
BOT_TOKEN = os.environ.get("BOT_TOKEN")
RCON_HOST = os.environ.get("RCON_HOST", "localhost")
RCON_PORT = int(os.environ.get("RCON_PORT", "25575"))
RCON_PASSWORD = os.environ.get("RCON_PASSWORD")
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x]
DATABASE_URL = os.environ.get("DATABASE_URL")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# ===== РАБОТА С БД =====
async def create_pool():
    return await asyncpg.create_pool(DATABASE_URL)

async def init_db():
    pool = await create_pool()
    async with pool.acquire() as conn:
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                telegram_nick TEXT,
                minecraft_nick TEXT UNIQUE,
                balance INTEGER DEFAULT 0
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                donat_name TEXT,
                amount INTEGER,
                code TEXT UNIQUE,
                status TEXT DEFAULT 'pending'
            )
        ''')
    await pool.close()

# Генерация кода платежа
def generate_code():
    return 'NFX-' + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

# ===== КОМАНДЫ =====
@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.reply(
        "👋 Привет! Это бот сервера NewFlameX.\n\n"
        "📝 Зарегистрируйся: /reg <ник>\n"
        "🛒 Магазин: /shop\n"
        "👤 Профиль: /profile"
    )

@dp.message_handler(commands=['reg'])
async def cmd_reg(message: types.Message):
    args = message.get_args()
    if not args:
        await message.reply("❌ Укажи ник: /reg Игрок123")
        return

    pool = await create_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO users (user_id, telegram_nick, minecraft_nick) VALUES ($1, $2, $3)",
                message.from_user.id, message.from_user.username or "no_username", args
            )
            await message.reply(f"✅ Ник {args} успешно привязан!")
        except asyncpg.UniqueViolationError:
            await message.reply("❌ Этот ник уже занят или ты уже зарегистрирован.")
    await pool.close()

@dp.message_handler(commands=['profile'])
async def cmd_profile(message: types.Message):
    pool = await create_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT minecraft_nick, balance FROM users WHERE user_id = $1",
            message.from_user.id
        )
    await pool.close()
    if not row:
        await message.reply("❌ Ты не зарегистрирован. Используй /reg")
    else:
        await message.reply(
            f"👤 Твой профиль:\n"
            f"🎮 Ник: {row['minecraft_nick']}\n"
            f"💰 Баланс: {row['balance']} баллов"
        )

@dp.message_handler(commands=['shop'])
async def cmd_shop(message: types.Message):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton("Imperial 19₽", callback_data="buy_imperial"),
        InlineKeyboardButton("Nether 49₽", callback_data="buy_nether"),
        InlineKeyboardButton("Space 99₽", callback_data="buy_space"),
        InlineKeyboardButton("Samurai 199₽", callback_data="buy_samurai"),
        InlineKeyboardButton("Flame 499₽", callback_data="buy_flame")
    )
    await message.reply("🛒 Выбери донат:", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith('buy_'))
async def process_buy(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    donat = callback.data.replace('buy_', '').upper()
    prices = {'imperial': 19, 'nether': 49, 'space': 99, 'samurai': 199, 'flame': 499}
    price = prices[donat.lower()]

    pool = await create_pool()
    async with pool.acquire() as conn:
        # Проверка регистрации
        user = await conn.fetchrow("SELECT minecraft_nick FROM users WHERE user_id = $1", user_id)
        if not user:
            await bot.answer_callback_query(callback.id, "❌ Сначала зарегистрируйся командой /reg", show_alert=True)
            await pool.close()
            return

        # Генерируем код платежа
        code = generate_code()
        await conn.execute(
            "INSERT INTO payments (user_id, donat_name, amount, code) VALUES ($1, $2, $3, $4)",
            user_id, donat, price, code
        )
    await pool.close()

    text = (
        f"💰 Для покупки *{donat}* переведи *{price} руб* на карту:\n"
        f"`1234 5678 9012 3456` (Сбербанк)\n"
        f"📌 Обязательно укажи в комментарии код: `{code}`\n\n"
        f"После перевода нажми кнопку ниже"
    )
    keyboard = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Я оплатил", callback_data=f"paid_{code}")
    )
    await bot.send_message(user_id, text, reply_markup=keyboard, parse_mode="Markdown")
    await bot.answer_callback_query(callback.id)

@dp.callback_query_handler(lambda c: c.data.startswith('paid_'))
async def confirm_paid(callback: types.CallbackQuery):
    code = callback.data.replace('paid_', '')
    user_id = callback.from_user.id

    # Уведомляем админов (можно заменить на ID своего чата)
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🆕 Пользователь {user_id} подтвердил оплату с кодом {code}.\n"
                f"Проверь и выдай донат командой /approve {code}"
            )
        except:
            pass
    await bot.send_message(user_id, "📨 Заявка отправлена администратору. Ожидай подтверждения.")
    await bot.answer_callback_query(callback.id)

@dp.message_handler(commands=['approve'])
async def approve_payment(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    code = message.get_args()
    if not code:
        await message.reply("Укажи код: /approve NFX-XXXXXX")
        return

    pool = await create_pool()
    async with pool.acquire() as conn:
        # Находим платеж
        payment = await conn.fetchrow(
            "SELECT user_id, donat_name FROM payments WHERE code = $1 AND status = 'pending'",
            code
        )
        if not payment:
            await message.reply("❌ Код не найден или уже обработан.")
            await pool.close()
            return

        user_id = payment['user_id']
        donat = payment['donat_name']

        # Получаем ник игрока
        user = await conn.fetchrow("SELECT minecraft_nick FROM users WHERE user_id = $1", user_id)
        if not user:
            await message.reply("❌ Пользователь не найден в БД.")
            await pool.close()
            return
        nick = user['minecraft_nick']

        # Обновляем статус платежа
        await conn.execute("UPDATE payments SET status = 'approved' WHERE code = $1", code)
    await pool.close()

    # Отправляем команду на сервер через RCON
    try:
        rcon = await aiorcon.connect(RCON_HOST, RCON_PORT, RCON_PASSWORD)
        command = f"lp user {nick} parent add {donat}"
        response = await rcon.command(command)
        await rcon.close()
        await bot.send_message(user_id, f"✅ Донат *{donat}* успешно выдан! Спасибо за поддержку!", parse_mode="Markdown")
        await message.reply(f"✅ Команда выполнена:\n`{command}`\nОтвет: {response}", parse_mode="Markdown")
    except Exception as e:
        await message.reply(f"❌ Ошибка при выполнении команды: {e}")
        await bot.send_message(user_id, "❌ Ошибка при выдаче доната. Свяжись с админом @IvanShege")

# ===== ЗАПУСК =====
async def on_startup(dp):
    await init_db()
    logging.info("Бот запущен!")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)
