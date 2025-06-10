import os
import math
import asyncio
import aiohttp
from datetime import datetime, timezone, timedelta
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from astral import Observer
from astral.sun import azimuth as sun_azimuth_func, elevation as sun_elevation_func

BOT_TOKEN = os.getenv('BOT_TOKEN')
OPENWEATHER_API_KEY = os.getenv('OPENWEATHER_API_KEY')
USE_WEATHER = os.getenv('USE_WEATHER', 'false').lower() == 'true'

ORIENTATION_DEGREES = {
    "Північ": 0,
    "Схід": 90,
    "Південь": 180,
    "Захід": 270,
    "Не знаю": None
}

# Прості кеші з часом життя 10 хв
cache_overpass = {}
cache_weather = {}

CACHE_TTL = timedelta(minutes=10)

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

async def fetch_weather(lat, lon):
    if not OPENWEATHER_API_KEY:
        return None
    key = (round(lat, 4), round(lon, 4))
    now = datetime.now(timezone.utc)
    if key in cache_weather:
        cached_time, data = cache_weather[key]
        if now - cached_time < CACHE_TTL:
            return data

    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ua"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=5) as resp:
                data = await resp.json()
                description = data['weather'][0]['description']
                clouds = data.get('clouds', {}).get('all', 0)
                rain = data.get('rain', {})
                snow = data.get('snow', {})
                precip = rain.get('1h', 0) + snow.get('1h', 0)
                is_rain = precip > 0
                is_cloudy = clouds > 70
                result = {
                    'description': description,
                    'is_rain': is_rain,
                    'is_cloudy': is_cloudy
                }
                cache_weather[key] = (now, result)
                return result
        except Exception:
            return None

def get_building_height(tags):
    if 'height' in tags:
        try:
            h_str = tags['height'].replace('m', '').strip()
            height = float(h_str)
            return height
        except:
            pass
    if 'building:levels' in tags:
        try:
            levels = int(tags['building:levels'])
            return levels * 3
        except:
            pass
    return 10  # За замовчуванням 10 метрів

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
    azimuth_deg = (math.degrees(azimuth_rad) + 360) % 360
    return azimuth_deg

def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(d_lambda/2)**2
    c = 2*math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def point_in_shadow(building_center_lat, building_center_lon, car_lat, car_lon, shadow_length, shadow_direction):
    azimuth_to_car = calculate_azimuth(building_center_lat, building_center_lon, car_lat, car_lon)
    dist = haversine_distance(building_center_lat, building_center_lon, car_lat, car_lon)
    angle_diff = min(abs(azimuth_to_car - shadow_direction), 360 - abs(azimuth_to_car - shadow_direction))
    return dist <= shadow_length and angle_diff <= 30

def get_sun_facing_side(car_azimuth, sun_azimuth):
    angle_diff = (sun_azimuth - car_azimuth + 360) % 360
    if 45 <= angle_diff < 135:
        return "ліву сторону"
    elif 135 <= angle_diff < 225:
        return "задню частину"
    elif 225 <= angle_diff < 315:
        return "праву сторону"
    else:
        return "передню частину"

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

async def analyze_shadow_schedule_detailed(lat, lon, buildings, car_orientation, interval_minutes=15):
    now = datetime.now(timezone.utc)
    end_time = now + timedelta(hours=24)
    schedule = []
    current_side = None
    state_start = now
    time = now

    while time <= end_time:
        azimuth, altitude = get_sun_position(lat, lon, time)
        if altitude <= 0:
            side = "тінь (ніч)"
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

            if in_shadow:
                side = "тінь від будівель"
            else:
                side = get_sun_facing_side(car_orientation, azimuth)

        if current_side is None:
            current_side = side
            state_start = time
        elif side != current_side:
            schedule.append((state_start, time, current_side))
            current_side = side
            state_start = time

        time += timedelta(minutes=interval_minutes)

    schedule.append((state_start, time, current_side))
    return schedule

def format_schedule_text(schedule):
    parts = {}
    for start, end, state in schedule:
        parts.setdefault(state, []).append((start, end))

    lines = []
    for state, intervals in parts.items():
        total_min = sum(int((e - s).total_seconds() // 60) for s, e in intervals)
        lines.append(f"{state}: {total_min} хв")
        for s, e in intervals:
            lines.append(f"  з {s.astimezone().strftime('%H:%M')} до {e.astimezone().strftime('%H:%M')}")
    return "\n".join(lines)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton('Запаркувався - скинути локацію', request_location=True)]]
    await update.message.reply_text(
        'Натисни кнопку, щоб надіслати свою локацію:',
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )

async def ask_car_orientation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [KeyboardButton("Північ"), KeyboardButton("Схід")],
        [KeyboardButton("Південь"), KeyboardButton("Захід")],
        [KeyboardButton("Не знаю")]
    ]
    await update.message.reply_text(
        "Вкажіть, в який бік орієнтована передня частина вашої машини або оберіть «Не знаю»:",
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )

