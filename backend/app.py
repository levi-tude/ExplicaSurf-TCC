from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import os, time, datetime, httpx
from typing import Any, Dict, Optional, List, Any as TypingAny

# Load environment variables from .env file if present
load_dotenv()

app = Flask(__name__)
CORS(app)

# Coordinates for the spot (defaults to Stella Maris, Salvador-BA)
LAT = float(os.getenv("LAT", "-12.9437"))
LON = float(os.getenv("LON", "-38.3539"))

# API keys from .env (empty strings if not provided)
STORMGLASS_API_KEY = os.getenv("STORMGLASS_API_KEY") or ""
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY") or ""
TIDE_API_URL = os.getenv("TIDE_API_URL") or ""
TIDE_LOCATION = os.getenv("TIDE_LOCATION", "Salvador")

# Simple in-memory cache with TTL to avoid excessive API calls
class TTLCache:
    def __init__(self, ttl_seconds: int = 300):
        self.ttl = ttl_seconds
        self.store: Dict[str, Any] = {}

    def get(self, key: str) -> Optional[TypingAny]:
        item = self.store.get(key)
        if not item:
            return None
        ts, value = item
        if time.time() - ts > self.ttl:
            # Expired entry
            self.store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: TypingAny) -> None:
        self.store[key] = (time.time(), value)

# Instantiate cache (3 minute TTL)
cache = TTLCache(ttl_seconds=180)

# Helper functions for time parsing
def parse_iso_list(iso_list: List[str]) -> List[datetime.datetime]:
    """Convert list of ISO8601 strings to datetime objects."""
    return [datetime.datetime.fromisoformat(t) for t in iso_list]

def nearest_index(times: List[datetime.datetime], now: Optional[datetime.datetime] = None) -> int:
    """Return index of time closest to now."""
    if not times:
        return 0
    if now is None:
        now = datetime.datetime.now()
    return min(range(len(times)), key=lambda i: abs(times[i] - now))

