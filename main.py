import os
import aiohttp
import asyncio
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

BOT_TOKEN = os.getenv('BOT_TOKEN')
SHADEMAP_API_KEY = os.getenv('SHADEMAP_API_KEY')
SHADEMAP_API_URL = "https://api.shademap.app/v1/shadow-analysis"
REQUEST_TIMEOUT = 15  # Збільшений таймаут

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Надіслати локацію", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

async def fetch_shademap_data(lat: float, lon: float) -> dict | None:
    headers = {"Authorization": f"Bearer {SHADEMAP_API_KEY}"}
    params = {
        "lat": lat,
        "lon": lon,
        "interval": 15,
        "hours": 24,
        "timezone": "auto"
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                SHADEMAP_API_URL,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                print(f"ShadeMap API returned status {resp.status}")
                return None
    except Exception as e:
        print(f"API Error: {e}")
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚗 Вітаю! Надішліть локацію авто, щоб дізнатися час під сонцем.",
        reply_markup=get_main_keyboard()
    )

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    loc = update.message.location
    if not loc:
        await update.message.reply_text("❌ Будь ласка, надішліть локацію через кнопку.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        data = await fetch_shademap_data(loc.latitude, loc.longitude)
        if not data or "total_sun_minutes" not in data:
            raise ValueError("Некоректна відповідь від API")

        sun_minutes = data.get("total_sun_minutes", 0)
        accuracy = data.get("accuracy", "high")
        
        await update.message.reply_text(
            f"☀️ **Прогноз сонця:**\n"
            f"Авто буде під сонцем *{sun_minutes} хвилин* протягом наступних 24 год.\n"
            f"Точність: {accuracy}",
            parse_mode="Markdown"
        )
        
    except asyncio.TimeoutError:
        await update.message.reply_text("⌛ Запит зайняв забагато часу. Спробуйте ще раз.")
    except Exception as e:
        print(f"Error: {e}")
        await update.message.reply_text("❗ Сталася помилка при обробці запиту. Спробуйте пізніше.")

def main():
    if not BOT_TOKEN or not SHADEMAP_API_KEY:
        print("❌ Помилка: Перевірте BOT_TOKEN та SHADEMAP_API_KEY")
        return
    
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))
    
    print("🤖 Бот активовано")
    app.run_polling()

if __name__ == "__main__":
    main()