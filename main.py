import os
import aiohttp
import asyncio
from datetime import datetime, timedelta
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from timezonefinder import TimezoneFinder
from pytz import timezone
from shapely.geometry import Point
import geopandas as gpd

BOT_TOKEN = os.getenv('BOT_TOKEN')

DATA_URL = "https://raw.githubusercontent.com/datasets/geo-boundaries-world-110m/master/countries.geojson"
BUILDINGS_FILE = "data/buildings.geojson"  # локальний або завантажити
TREES_FILE = "data/trees.geojson"          # аналогічно

RADIUS_BUILDING = 15  # м
RADIUS_TREE = 10      # м

# Завантаження об'єктів (при потребі — додай в проєкт)
buildings_gdf = gpd.read_file(BUILDINGS_FILE) if os.path.exists(BUILDINGS_FILE) else gpd.GeoDataFrame()
trees_gdf = gpd.read_file(TREES_FILE) if os.path.exists(TREES_FILE) else gpd.GeoDataFrame()


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Надіслати локацію", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def find_objects_nearby(lat: float, lon: float) -> list:
    point = Point(lon, lat)
    objects = []

    if not buildings_gdf.empty:
        nearby_buildings = buildings_gdf[buildings_gdf.geometry.distance(point) * 111000 < RADIUS_BUILDING]
        objects += [{"type": "building", "geometry": geom} for geom in nearby_buildings.geometry]

    if not trees_gdf.empty:
        nearby_trees = trees_gdf[trees_gdf.geometry.distance(point) * 111000 < RADIUS_TREE]
        objects += [{"type": "tree", "geometry": geom} for geom in nearby_trees.geometry]

    return objects


def estimate_shadow_presence(objects: list) -> bool:
    return any(obj["type"] in ["tree", "building"] for obj in objects)


def summarize_object_types(objects: list) -> str:
    trees = sum(1 for o in objects if o["type"] == "tree")
    buildings = sum(1 for o in objects if o["type"] == "building")
    parts = []
    if buildings:
        parts.append(f"🏢 Будівлі: {buildings}")
    if trees:
        parts.append(f"🌳 Дерева: {trees}")
    return "\n".join(parts) if parts else "Немає об'єктів поруч."


def simulate_sun_angle(hour_decimal: float) -> float:
    return max(0, 90 - abs(hour_decimal - 13) * 7)


def get_shadow_data(objects: list, local_time: datetime) -> tuple[list[str], str]:
    has_shadow_sources = estimate_shadow_presence(objects)
    if not has_shadow_sources:
        return [], "☀️" * 24

    intervals = []
    bar = ""
    in_shadow = False
    start_time = None

    for i in range(288):  # 24*60 / 5
        minutes_offset = i * 5
        current_time = local_time + timedelta(minutes=minutes_offset)
        hour_decimal = current_time.hour + current_time.minute / 60
        sun_angle = simulate_sun_angle(hour_decimal)

        shadow = sun_angle < 45

        if i % 12 == 0:
            bar += "🌑" if shadow else "☀️"

        if shadow:
            if not in_shadow:
                start_time = current_time
                in_shadow = True
        else:
            if in_shadow:
                end_time = current_time
                intervals.append(f"{start_time.strftime('%H:%M')} – {end_time.strftime('%H:%M')}")
                in_shadow = False

    if in_shadow and start_time:
        end_time = local_time + timedelta(minutes=5 * 288)
        intervals.append(f"{start_time.strftime('%H:%M')} – {end_time.strftime('%H:%M')}")

    return intervals, bar


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚗 Вітаю! Надішліть локацію авто, щоб дізнатися прогноз тіні.",
        reply_markup=get_main_keyboard()
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)

    loc = update.message.location
    if not loc:
        await update.message.reply_text("❌ Будь ласка, надішліть локацію через кнопку.")
        return

    try:
        tf = TimezoneFinder()
        tz_name = tf.timezone_at(lat=loc.latitude, lng=loc.longitude)
        if not tz_name:
            raise ValueError("Не вдалося визначити часовий пояс.")
        local_time = datetime.now(timezone(tz_name))

        nearby_objects = find_objects_nearby(loc.latitude, loc.longitude)
        has_shadow = estimate_shadow_presence(nearby_objects)
        result = "✅ Ймовірно, в тіні." if has_shadow else "☀️ Об'єкт, ймовірно, буде під сонцем."

        summary = summarize_object_types(nearby_objects)
        shadow_intervals, shadow_graph = get_shadow_data(nearby_objects, local_time)
        shadow_text = (
            "🕶️ Час у тіні:\n" + "\n".join(shadow_intervals)
            if shadow_intervals else "☀️ Ймовірно, весь день буде без тіні."
        )

        await update.message.reply_text(
            f"{result}\n\n"
            f"🕒 Локальний час: {local_time.strftime('%H:%M')}\n"
            f"📍 Об’єкти поруч:\n{summary}\n\n"
            f"{shadow_text}\n\n"
            f"📊 Графік тіні:\n{shadow_graph}"
        )

    except Exception as e:
        print(f"Error: {e}")
        await update.message.reply_text("❗ Сталася помилка. Спробуйте пізніше.")


def main():
    if not BOT_TOKEN:
        print("❌ Помилка: BOT_TOKEN не вказаний у змінних середовища")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.LOCATION, handle_location))

    print("🤖 Бот активовано")
    app.run_polling()


if __name__ == "__main__":
    main()