# ---------- Open-Meteo Marine API ----------
def fetch_open_meteo(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Fetch hourly marine data from Open-Meteo."""
    key = f"openmeteo:{lat:.4f},{lon:.4f}"
    cached = cache.get(key)
    if cached:
        return cached
    url = "https://marine-api.open-meteo.com/v1/marine"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "wave_height,wave_period,wave_direction,wind_wave_height,wind_wave_period,wind_wave_direction",
        "length_unit": "metric",
        "timezone": "auto",
    }
    try:
        r = httpx.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        cache.set(key, data)
        return data
    except Exception:
        return None

def pick_open_meteo_point(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pick the forecast hour closest to now from Open-Meteo data."""
    try:
        times_iso = data["hourly"]["time"]
        times = parse_iso_list(times_iso)
        idx = nearest_index(times)
        return {
            "time": times_iso[idx],
            "wave_height_m": data["hourly"]["wave_height"][idx],
            "wave_period_s": data["hourly"]["wave_period"][idx],
            "wave_direction_deg": data["hourly"]["wave_direction"][idx],
            "wind_wave_height_m": data["hourly"]["wind_wave_height"][idx],
            "wind_wave_period_s": data["hourly"]["wind_wave_period"][idx],
            "wind_wave_direction_deg": data["hourly"]["wind_wave_direction"][idx],
            "source": "open-meteo",
        }
    except Exception:
        return None

# ---------- Stormglass API ----------
def fetch_stormglass(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Fetch swell, wind and wave data from Stormglass (if API key provided)."""
    if not STORMGLASS_API_KEY:
        return None
    key = f"stormglass:{lat:.4f},{lon:.4f}"
    cached = cache.get(key)
    if cached:
        return cached
    url = "https://api.stormglass.io/v2/weather/point"
    params = {
        "lat": lat,
        "lng": lon,
        "params": ",".join([
            "waveHeight", "wavePeriod", "waveDirection",
            "swellHeight", "swellPeriod", "swellDirection",
            "windSpeed", "windDirection",
        ]),
    }
    headers = {"Authorization": STORMGLASS_API_KEY}
    try:
        r = httpx.get(url, params=params, headers=headers, timeout=15)
        if r.status_code == 429:
            return None
        r.raise_for_status()
        data = r.json()
        cache.set(key, data)
        return data
    except Exception:
        return None

def pick_stormglass_point(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Pick the hour closest to now and normalise Stormglass data."""
    try:
        hours = data.get("hours", [])
        if not hours:
            return None
        ts = [datetime.datetime.fromisoformat(h["time"].replace("Z", "+00:00")).astimezone() for h in hours]
        idx = nearest_index(ts)
        def choose(src_dict: Dict[str, Any]) -> Optional[float]:
            if not isinstance(src_dict, dict):
                return None
            for pref in ["noaa", "dwd", "meteo", "icon", "sg"]:
                if pref in src_dict and isinstance(src_dict[pref], (int, float)):
                    return float(src_dict[pref])
            for v in src_dict.values():
                if isinstance(v, (int, float)):
                    return float(v)
            return None
        h = hours[idx]
        wave_height = choose(h.get("waveHeight", {}))
        wave_period = choose(h.get("wavePeriod", {}))
        wave_direction = choose(h.get("waveDirection", {}))
        swell_height = choose(h.get("swellHeight", {}))
        swell_period = choose(h.get("swellPeriod", {}))
        swell_direction = choose(h.get("swellDirection", {}))
        wind_speed_ms = choose(h.get("windSpeed", {}))
        wind_direction = choose(h.get("windDirection", {}))
        wind_speed_kmh = float(wind_speed_ms) * 3.6 if wind_speed_ms is not None else None
        return {
            "time": h["time"],
            "wave_height_m": wave_height,
            "wave_period_s": wave_period,
            "wave_direction_deg": wave_direction,
            "swell_height_m": swell_height,
            "swell_period_s": swell_period,
            "swell_direction_deg": swell_direction,
            "wind_speed_kmh": wind_speed_kmh,
            "wind_direction_deg": wind_direction,
            "source": "stormglass",
        }
    except Exception:
        return None

# ---------- OpenWeather API ----------
def fetch_openweather(lat: float, lon: float) -> Optional[Dict[str, Any]]:
    """Fetch current weather (wind, clouds, rain, temp) from OpenWeather (if API key provided)."""
    if not OPENWEATHER_API_KEY:
        return None
    key = f"openweather:{lat:.4f},{lon:.4f}"
    cached = cache.get(key)
    if cached:
        return cached
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "lat": lat,
        "lon": lon,
        "appid": OPENWEATHER_API_KEY,
        "units": "metric",
    }
    try:
        r = httpx.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        cache.set(key, data)
        return data
    except Exception:
        return None

def pick_openweather_now(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Extract relevant fields from OpenWeather current data."""
    try:
        wind_ms = data.get("wind", {}).get("speed")
        wind_deg = data.get("wind", {}).get("deg")
        clouds = data.get("clouds", {}).get("all")
        temp_c = data.get("main", {}).get("temp")
        rain_1h = data.get("rain", {}).get("1h", 0.0)
        return {
            "wind_speed_kmh": float(wind_ms) * 3.6 if isinstance(wind_ms, (int, float)) else None,
            "wind_direction_deg": wind_deg,
            "cloud_cover_pct": clouds,
            "temp_c": temp_c,
            "rain_mm_1h": rain_1h,
            "source": "openweather",
        }
    except Exception:
        return None

# ---------- Tide API (Tabua-Mare) ----------
def fetch_tide(location: str) -> Optional[Any]:
    """Fetch tide information from API-Tabua-Mare."""
    if not TIDE_API_URL:
        return None
    key = f"tide:{location}"
    cached = cache.get(key)
    if cached:
        return cached
    try:
        # Corrigido: a API usa o nome da cidade no path
        url = f"{TIDE_API_URL}/{location}"
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        cache.set(key, data)
        return data
    except Exception as e:
        print("Erro ao buscar maré:", e)
        return None

# ---------- Merging logic ----------
def merge_forecast(
    om_point: Optional[Dict[str, Any]],
    sg_point: Optional[Dict[str, Any]],
    ow_now: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Merge data from different sources.
    Waves/swell: prefer Stormglass if available, else Open-Meteo.
    Wind: prefer OpenWeather, else Stormglass.
    """
    merged: Dict[str, Any] = {"time": None, "sources": {}}
    # Reference time
    if sg_point and sg_point.get("time"):
        merged["time"] = sg_point["time"]
    elif om_point and om_point.get("time"):
        merged["time"] = om_point["time"]
    # Waves
    if sg_point:
        merged["wave_height_m"] = sg_point.get("wave_height_m")
        merged["wave_period_s"] = sg_point.get("wave_period_s")
        merged["wave_direction_deg"] = sg_point.get("wave_direction_deg")
        merged["swell_height_m"] = sg_point.get("swell_height_m")
        merged["swell_period_s"] = sg_point.get("swell_period_s")
        merged["swell_direction_deg"] = sg_point.get("swell_direction_deg")
        merged["sources"]["waves"] = "stormglass"
    elif om_point:
        merged["wave_height_m"] = om_point.get("wave_height_m")
        merged["wave_period_s"] = om_point.get("wave_period_s")
        merged["wave_direction_deg"] = om_point.get("wave_direction_deg")
        merged["swell_height_m"] = om_point.get("wind_wave_height_m")
        merged["swell_period_s"] = om_point.get("wind_wave_period_s")
        merged["swell_direction_deg"] = om_point.get("wind_wave_direction_deg")
        merged["sources"]["waves"] = "open-meteo"
    # Wind
    if ow_now and ow_now.get("wind_speed_kmh") is not None:
        merged["wind_speed_kmh"] = ow_now.get("wind_speed_kmh")
        merged["wind_direction_deg"] = ow_now.get("wind_direction_deg")
        merged["sources"]["wind"] = "openweather"
    elif sg_point and sg_point.get("wind_speed_kmh") is not None:
        merged["wind_speed_kmh"] = sg_point.get("wind_speed_kmh")
        merged["wind_direction_deg"] = sg_point.get("wind_direction_deg")
        merged["sources"]["wind"] = "stormglass"
    return merged

# ---------- Explanation generator ----------
def explain(level: str, merged: Dict[str, Any]) -> str:
    """Create a simple Portuguese explanation based on the user's level."""
    h = merged.get("wave_height_m", 0.0)
    p = merged.get("wave_period_s", 0.0)
    w = merged.get("wind_speed_kmh", 0.0)
    d = merged.get("wind_direction_deg", 0)
    if level == "iniciante":
        return (
            f"O mar está com ~{h:.1f} m e período de {p:.0f}s. "
            f"Vento {w:.0f} km/h ({d}°). Priorize período >10s e vento fraco."
        )
    if level == "intermediario":
        return (
            f"Altura {h:.1f} m; Tp {p:.0f}s; vento {w:.0f} km/h @{d}°. "
            "Se o vento girar terral, melhora bastante."
        )
    # avançado
    return (
        f"Hs={h:.1f} m, Tp={p:.0f}s, W={w:.0f} km/h@{d}°. "
        "Combine swell+vento na escolha do pico/horário."
    )

# ---------- Flask routes ----------
@app.get("/api/explain")
def api_explain():
    level = (request.args.get("level") or "iniciante").lower()
    if level not in {"iniciante", "intermediario", "avancado"}:
        return jsonify({"error": "level inválido. Use: iniciante|intermediario|avancado"}), 400
    om_raw = fetch_open_meteo(LAT, LON)
    om_point = pick_open_meteo_point(om_raw) if om_raw else None
    sg_raw = fetch_stormglass(LAT, LON)
    sg_point = pick_stormglass_point(sg_raw) if sg_raw else None
    ow_raw = fetch_openweather(LAT, LON)
    ow_now = pick_openweather_now(ow_raw) if ow_raw else None
    merged = merge_forecast(om_point, sg_point, ow_now)
    tide = fetch_tide(TIDE_LOCATION)
    explanation_pt = explain(level, merged) if merged.get("wave_height_m") else "Sem dados suficientes no momento."
    return jsonify({
        "spot": "Stella Maris, Salvador-BA",
        "level": level,
        "time_ref": merged.get("time"),
        "merged": merged,
        "open_meteo": om_point,
        "stormglass": sg_point,
        "openweather": ow_now,
        "tide": tide,
        "explanation_pt": explanation_pt,
    })

@app.get("/api/tide")
def api_tide():
    """Endpoint para testar apenas a maré"""
    data = fetch_tide(TIDE_LOCATION)
    return jsonify(data or {"error": "Sem dados de maré"})

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
