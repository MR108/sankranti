"""
Swiss Ephemeris backed astronomical calculations for the Sankranti Number tool.

Uses pyswisseph in Moshier mode (SEFLG_MOSEPH) throughout - no ephemeris data
files needed, which keeps this deployable as a small serverless function. This
mode is still arc-second accurate for the Sun and gives the true (non-linear)
Lahiri ayanamsa, rather than the linear approximation this project used before.

All heavy astronomy (Sun/planet positions, ayanamsa, the Sankranti search,
ascendant, sunrise, Hora/Ghati Lagna) happens here. Calendar-cutoff arithmetic,
digital-root reduction and all rendering stay in the frontend - they don't need
ephemeris data and moving them would just add round trips.
"""

import json
from datetime import datetime, timedelta, timezone

import swisseph as swe

swe.set_sid_mode(swe.SIDM_LAHIRI, 0, 0)

MOSEPH = swe.FLG_MOSEPH
SIDEREAL = swe.FLG_MOSEPH | swe.FLG_SIDEREAL

BODIES = {
    "Sun": swe.SUN, "Moon": swe.MOON, "Mars": swe.MARS, "Mercury": swe.MERCURY,
    "Jupiter": swe.JUPITER, "Venus": swe.VENUS, "Saturn": swe.SATURN,
}


def jd_from_utc(dt):
    return swe.utc_to_jd(
        dt.year, dt.month, dt.day, dt.hour, dt.minute,
        dt.second + dt.microsecond / 1e6, swe.GREG_CAL,
    )[1]


def dt_from_jd(jd):
    y, m, d, hours = swe.revjul(jd, swe.GREG_CAL)
    return datetime(y, m, d, tzinfo=timezone.utc) + timedelta(hours=hours)


def iso(dt):
    return dt.isoformat().replace("+00:00", "Z")


def sun_tropical(jd):
    return swe.calc_ut(jd, swe.SUN, MOSEPH)[0][0]


def sun_sidereal(jd):
    return swe.calc_ut(jd, swe.SUN, SIDEREAL)[0][0]


def ayanamsa(jd):
    return swe.get_ayanamsa_ex_ut(jd, MOSEPH)[1]


def find_preceding_sankranti(birth_jd, target_sidereal):
    """Bisection root-find for the moment the Sun's sidereal longitude last
    crossed target_sidereal, ascending. The (x+180)%360-180 remap keeps the
    search continuous across the 0/360 wrap (e.g. the Mesha/Aries Sankranti,
    which the raw sidereal value wraps through)."""
    def diff(jd):
        return ((sun_sidereal(jd) - target_sidereal + 180) % 360) - 180

    lo, hi = birth_jd - 40, birth_jd
    if diff(lo) >= 0 or diff(hi) < 0:
        raise ValueError("Could not bracket the preceding Sankranti for this date/location.")
    for _ in range(60):
        mid = (lo + hi) / 2
        if diff(mid) < 0:
            lo = mid
        else:
            hi = mid
    return hi


def find_sunrise_before(birth_jd, tz, lat, lon):
    local_dt = dt_from_jd(birth_jd) + timedelta(hours=tz)

    def sunrise_for_local_date(dt):
        midnight_utc_jd = swe.julday(dt.year, dt.month, dt.day, 0, swe.GREG_CAL) - tz / 24.0
        res, tret = swe.rise_trans(
            midnight_utc_jd, swe.SUN, swe.CALC_RISE, (lon, lat, 0.0), flags=MOSEPH,
        )
        if res != 0:
            return None
        return tret[0]

    sunrise_jd = sunrise_for_local_date(local_dt)
    if sunrise_jd is not None and sunrise_jd > birth_jd:
        sunrise_jd = sunrise_for_local_date(local_dt - timedelta(days=1))
    if sunrise_jd is None:
        raise ValueError("Could not compute sunrise for Hora/Ghati Lagna at this location/date.")
    return sunrise_jd


