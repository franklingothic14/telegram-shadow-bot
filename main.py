import os
import math
import requests
from datetime import datetime, timezone
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from astral import LocationInfo
from astral.sun import sun
from astral import sun as astral_sun
from astral import Observer

# Отримання ключів API та налаштувань з оточення
BOT_TOKEN = os.getenv('BOT_TOKEN')
OPENWEATHER_API_KEY = os.getenv('OPENWEATHER_API_KEY')
USE_WEATHER = os.getenv('USE_WEATHER', 'false').lower() == 'true'

# Функція для отримання погоди
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

# Функція для отримання будівель поблизу з OpenStreetMap
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

# Функція для обчислення положення сонця
def get_sun_position(lat, lon, date_time):
    observer = Observer(latitude=lat, longitude=lon, elevation=0)
    azimuth = astral_sun.azimuth(observer, date_time)
    altitude = astral_sun.elevation(observer, date_time)
    return azimuth, altitude

# Функція для обчислення довжини тіні
def calculate_shadow_length(height, sun_altitude_deg):
    if sun_altitude_deg <= 0:
        return None
    sun_altitude_rad = math.radians(sun_altitude_deg)
    return height / math.tan(sun_altitude_rad)

# Функція для обчислення азимуту між двома точками
def calculate_azimuth(lat1, lon1, lat2, lon2):
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    d_lon = math.radians(lon2 - lon1)
    x = math.sin(d_lon) * math.cos(lat2_rad)
    y = math.cos(lat1_rad)*math.sin(lat2_rad) - math.sin(lat1_rad)*math.cos(lat2_rad)*math.cos(d_lon)
    azimuth_rad = math.atan2(x, y)
    azimuth_deg = (math.degrees(azimuth_rad) + 360) % 360
    return azimuth_deg

# Функція для обчислення відстані між двома точками
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(d_lambda/2)**2
    c = 2*math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# Функція для перевірки, чи точка в тіні
def point_in_shadow(building_center_lat, building_center_lon, car_lat, car_lon, shadow_length, shadow_direction):
    azimuth_to_car = calculate_azimuth(building_center_lat, building_center_lon, car_lat, car_lon)
    dist = haversine_distance(building_center_lat, building_center_lon, car_lat, car_lon)
    angle_diff = min(abs(azimuth_to_car - shadow_direction), 360 - abs(azimuth_to_car - shadow_direction))
    if dist <= shadow_length and angle_diff <= 30:
        return True
    return False

# Обробник команди /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton('Запаркувався - скинути локацію', request_location=True)]]
    await update.message.reply_text(
        'Натисни кнопку, щоб надіслати свою локацію:',
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )

# Обробник отриманої локації
async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude

    # Перевірка погоди, якщо увімкнено
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

    now = datetime.now(timezone.utc)
    sun_azimuth, sun_altitude = get_sun_position(lat, lon, now)

    if sun_altitude <= 0:
        await update.message.reply_text("Сонце зараз під горизонтом, тіні від сонця немає.")
        return

    buildings = get_nearby_buildings(lat, lon, radius=50)
    if not buildings:
        await update.message.reply_text("Не вдалося знайти будівлі поблизу для аналізу тіні.")
        return

    # Перевірка, чи локація співпадає з будівлею
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

    # Перевірка наявності тіні від будівель
    in_shadow = False
    shadow_building_id = None
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

        tags = b.get('tags', {})
        height = None
        if 'height' in tags:
            try:
                height = float(tags['height'].replace('m','').strip())
            except:
                height = 10
        elif 'building:levels' in tags:
            try:
                height = float(tags['building:levels']) * 3
            except:
                height = 10
        else:
            height = 10

        shadow_length = calculate_shadow_length(height, sun_altitude)
        if shadow_length is None:
            continue

        shadow_direction = (sun_azimuth + 180) % 360

        if point_in_shadow(b_lat, b_lon, lat, lon, shadow_length, shadow_direction):
            in_shadow = True
            shadow_building_id = b.get('id', 'невідомо')
            break

    if in_shadow:
        await update.message.reply_text(
            f"Ваша машина зараз у тіні від будівлі (ID: {shadow_building_id}).\n"
            f"Тінь залежить від положення сонця і триватиме приблизно до заходу."
        )
    else:
        city = LocationInfo(latitude=lat, longitude=lon)
        s = sun(city.observer, date=now, tzinfo=timezone.utc)
        sunset = s['sunset']
        delta = sunset - now
        minutes_to_shadow = int(delta.total_seconds() // 60) if delta.total_seconds() > 0 else 0
        await update.message.reply_text(
            f"Зараз ваша машина на сонці.\n"
            f"Тінь з’явиться приблизно через {minutes_to_shadow} хвилин (після заходу сонця)."
        )

if __name__ == '__main__':
    if not BOT_TOKEN:
        print("Помилка: не задано BOT_TOKEN")
        exit(1)

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))
    print("Бот запущено...")
    app.run_polling()
