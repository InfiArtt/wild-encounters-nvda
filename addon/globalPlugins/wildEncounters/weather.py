# Wild Encounters for NVDA: an unofficial Pokémon fan add-on.
# Copyright (C) 2026 Rafli. Ported from the Wild Encounters extension for Hariku V2.
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License, version 2 or (at your option) any later version.
# Distributed WITHOUT ANY WARRANTY. See COPYING.txt.
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The weather right now at the user's main place, from Open-Meteo
(https://open-meteo.com, no account or key). Blocking: the plugin calls it on a
worker thread. No wx or NVDA here.

Privacy: the request carries the place rounded to 2 decimals (about 1 km),
never the exact point, and "timezone=auto". The answer is kept in memory for
FRESH_SECONDS (30 minutes) for that rounded point, so encounters close
together ask once; nothing is written to disk.
"""

import datetime
import http.client
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import meta

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HOST = "api.open-meteo.com"
QUERY_DECIMALS = 2
TIMEOUT = 10
MAX_BYTES = 256 * 1024
FRESH_SECONDS = 30 * 60
CURRENT_FIELDS = ("temperature_2m", "weather_code", "wind_speed_10m", "is_day")
DAILY_FIELDS = ("sunrise", "sunset")


class WeatherError(Exception):
    """kind: "offline", "http", "bad_data", "host"."""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


def user_agent():
    return meta.USER_AGENT


def query_point(latitude, longitude):
    """The rounded point that is sent instead of the exact one."""
    return round(float(latitude), QUERY_DECIMALS), round(float(longitude), QUERY_DECIMALS)


def build_url(latitude, longitude):
    # Always rounded here, so an exact point can never reach the URL.
    lat, lon = query_point(latitude, longitude)
    params = {
        "latitude": f"{lat:.{QUERY_DECIMALS}f}",
        "longitude": f"{lon:.{QUERY_DECIMALS}f}",
        "current": ",".join(CURRENT_FIELDS),
        "daily": ",".join(DAILY_FIELDS),
        "timezone": "auto",
        "forecast_days": 1,
    }
    return FORECAST_URL + "?" + urllib.parse.urlencode(params)


def _open(url, timeout=TIMEOUT):
    request = urllib.request.Request(url, headers={"User-Agent": user_agent(),
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_BYTES + 1), response.geturl()


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _moment(text):
    """An Open-Meteo local time ("2026-10-03T15:45") -> naive datetime, or None."""
    try:
        return datetime.datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None


def _first(block, name):
    values = block.get(name) if isinstance(block, dict) else None
    return values[0] if isinstance(values, list) and values else None


def parse(payload):
    """Open-Meteo's answer -> {"code", "temperature" (°C), "wind" (km/h),
    "is_day", "local" (the quarter hour it is for, on the place's clock),
    "utc_offset" (seconds, or None), "sunrise", "sunset" (naive datetimes on
    that clock, or None)}. Raises WeatherError."""
    if not isinstance(payload, dict) or not isinstance(payload.get("current"), dict):
        raise WeatherError("bad_data", "no current conditions")
    current = payload["current"]
    code = _number(current.get("weather_code"))
    local = _moment(current.get("time"))
    if code is None or local is None:
        raise WeatherError("bad_data", "no weather code")
    daily = payload.get("daily") if isinstance(payload.get("daily"), dict) else {}
    is_day = current.get("is_day")
    offset = _number(payload.get("utc_offset_seconds"))
    return {
        "code": int(code),
        "temperature": _number(current.get("temperature_2m")),
        "wind": _number(current.get("wind_speed_10m")),
        "is_day": None if is_day is None else bool(_number(is_day)),
        "local": local,
        "utc_offset": int(offset) if offset is not None and abs(offset) <= 15 * 3600 else None,
        "sunrise": _moment(_first(daily, "sunrise")),
        "sunset": _moment(_first(daily, "sunset")),
    }


class WeatherClient:
    """`opener(url)` -> (body, final url); `clock()` the wall clock."""

    def __init__(self, opener=None, clock=time.time):
        self._opener = opener or _open
        self._clock = clock
        self._lock = threading.Lock()
        self._cache = {}        # rounded point -> (fetched_at, parsed)
        self.sent = []

    def current(self, latitude, longitude):
        """The weather now at (the rounded) point. Raises WeatherError."""
        point = query_point(latitude, longitude)
        with self._lock:
            kept = self._cache.get(point)
            if kept and 0 <= self._clock() - kept[0] <= FRESH_SECONDS:
                return dict(kept[1])
        url = build_url(*point)
        self.sent.append(url)
        try:
            body, final = self._opener(url)
        except urllib.error.HTTPError as e:
            raise WeatherError("http", str(e.code)) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
            raise WeatherError("offline", str(getattr(e, "reason", e))) from None
        if urllib.parse.urlsplit(str(final)).hostname != HOST:
            raise WeatherError("host", str(final))
        if len(body) > MAX_BYTES:
            raise WeatherError("bad_data", "too large")
        try:
            parsed = parse(json.loads(body.decode("utf-8")))
        except (UnicodeDecodeError, ValueError) as e:
            raise WeatherError("bad_data", str(e)) from None
        with self._lock:
            self._cache[point] = (self._clock(), parsed)
        return dict(parsed)

    def cached(self, latitude, longitude):
        """The weather kept for that point when it is still fresh, else None
        (never asks)."""
        point = query_point(latitude, longitude)
        with self._lock:
            kept = self._cache.get(point)
            if kept and 0 <= self._clock() - kept[0] <= FRESH_SECONDS:
                return dict(kept[1])
        return None

    def forget(self):
        with self._lock:
            self._cache.clear()