def compute_chart(birth_jd, lat, lon, tz):
    positions = {name: swe.calc_ut(birth_jd, pid, SIDEREAL)[0][0] for name, pid in BODIES.items()}
    positions["Rahu"] = swe.calc_ut(birth_jd, swe.MEAN_NODE, SIDEREAL)[0][0]
    positions["Ketu"] = (positions["Rahu"] + 180) % 360

    house_lat = lat
    if abs(house_lat) >= 89.9:
        house_lat = 89.9 if house_lat > 0 else -89.9
    _, ascmc = swe.houses_ex(birth_jd, house_lat, lon, b"P", flags=SIDEREAL)
    ascendant = ascmc[0]

    sunrise_jd = find_sunrise_before(birth_jd, tz, lat, lon)
    sun_at_sunrise = sun_sidereal(sunrise_jd)
    ishta_ghatis = ((birth_jd - sunrise_jd) * 1440) / 24
    hora_lagna = (sun_at_sunrise + (ishta_ghatis / 2.5) * 30) % 360
    ghati_lagna = (sun_at_sunrise + ishta_ghatis * 30) % 360

    return {
        "positions": positions,
        "ascendant": ascendant,
        "sunriseUtc": iso(dt_from_jd(sunrise_jd)),
        "horaLagna": hora_lagna,
        "ghatiLagna": ghati_lagna,
    }


def compute(birth_utc_iso, lat, lon, tz):
    birth_dt = datetime.fromisoformat(birth_utc_iso.replace("Z", "+00:00"))
    birth_jd = jd_from_utc(birth_dt)

    sun_trop = sun_tropical(birth_jd)
    ayan_birth = ayanamsa(birth_jd)
    sun_sid = (sun_trop - ayan_birth) % 360
    rasi_index = int(sun_sid // 30)
    target_sidereal = rasi_index * 30

    sankranti_jd = find_preceding_sankranti(birth_jd, target_sidereal)
    sankranti_ayanamsa = ayanamsa(sankranti_jd)
    sankranti_sun_sid = sun_sidereal(sankranti_jd)

    elapsed_days = birth_jd - sankranti_jd
    num_samples = int(elapsed_days) + 4
    daily_samples = []
    for i in range(0, num_samples + 1):
        t_jd = sankranti_jd + i
        raw = sun_sidereal(t_jd) - target_sidereal
        if raw > 300:
            raw -= 360
        daily_samples.append({
            "dayNumber": i + 1,
            "dateUtc": iso(dt_from_jd(t_jd)),
            "degreeIntoRasi": raw,
        })

    result = {
        "sun": {"tropical": sun_trop, "sidereal": sun_sid, "ayanamsa": ayan_birth},
        "rasiIndex": rasi_index,
        "degreeIntoRasi": sun_sid - target_sidereal,
        "sankranti": {
            "utc": iso(dt_from_jd(sankranti_jd)),
            "ayanamsa": sankranti_ayanamsa,
            "sunSidereal": sankranti_sun_sid,
            "residual": sankranti_sun_sid - target_sidereal,
        },
        "dailySamples": daily_samples,
        "elapsedDays": elapsed_days,
    }

    try:
        result["chart"] = compute_chart(birth_jd, lat, lon, tz)
    except (ValueError, swe.Error) as e:
        result["chart"] = None
        result["chartError"] = str(e)

    return result


def handler(event, context):
    headers = {"Content-Type": "application/json"}
    if event.get("httpMethod") != "POST":
        return {"statusCode": 405, "headers": headers, "body": json.dumps({"error": "POST only"})}

    try:
        payload = json.loads(event.get("body") or "{}")
        birth_utc_iso = payload["birthUtcIso"]
        lat = float(payload["lat"])
        lon = float(payload["lon"])
        tz = float(payload["tz"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "Invalid request body"})}

    try:
        result = compute(birth_utc_iso, lat, lon, tz)
    except ValueError as e:
        return {"statusCode": 422, "headers": headers, "body": json.dumps({"error": str(e)})}
    except Exception as e:
        return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": f"Calculation failed: {e}"})}

    return {"statusCode": 200, "headers": headers, "body": json.dumps(result)}
