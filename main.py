import os
import aiohttp
from datetime import datetime, timezone, timedelta
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

BOT_TOKEN = os.getenv('BOT_TOKEN')
SHADEMAP_API_KEY = os.getenv('SHADEMAP_API_KEY')  # Ваш ключ Shademap API

def get_main_keyboard():
    kb = [[KeyboardButton("Надіслати локацію", request_location=True)]]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True)

async def fetch_shademap_sun_minutes(lat, lon):
    url = "https://api.shademap.app/v1/shadow-analysis"  # Приклад URL, уточніть у документації
    headers = {
        "Authorization": f"Bearer {SHADEMAP_API_KEY}",
        "Accept": "application/json"
    }
    params = {
        "lat": lat,
        "lon": lon,
        "interval": 15,  # інтервал в хвилинах
        "hours": 24      # період аналізу (24 години)
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params) as resp:
            if resp.status == 200:
                data = await resp.json()
                # Припустимо, API повертає поле totalSunMinutes або подібне
                return data.get("totalSunMinutes") or data.get("total_sun_minutes")
            else:
                return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Вітаю! Надішліть свою локацію, щоб дізнатись, скільки часу ваша машина буде під сонцем.",
        reply_markup=get_main_keyboard()
    )

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    if not loc:
        await update.message.reply_text("Будь ласка, надішліть локацію.")
        return

    lat, lon = loc.latitude, loc.longitude

    sun_minutes = await fetch_shademap_sun_minutes(lat, lon)
    if sun_minutes is not None:
        await update.message.reply_text(
            f"За даними Shademap, ваша машина буде під сонцем приблизно {int(sun_minutes)} хвилин протягом наступних 24 годин."
        )
    else:
        await update.message.reply_text(
            "Не вдалося отримати дані з Shademap. Спробуйте пізніше або перевірте API ключ."
        )

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))
    print("Бот запущено...")
    app.run_polling()

if __name__ == "__main__":
    if not BOT_TOKEN or not SHADEMAP_API_KEY:
        print("Помилка: не задано BOT_TOKEN або SHADEMAP_API_KEY")
        exit(1)
    main()
