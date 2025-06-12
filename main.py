from astral import LocationInfo
from astral.sun import sun, azimuth
from timezonefinder import TimezoneFinder
from datetime import datetime, timedelta
from math import atan2, degrees
import pytz


def check_shadow(lat, lon, sun_azimuth, nearby_objects):
    """
    Перевіряє, чи буде тінь від об'єктів поблизу в напрямку сонця.
    """
    for obj in nearby_objects:
        dx = obj['lon'] - lon
        dy = obj['lat'] - lat
        if dx == 0 and dy == 0:
            continue

        angle = (degrees(atan2(dx, dy)) + 360) % 360
        dist = ((dx)**2 + (dy)**2)**0.5 * 111000  # прибл. у метрах
        angle_diff = abs((angle - sun_azimuth + 180) % 360 - 180)

        if dist < 25 and angle_diff < 20 and obj['type'] in ['building', 'tree']:
            return True
    return False


def generate_sun_shadow_slots(lat: float, lon: float, nearby_objects: list):
    """
    Повертає список таймслотів з іконкою сонце/тінь на основі геолокації та об'єктів поруч.
    """
    # Визначення часового поясу
    tf = TimezoneFinder()
    timezone_str = tf.timezone_at(lat=lat, lng=lon) or "UTC"
    tz = pytz.timezone(timezone_str)

    # Параметри локації
    location = LocationInfo(latitude=lat, longitude=lon)
    observer = location.observer
    today = datetime.now(tz).date()
    s = sun(observer, date=today, tzinfo=tz)

    start_time = s['sunrise'] - timedelta(minutes=30)
    end_time = s['sunset'] + timedelta(minutes=30)

    # Генерація таймслотів
    slots = []
    dt = start_time
    while dt <= end_time:
        az = azimuth(observer, dt)
        in_shadow = check_shadow(lat, lon, az, nearby_objects)
        label = "🌑 Тінь" if in_shadow else "☀️ Сонце"
        slot = f"{label} {dt.strftime('%H:%M')} — {(dt + timedelta(minutes=15)).strftime('%H:%M')}"
        slots.append(slot)
        dt += timedelta(minutes=15)

    return "\n".join(slots)


# === 🔍 ПРИКЛАД ВИКОРИСТАННЯ ===
if __name__ == "__main__":
    latitude = 50.4501
    longitude = 30.5234  # Київ

    # Умовні об'єкти поблизу
    nearby_objects = [
        {'type': 'building', 'lat': 50.4504, 'lon': 30.5236},
        {'type': 'tree', 'lat': 50.4499, 'lon': 30.5231}
    ]

    print(generate_sun_shadow_slots(latitude, longitude, nearby_objects))