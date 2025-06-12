import os
import aiohttp
from datetime import datetime
import pytz
from timezonefinder import TimezoneFinder
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = os.getenv("BOT_TOKEN")

def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Надіслати локацію", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_local_time(lat: float, lon: float) -> datetime:
    tf = TimezoneFinder()
    timezone_str = tf.timezone_at(lat=lat, lng=lon)
    if timezone_str is None:
        timezone_str = "UTC"
    tz = pytz.timezone(timezone_str)
    return datetime.now(tz)

async def fetch_nearby_objects(lat: float, lon: float) -> list:
    query = f"""
    [out:json][timeout:10];
    (
      way(around:50,{lat},{lon})["building"];
      node(around:50,{lat},{lon})["natural"="tree"];
      way(around:50,{lat},{lon})["landuse"="forest"];
    );
    out body;
    """
    url = "https://overpass-api.de/api/interpreter"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data={"data": query}) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result.get("elements", [])
    except Exception as e:
        print(f"Error fetching Overpass data: {e}")
    return []

def estimate_shadow_presence(objects: list) -> bool:
    for obj in objects:
        tags = obj.get("tags", {})
        if "building" in tags:
            return True
        if tags.get("natural") == "tree" or tags.get("landuse") == "forest":
            return True
    return False

def summarize_object_types(objects: list) -> str:
    summary = {"building": 0, "tree": 0, "forest": 0}
    for obj in objects:
        tags = obj.get("tags", {})
        if "building" in tags:
            summary["building"] += 1
        elif tags.get("natural") == "tree":
            summary["tree"] += 1
        elif tags.get("landuse") == "forest":
            summary["forest"] += 1

    parts = []
    if summary["building"]:
        parts.append(f"🏢 Будівлі: {summary['building']}")
    if summary["tree"]:
        parts.append(f"🌳 Дерева: {summary['tree']}")
    if summary["forest"]:
        parts.append(f"🌲 Ліс: {summary['forest']}")

    return "\n".join(parts) if parts else "🚫 Нічого не знайдено"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚗 Привіт! Надішліть локацію авто для аналізу тіні.",
        reply_markup=get_main_keyboard()
    )

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    if not loc:
        await update.message.reply_text("❌ Надішліть локацію через кнопку.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    try:
        local_time = get_local_time(loc.latitude, loc.longitude)
        nearby_objects = await fetch_nearby_objects(loc.latitude, loc.longitude)
        is_shadow = estimate_shadow_presence(nearby_objects)
        summary = summarize_object_types(nearby_objects)

        result = "✅ Ймовірно, в тіні." if is_shadow else "☀️ Ймовірно, на сонці."

        await update.message.reply_text(
            f"{result}\n\n"
            f"🕒 Локальний час: {local_time.strftime('%H:%M')}\n"
            f"📍 Об’єкти поруч:\n{summary}"
        )
    except Exception as e:
        print(f"Error: {e}")
        await update.message.reply_text("⚠️ Помилка. Спробуйте пізніше.")

def main():
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не встановлений!")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))

    print("🤖 Бот активовано!")
    app.run_polling()

if __name__ == "__main__":
    main()