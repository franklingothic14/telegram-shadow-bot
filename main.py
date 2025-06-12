import os
from datetime import datetime, timedelta

from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

from timezonefinder import TimezoneFinder
from pytz import timezone


BOT_TOKEN = os.getenv("BOT_TOKEN")


def get_main_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📍 Надіслати локацію", request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def simulate_sun_angle(hour_decimal: float) -> float:
    return max(0, 90 - abs(hour_decimal - 13) * 7)


def get_shadow_data(local_time: datetime) -> tuple[list[str], str]:
    intervals = []
    bar = ""
    in_shadow = False
    start_time = None

    for i in range(288):  # 24 години по 5 хв = 288 таймслотів
        minutes_offset = i * 5
        current_time = local_time.replace(second=0, microsecond=0) + timedelta(minutes=minutes_offset)
        hour_decimal = current_time.hour + current_time.minute / 60
        sun_angle = simulate_sun_angle(hour_decimal)

        shadow = sun_angle < 45

        if i % 12 == 0:
            bar += "🌑" if shadow else "☀️"

        if shadow and not in_shadow:
            start_time = current_time
            in_shadow = True
        elif not shadow and in_shadow:
            intervals.append(f"{start_time.strftime('%H:%M')} – {current_time.strftime('%H:%M')}")
            in_shadow = False

    if in_shadow and start_time:
        intervals.append(f"{start_time.strftime('%H:%M')} – {current_time.strftime('%H:%M')}")

    return intervals, bar


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚗 Вітаю! Надішліть локацію авто, щоб дізнатися прогноз тіні.",
        reply_markup=get_main_keyboard()
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

        intervals, bar = get_shadow_data(local_time)
        shadow_text = (
            "🕶️ Час у тіні:\n" + "\n".join(intervals)
            if intervals else "☀️ Ймовірно, весь день буде без тіні."
        )

        await update.message.reply_text(
            f"🕒 Локальний час: {local_time.strftime('%H:%M')}\n\n"
            f"{shadow_text}\n\n"
            f"📊 Графік тіні:\n{bar}"
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