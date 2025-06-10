import os
import math
import requests
from datetime import datetime, timezone, timedelta
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from astral import LocationInfo
from astral.sun import sun
from astral import sun as astral_sun
from astral import Observer

BOT_TOKEN = os.getenv('BOT_TOKEN')
OPENWEATHER_API_KEY = os.getenv('OPENWEATHER_API_KEY')
USE_WEATHER = os.getenv('USE_WEATHER', 'false').lower() == 'true'

def get_weather(lat, lon):
    if not OPENWEATHER_API_KEY:
        return None
    url = f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}&appid={OPENWEATHER_API_KEY}&units=metric&lang=ua"
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        description = data['weather'][0]['description']
        clouds = data.get('clouds', {}).get('all', 0)
        rain = data.get('rain', {})
        snow = data.get('snow', {})
        precip = rain.get('1h', 0) + snow.get('1h', 0)
        is_rain = precip > 0
        is_cloudy = clouds > 70
        return {
            'description': description,
            'is_rain': is_rain,
            'is_cloudy': is_cloudy
        }
    except Exception:
        return None

def get_nearby_buildings(lat, lon, radius=50):
    query = f"""
    [out:json];
    (
      way["building"](around:{radius},{lat},{lon});
      relation["building"](around:{radius},{lat},{lon});
    );
    out center tags;
    """
    response = requests.post("https://overpass-api.de/api/interpreter", data={'data': query})
    data = response.json()
    return data.get('elements', [])

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
    azimuth = astral_sun.azimuth(observer, date_time)
    altitude = astral_sun.elevation(observer, date_time)
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
    if dist <= shadow_length and angle_diff <= 30:
        return True
    return False

def analyze_shadow_schedule(lat, lon, buildings, interval_minutes=15):
    now = datetime.now(timezone.utc)
    end_time = now + timedelta(hours=24)

    schedule = []
    current_state = None
    state_start = now
    time = now

    while time <= end_time:
        azimuth, altitude = get_sun_position(lat, lon, time)
        if altitude <= 0:
            state = 'shade'  # ніч, тінь
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

            state = 'shade' if in_shadow else 'sun'

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

def format_schedule_text(schedule):
    shade_periods = [p for p in schedule if p[2] == 'shade']
    sun_periods = [p for p in schedule if p[2] == 'sun']

    total_shade = sum((p[1] - p[0]).total_seconds() for p in shade_periods) / 60
    total_sun = sum((p[1] - p[0]).total_seconds() for p in sun_periods) / 60

    def period_str(p):
        start_str = p[0].astimezone().strftime('%H:%M')
        end_str = p[1].astimezone().strftime('%H:%M')
        duration_min = int((p[1] - p[0]).total_seconds() // 60)
        return f"{duration_min}хв з {start_str} до {end_str}"

    shade_texts = [period_str(p) for p in shade_periods]
    sun_texts = [period_str(p) for p in sun_periods]

    text = (f"Ваша машина буде в тіні {int(total_shade)} хв:\n" +
            "\n".join(shade_texts) + "\n\n" +
            f"Під сонцем буде {int(total_sun)} хв:\n" +
            "\n".join(sun_texts))
    return text

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton('Запаркувався - скинути локацію', request_location=True)]]
    await update.message.reply_text(
        'Натисни кнопку, щоб надіслати свою локацію:',
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude

    if USE_WEATHER:
        weather = get_weather(lat, lon)
        if weather is None:
            await update.message.reply_text("Не вдалося отримати інформацію про погоду.")
            return
        if weather['is_rain']:
            await update.message.reply_text(f"Зараз йде опад ({weather['description']}). Тінь від сонця відсутня.")
            return
        if weather['is_cloudy']:
            await update.message.reply_text(f"Зараз хмарно ({weather['description']}). Сонячна тінь може бути слабкою або відсутньою.")

    buildings = get_nearby_buildings(lat, lon, radius=50)
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
    report_text = "\n".join(report_lines)
    await update.message.reply_text(report_text)

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

    schedule = analyze_shadow_schedule(lat, lon, buildings, interval_minutes=15)
    text = format_schedule_text(schedule)
    await update.message.reply_text(text)

if __name__ == '__main__':
    if not BOT_TOKEN:
        print("Помилка: не задано BOT_TOKEN")
        exit(1)

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))
    print("Бот запущено...")
    app.run_polling()
