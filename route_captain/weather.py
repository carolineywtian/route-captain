"""Weather for the run and what to wear (code, not the LLM).

Forecast: Open-Meteo (free, no API key), hourly, up to 16 days ahead.
Clothing: the common runner's rule "dress for 15-20°F warmer than it feels", turned
into temperature bands, plus wind, rain, sun and darkness adjustments.
Heat: the temperature + dew point rule of thumb runners use to slow their pace.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache

import requests

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 16
HOURLY = ("temperature_2m,apparent_temperature,dew_point_2m,precipitation_probability,"
          "precipitation,wind_speed_10m,wind_gusts_10m,uv_index,weather_code")

WEATHER_CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "rain showers", 82: "heavy rain showers", 85: "snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}
THUNDER = {95, 96, 99}

# (upper bound of "feels like" °F, clothing for that band)
CLOTHING_BANDS = [
    (10, ["Thermal base layer + insulated running jacket", "Running tights with wind pants over them",
          "Warm hat or balaclava", "Insulated gloves or mittens", "Wool socks"]),
    (20, ["Thermal long-sleeve + windproof jacket", "Thermal tights", "Hat that covers your ears",
          "Warm gloves", "Wool socks"]),
    (30, ["Long-sleeve base layer + light jacket", "Tights", "Headband or light hat", "Gloves"]),
    (40, ["Long-sleeve shirt (add a light vest if windy)", "Tights or capris", "Light gloves",
          "Headband if your ears get cold"]),
    (50, ["Long sleeves or a t-shirt with arm sleeves", "Shorts or capris",
          "Light gloves you can tuck away"]),
    (60, ["T-shirt or singlet", "Shorts"]),
    (70, ["Singlet or light t-shirt", "Shorts", "Sunglasses if sunny"]),
    (200, ["Singlet or sports bra", "Lightest shorts", "Hat or visor", "Sunglasses",
           "Carry water"]),
]

# Temperature °F + dew point °F -> how much slower to run (Hadley's runner chart)
HEAT_TABLE = [(100, 0.0), (110, 0.005), (120, 0.01), (130, 0.02), (140, 0.03),
              (150, 0.045), (160, 0.06), (170, 0.08), (180, 0.10)]


def heat_slowdown(temp_f: float, dew_point_f: float) -> float:
    """Fraction slower to run for the same effort (0.03 = 3% slower). >180 is not advised."""
    total = temp_f + dew_point_f
    for limit, slow in HEAT_TABLE:
        if total <= limit:
            return slow
    return 0.12


def what_to_wear(feels_like_f: float, wind_mph: float = 0, rain_chance: float = 0,
                 uv: float = 0, dark: bool = False) -> list[str]:
    """Clothing list for the conditions (bands already include the dress-warmer rule)."""
    items = next(list(c) for limit, c in CLOTHING_BANDS if feels_like_f < limit)
    if wind_mph >= 12 and feels_like_f < 60:
        items.append("Windbreaker or vest: it's breezy")
    if rain_chance >= 50:
        items.append("Light water-resistant shell and a cap to keep rain off your face")
    if uv >= 6 and not dark:
        items.append("Sunscreen: UV is high")
    if dark:
        items.append("Reflective vest or bright colors and a headlamp or clip light")
    return items


@lru_cache(maxsize=64)
def _fetch(lat: float, lon: float, day: str) -> dict | None:
    try:
        resp = requests.get(FORECAST_URL, timeout=8, params={
            "latitude": lat, "longitude": lon, "hourly": HOURLY,
            "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch",
            "timezone": "auto", "start_date": day, "end_date": day})  # hours in the run location's local time
        resp.raise_for_status()
        return resp.json().get("hourly")
    except (requests.RequestException, ValueError):
        return None


def forecast_for_run(lat: float, lon: float, start: datetime, minutes: float, dark: bool = False) -> dict:
    """Conditions over the run window, the outfit, the heat slowdown and any weather warnings."""
    now = datetime.now()
    if start < now - timedelta(hours=1) or start > now + timedelta(days=FORECAST_DAYS - 1):
        return {"available": False,
                "reason": "The forecast only covers the next 16 days, so dress for the day-of weather."}
    hourly = _fetch(round(lat, 2), round(lon, 2), start.strftime("%Y-%m-%d"))
    if not hourly:
        return {"available": False, "reason": "Couldn't reach the weather service."}

    end = start + timedelta(minutes=minutes)
    hours = [i for i, t in enumerate(hourly["time"])
             if start.replace(minute=0) <= datetime.fromisoformat(t) <= end]
    hours = hours or [min(range(len(hourly["time"])),
                          key=lambda i: abs(datetime.fromisoformat(hourly["time"][i]) - start))]

    def pick(key, fn=max):
        vals = [hourly[key][i] for i in hours if hourly[key][i] is not None]
        return fn(vals) if vals else None

    temp, temp_end = hourly["temperature_2m"][hours[0]], hourly["temperature_2m"][hours[-1]]
    feels = pick("apparent_temperature", min)
    dew = pick("dew_point_2m")
    wind, gusts = pick("wind_speed_10m"), pick("wind_gusts_10m")
    rain = pick("precipitation_probability") or 0
    uv = pick("uv_index") or 0
    codes = [hourly["weather_code"][i] for i in hours]
    worst = max(codes, key=lambda c: (c in THUNDER, c))
    slow = heat_slowdown(max(temp, temp_end), dew) if dew is not None else 0.0

    warnings = []
    if any(c in THUNDER for c in codes):
        warnings.append("Thunderstorms are forecast: run indoors or move your run. Lightning is dangerous in the open.")
    if max(temp, temp_end) + (dew or 0) > 160:
        warnings.append("Heat and humidity are high: slow down a lot, carry water and watch for heat illness.")
    if feels is not None and feels <= 0:
        warnings.append("Dangerous cold: cover all skin and consider a shorter run.")
    if worst in (56, 57, 66, 67):
        warnings.append("Freezing rain or drizzle: sidewalks may be icy.")

    return {
        "available": True,
        "summary": WEATHER_CODES.get(worst, "mixed conditions"),
        "temp_f": round(temp), "temp_end_f": round(temp_end),
        "feels_like_f": round(feels) if feels is not None else round(temp),
        "dew_point_f": round(dew) if dew is not None else None,
        "wind_mph": round(wind or 0), "gusts_mph": round(gusts or 0),
        "rain_chance": round(rain), "uv_index": round(uv, 1),
        "heat_slowdown": slow,
        "what_to_wear": what_to_wear(feels if feels is not None else temp, wind or 0, rain, uv, dark),
        "warnings": warnings,
    }
