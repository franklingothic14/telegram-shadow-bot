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

# Функція для отримання будівель поблизу за Overpass API
def get_nearby_buildings(lat, lon, radius=50):
    query = f"""
    [out:json];
    (
      way["building"](around:{radius},{lat},{lon});
      relation["building"](around:{radius},{lat},{lon});
    );
    out body;
    """
    response = requests.post("https://overpass-api.de/api/interpreter", data={'data': query})
    data = response.json()
    return data.get('elements', [])

# Обчислити азимут і висоту сонця у градусах
def get_sun_position(lat, lon, date_time):
    observer = Observer(latitude=lat, longitude=lon, elevation=0)
    azimuth = astral_sun.azimuth(observer, date_time)
    altitude = astral_sun.elevation(observer, date_time)
    return azimuth, altitude

# Обчислити довжину тіні будівлі
def calculate_shadow_length(height, sun_altitude_deg):
    if sun_altitude_deg <= 0:
        return None  # Сонце під горизонтом, тіні немає
    sun_altitude_rad = math.radians(sun_altitude_deg)
    return height / math.tan(sun_altitude_rad)

# Обчислити азимут між двома точками (у градусах)
def calculate_azimuth(lat1, lon1, lat2, lon2):
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    d_lon = math.radians(lon2 - lon1)
    x = math.sin(d_lon) * math.cos(lat2_rad)
    y = math.cos(lat1_rad)*math.sin(lat2_rad) - math.sin(lat1_rad)*math.cos(lat2_rad)*math.cos(d_lon)
    azimuth_rad = math.atan2(x, y)
    azimuth_deg = (math.degrees(azimuth_rad) + 360) % 360
    return azimuth_deg

# Обчислити відстань між двома точками (метри, за формулою Хаверсин)
def haversine_distance(lat1, lon1, lat2, lon2):
    R = 6371000  # радіус Землі в метрах
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(d_lambda/2)**2
    c = 2*math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# Перевірити, чи точка (машина) в тіні будівлі
def point_in_shadow(building_center_lat, building_center_lon, car_lat, car_lon, shadow_length, shadow_direction):
    # Обчислити азимут від будівлі до машини
    azimuth_to_car = calculate_azimuth(building_center_lat, building_center_lon, car_lat, car_lon)
    # Обчислити відстань
    dist = haversine_distance(building_center_lat, building_center_lon, car_lat, car_lon)
    # Вважаємо, що тінь "розкинута" в секторі ±30° від напрямку тіні
    angle_diff = min(abs(azimuth_to_car - shadow_direction), 360 - abs(azimuth_to_car - shadow_direction))
    if dist <= shadow_length and angle_diff <= 30:
        return True
    return False

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[KeyboardButton('Запаркувався - скинути локацію', request_location=True)]]
    await update.message.reply_text(
        'Натисни кнопку, щоб надіслати свою локацію:',
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude
    now = datetime.now(timezone.utc)

    # Отримуємо сонячні параметри
    sun_azimuth, sun_altitude = get_sun_position(lat, lon, now)

    if sun_altitude <= 0:
        await update.message.reply_text("Сонце зараз під горизонтом, тіні від сонця немає.")
        return

    # Отримуємо будівлі поблизу
    buildings = get_nearby_buildings(lat, lon, radius=50)
    if not buildings:
        await update.message.reply_text("Не вдалося знайти будівлі поблизу для аналізу тіні.")
        return

    # Перевіряємо кожну будівлю
    in_shadow = False
    shadow_building_id = None
    shadow_time_min = None  # Для простоти поки не розраховуємо час тіні
    for b in buildings:
        # Отримуємо центр будівлі (приблизно середина контуру)
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

        # Висота будівлі (якщо є), інакше 10 м
        height = None
        tags = b.get('tags', {})
        if 'height' in tags:
            try:
                height = float(tags['height'].replace('m','').strip())
            except:
                height = 10
        elif 'building:levels' in tags:
            try:
                height = float(tags['building:levels']) * 3  # приблизно 3 м на поверх
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
        # Для простоти повідомимо, коли буде захід сонця (коли тіні не буде)
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
    token = os.getenv('BOT_TOKEN')
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(MessageHandler(filters.LOCATION, location_handler))
    print("Бот запущено...")
    app.run_polling()
