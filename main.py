
from aiogram import Bot, Dispatcher, types
from aiogram.types import Location
from aiogram.utils import executor
from suntime import Sun
from datetime import datetime
import os

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

@dp.message_handler(commands=['start'])
async def start_cmd(message: types.Message):
    await message.reply("👋 Надішли свою геолокацію, де запаркувався.")

@dp.message_handler(content_types=['location'])
async def handle_location(message: types.Message):
    loc: Location = message.location
    sun = Sun(loc.latitude, loc.longitude)
    now = datetime.utcnow()

    try:
        sr = sun.get_sunrise_time()
        ss = sun.get_sunset_time()

        if now < sr:
            diff = sr - now
            await message.reply(f"Зараз темно 🌙. Сонце зійде через {diff.seconds//60} хв.")
        elif now > ss:
            await message.reply("Сонце вже сіло 🌙. Місце в тіні.")
        else:
            await message.reply("Сонце над горизонтом ☀️. Можливо, ще немає тіні.")
    except Exception as e:
        await message.reply("Помилка при розрахунках.")

if __name__ == '__main__':
    executor.start_polling(dp)
