import os
import math
import aiohttp
from datetime import datetime, timezone, timedelta
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from astral import Observer
from astral.sun import azimuth as sun_azimuth_func, elevation as sun_elevation_func

BOT_TOKEN = os.getenv('BOT_TOKEN')
CACHE_TTL = timedelta(minutes=10)
cache_overpass = {}

def get_main_keyboard():
    kb = [[KeyboardButton("Надіслати локацію", request_location=True)]]
    return ReplyKeyboardMarkup(kb, resize_keyboard=True, one_time_keyboard=True)

async def fetch_overpass(lat, lon, radius=50):
    key = (round(lat, 4), round(lon, 4), radius)
    now = datetime.now(timezone.utc)
    if key in cache_overpass:
        cached_time, data = cache_overpass[key]
        if now - cached_time < CACHE_TTL:
            return data

    query = f"""
    [out:json];
    (
      way["building"](around:{radius},{lat},{lon});
      relation["building"](around:{radius},{lat},{lon});
    );
    out center tags;
    """
    url = "https://overpass-api.de/api/interpreter"
    async with aiohttp.ClientSession() as session:
        async with session.post(url, data={'data': query}) as resp:
            data = await resp.json()
            cache_overpass[key] = (now, data.get('elements', []))
            return cache_overpass[key][1]

def get_building_height(tags):
    if 'height' in tags:
        try:
            h_str = tags['height'].replace('m', '').strip()
            return float(h_str)
        except:
            pass
    if 'building:levels' in tags:
        try:
            return int(tags['building:levels']) * 3
        except:
            pass
    return 15  # За замовчуванням 15 метрів

def get_sun_position(lat, lon, date_time):
    observer = Observer(latitude=lat, longitude=lon, elevation=0)
    azimuth = sun_azimuth_func(observer, date_time)
    altitude = sun_elevation_func(observer, date_time)
    return azimuth, altitude

def calculate_shadow_length(height, sun_altitude_deg):
    if sun_altitude_deg <= 0:
        return None
    sun_altitude_rad = math.radians(sun_altitude_deg)
    return height / math.tan(sun_altitude_rad)

def calculate_azimuth(lat1, lon1, lat2, lon2):
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    d_lon = math.radians(lon2 - lon1)
    x = math.sin(d_lon) * math.cos(lat2_rad)
    y = math.cos(lat1_rad)*math.sin(lat2_rad) - math.sin(lat1_rad)*math.cos(lat2_rad)*math.cos(d_lon)
    azimuth_rad = math.atan2(x, y)
    return (math.degrees(azimuth_rad) + 360) % 360

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000  # радіус Землі в метрах
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(d_lambda/2)**2
    c = 2*math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def point_in_shadow(b_lat, b_lon, car_lat, car_lon, shadow_length, shadow_direction):
    azimuth_to_car = calculate_azimuth(b_lat, b_lon, car_lat, car_lon)
    dist = haversine_distance(b_lat, b_lon, car_lat, car_lon)
    angle_diff = min(abs(azimuth_to_car - shadow_direction), 360 - abs(azimuth_to_car - shadow_direction))
    return dist <= shadow_length and angle_diff <= 30

async def analyze_shadow_schedule(lat, lon, buildings, interval_minutes=15):
    now = datetime.now(timezone.utc)
    end_time = now + timedelta(hours=24)
    schedule = []
    current_state = None
    state_start = now
    time = now

    while time <= end_time:
        azimuth, altitude = get_sun_position(lat, lon, time)
        if altitude <= 0:
            state = 'тінь (ніч)'
        else:
            shadow_direction = (azimuth + 180) % 360
            in_shadow = False
            for b in buildings:
                if 'center' in b:
                    b_lat = b['center']['lat']
                    b_lon = b['center']['lon']
                elif 'geometry' in b and len(b['geometry']) > 0:
                    lats = [p['lat'] for p in b['geometry']]
                    lons = [p['lon'] for p in b['geometry']]
                    b_lat = sum(lats)/len(lats)
                    b_lon = sum(lons)/len(lons)
                else:
                    continue

                tags = b.get('tags', {})
                height = get_building_height(tags)
                shadow_length = calculate_shadow_length(height, altitude)
                if shadow_length is None:
                    continue

                if point_in_shadow(b_lat, b_lon, lat, lon, shadow_length, shadow_direction):
                    in_shadow = True
                    break

            state = 'тінь від будівель' if in_shadow else 'сонце'

        if current_state is None:
            current_state = state
            state_start = time
        elif state != current_state:
            schedule.append((state_start, time, current_state))
            current_state = state
            state_start = time

        time += timedelta(minutes=interval_minutes)

    schedule.append((state_start, time, current_state))
    return schedule

def format_schedule_text_with_sun_slots(schedule):
    sun_periods = [p for p in schedule if p[2] == 'сонце']
    shade_periods = [p for p in schedule if p[2].startswith('тінь')]

    total_sun = int(sum((p[1] - p[0]).total_seconds() for p in sun_periods) // 60)
    total_shade = int(sum((p[1] - p[0]).total_seconds() for p in shade_periods) // 60)

    lines = [
        f"Ваша машина буде під сонцем: {total_sun} хв",
        "Часові слоти під сонцем:"
    ]

    for start, end, _ in sun_periods:
        lines.append(f"  з {start.astimezone().strftime('%H:%M')} до {end.astimezone().strftime('%H:%M')}")

    if shade_periods:
        shade_start = min(p[0] for p in shade_periods)
        shade_end = max(p[1] for p in shade_periods)
        lines.append(f"\nВаша машина буде в тіні: {total_shade} хв з {shade_start.astimezone().strftime('%H:%M')} до {shade_end.astimezone().strftime('%H:%M')}")

    return "\n".join(lines)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Натисніть кнопку, щоб надіслати свою локацію:",
        reply_markup=get_main_keyboard()
    )

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    if not loc:
        await update.message.reply_text("Будь ласка, надішліть локацію.")
        return
    lat = loc.latitude
    lon = loc.longitude

    buildings = await fetch_overpass(lat, lon, radius=50)
    if not buildings:
        await update.message.reply_text("Не вдалося знайти будівлі поблизу для аналізу тіні.")
        return

    schedule = await analyze_shadow_schedule(lat, lon, buildings, interval_minutes=15)
    text = format_schedule_text_with_sun_slots(schedule)
    await update.message.reply_text(text)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))
    print("Бот запущено...")
    app.run_polling()

if __name__ == '__main__':
    if not BOT_TOKEN:
        print("Помилка: не задано BOT_TOKEN")
        exit(1)
    main()