async def orientation_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text not in ORIENTATION_DEGREES:
        await update.message.reply_text("Будь ласка, оберіть напрямок із кнопок.")
        return
    orientation = ORIENTATION_DEGREES[text]
    context.user_data['car_orientation'] = orientation
    await update.message.reply_text(f"Орієнтація машини встановлена: {text}. Обробляю дані...")

    if orientation is None:
        await analyze_and_report_shadow_simple(update, context)
    else:
        await analyze_and_report_shadow(update, context)

async def analyze_and_report_shadow_simple(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = context.user_data.get('location') or update.message.location
    lat = loc.latitude
    lon = loc.longitude

    if USE_WEATHER:
        weather = await fetch_weather(lat, lon)
        if weather is None:
            await update.message.reply_text("Не вдалося отримати інформацію про погоду.")
            return
        if weather['is_rain']:
            await update.message.reply_text(f"Зараз йде опад ({weather['description']}). Тінь від сонця відсутня.")
            return
        if weather['is_cloudy']:
            await update.message.reply_text(f"Зараз хмарно ({weather['description']}). Сонячна тінь може бути слабкою або відсутньою.")

    buildings = await fetch_overpass(lat, lon, radius=50)
    if not buildings:
        await update.message.reply_text("Не вдалося знайти будівлі поблизу для аналізу тіні.")
        return

    count_buildings = len(buildings)
    count_with_height = 0
    count_with_levels = 0
    for b in buildings:
        tags = b.get('tags', {})
        if 'height' in tags:
            count_with_height += 1
        if 'building:levels' in tags:
            count_with_levels += 1

    report_lines = [
        f"Знайдено будівель поблизу: {count_buildings}",
        f"З них з вказаною висотою: {count_with_height}",
        f"З них з вказаною кількістю поверхів: {count_with_levels}"
    ]
    await update.message.reply_text("\n".join(report_lines))

    # Перевірка, чи машина під будівлею (відстань < 3м)
    for b in buildings:
        b_lat = None
        b_lon = None
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

        dist = haversine_distance(lat, lon, b_lat, b_lon)
        if dist < 3:
            await update.message.reply_text(
                f"Ваша машина знаходиться безпосередньо під будівлею (ID: {b.get('id', 'невідомо')}) — вона в тіні."
            )
            return

    schedule = await analyze_shadow_schedule(lat, lon, buildings, interval_minutes=15)
    text = format_schedule_text(schedule)
    await update.message.reply_text(text)

async def analyze_and_report_shadow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = context.user_data.get('location') or update.message.location
    lat = loc.latitude
    lon = loc.longitude
    car_orientation = context.user_data.get('car_orientation')
    if car_orientation is None:
        await update.message.reply_text("Помилка: не задано орієнтацію машини.")
        return

    if USE_WEATHER:
        weather = await fetch_weather(lat, lon)
        if weather is None:
            await update.message.reply_text("Не вдалося отримати інформацію про погоду.")
            return
        if weather['is_rain']:
            await update.message.reply_text(f"Зараз йде опад ({weather['description']}). Тінь від сонця відсутня.")
            return
        if weather['is_cloudy']:
            await update.message.reply_text(f"Зараз хмарно ({weather['description']}). Сонячна тінь може бути слабкою або відсутньою.")

    buildings = await fetch_overpass(lat, lon, radius=50)
    if not buildings:
        await update.message.reply_text("Не вдалося знайти будівлі поблизу для аналізу тіні.")
        return

    count_buildings = len(buildings)
    count_with_height = 0
    count_with_levels = 0
    for b in buildings:
        tags = b.get('tags', {})
        if 'height' in tags:
            count_with_height += 1
        if 'building:levels' in tags:
            count_with_levels += 1

    report_lines = [
        f"Знайдено будівель поблизу: {count_buildings}",
        f"З них з вказаною висотою: {count_with_height}",
        f"З них з вказаною кількістю поверхів: {count_with_levels}"
    ]
    await update.message.reply_text("\n".join(report_lines))

    # Перевірка, чи машина під будівлею (відстань < 3м)
    for b in buildings:
        b_lat = None
        b_lon = None
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

        dist = haversine_distance(lat, lon, b_lat, b_lon)
        if dist < 3:
            await update.message.reply_text(
                f"Ваша машина знаходиться безпосередньо під будівлею (ID: {b.get('id', 'невідомо')}) — вона в тіні."
            )
            return

    schedule = await analyze_shadow_schedule_detailed(lat, lon, buildings, car_orientation, interval_minutes=15)
    text = format_schedule_text(schedule)
    await update.message.reply_text(text)

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.location is None:
        await update.message.reply_text("Будь ласка, надішліть локацію.")
        return
    context.user_data['location'] = update.message.location
    await ask_car_orientation(update, context)

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), orientation_handler))
    print("Бот запущено...")
    app.run_polling()

if __name__ == '__main__':
    if not BOT_TOKEN:
        print("Помилка: не задано BOT_TOKEN")
        exit(1)
    main()
