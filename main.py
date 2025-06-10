from flask import Flask, request
from telegram import Bot, Update
from telegram.ext import Dispatcher, MessageHandler, Filters
from suntime import Sun
from datetime import datetime, timedelta
import pytz
import os

TOKEN = os.getenv('BOT_TOKEN')
bot = Bot(token=TOKEN)
app = Flask(__name__)

def check_shadow(lat, lon):
    sun = Sun(lat, lon)
    now = datetime.utcnow()
    try:
        elevation = sun.get_solar_elevation(now)
        if elevation < 35:
            return f"🌳 Є тінь зараз (висота сонця: {int(elevation)}°)"
        else:
            for i in range(1, 240):
                t = now + timedelta(minutes=i)
                if sun.get_solar_elevation(t) < 35:
                    return f"☀️ Зараз на сонці. Тінь буде через {i} хв."
            return "☀️ Зараз на сонці. Тінь з'явиться пізніше сьогодні."
    except Exception as e:
        return "Помилка розрахунку тіні."

def handle_location(update, context):
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude
    msg = check_shadow(lat, lon)
    update.message.reply_text(msg)

@app.route(f"/{TOKEN}", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), bot)
    dp = Dispatcher(bot, None, workers=0)
    dp.add_handler(MessageHandler(Filters.location, handle_location))
    dp.process_update(update)
    return 'ok'

@app.route('/')
def home():
    return 'Bot is running!'

if __name__ == '__main__':
    app.run()