# Wild Encounters for NVDA: an unofficial Pokémon fan add-on.
# Copyright (C) 2026 Rafli. Ported from the Wild Encounters extension for Hariku V2.
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License, version 2 or (at your option) any later version.
# Distributed WITHOUT ANY WARRANTY. See COPYING.txt.
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
What it is like outside, and which kinds of Pokémon that brings out. Pure
logic: no wx, no network, no translated text.

  weather_kind(code)        an Open-Meteo (WMO) weather code -> "rain", ...
  time_of_day(local, ...)   "dawn", "day", "dusk" or "night"
  moon_phase(when)          the Moon's phase, worked out here (no network)
  Conditions                all of that for one moment at one place
  type_weights(conditions)  {type: weight}: Wild Encounters' own mapping from the
                            conditions to Pokémon types (see below)
  special_occasion(c)       "birthday", "full_moon" or None: the only times
                            a legendary or mythical Pokémon may appear

The mapping (weights add up; a type with a bigger weight is likelier):

  clear sky       Grass 3, Fire 2, Ground, Bug, Normal, Flying 1
  a few clouds    Normal 3, Flying 2, Grass 2, Bug, Fairy 1
  overcast        Normal, Rock, Fighting, Poison 2
  fog             Ghost 3, Dark 2, Psychic 1
  drizzle         Water 3, Bug 2, Grass 1
  rain            Water 4, Bug 2, Electric 1
  heavy rain      Water 4, Electric 2, Bug 1
  thunderstorm    Electric 5, Water, Dragon 1
  snow            Ice 5, Steel 2
  weather unknown Normal 2, Grass, Flying, Bug 1 (offline: time and moon only)
then, added on top:
  windy (30 km/h or more)    Flying +3, Dragon +1, Psychic +1
  hot (30 °C or more)        Fire +2, Ground +1
  very cold (3 °C or less)   Ice +3, Steel +1
  dawn                       Flying, Normal, Fairy +1
  dusk                       Ghost, Dark, Psychic +1
  night                      Dark +3, Ghost +2, and the daytime kinds (Grass,
                             Fire, Ground, Normal, Bug) count half
  a full moon, dusk or night Fairy +4, Psychic +1
"""

import datetime
import math

TYPES = ("normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison",
         "ground", "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel",
         "fairy")
WEATHER_KINDS = ("clear", "partly_cloudy", "cloudy", "fog", "drizzle", "rain", "heavy_rain",
                 "thunderstorm", "snow")
TIMES_OF_DAY = ("dawn", "day", "dusk", "night")

WINDY_KMH = 30.0
HOT_C = 30.0
COLD_C = 3.0
TWILIGHT_MINUTES = 45          # dawn and dusk: this long either side of sunrise and sunset

# Without sunrise and sunset (offline), the hours of the place's own clock.
FALLBACK_DAWN = (5 * 60, 7 * 60)
FALLBACK_DUSK = (17 * 60 + 30, 19 * 60)

# The Moon: the mean synodic month and a new moon to count from (6 January
# 2000, 18:14 UTC). Good to about half a day, plenty for "a full moon night".
SYNODIC_MONTH = 29.530588853
NEW_MOON_EPOCH = datetime.datetime(2000, 1, 6, 18, 14, tzinfo=datetime.timezone.utc)
FULL_MOON_DAYS = 1.1           # a full moon is "full" this long either side
MOON_PHASES = (                # (up to this fraction of the month, name)
    (0.0339, "new"), (0.2161, "waxing_crescent"), (0.2839, "first_quarter"),
    (0.4661, "waxing_gibbous"), (0.5339, "full"), (0.7161, "waning_gibbous"),
    (0.7839, "last_quarter"), (0.9661, "waning_crescent"), (1.0, "new"),
)

_WEATHER_CODES = {
    0: "clear", 1: "clear", 2: "partly_cloudy", 3: "cloudy",
    45: "fog", 48: "fog",
    51: "drizzle", 53: "drizzle", 55: "drizzle", 56: "drizzle", 57: "drizzle",
    61: "rain", 63: "rain", 65: "heavy_rain", 66: "rain", 67: "heavy_rain",
    80: "rain", 81: "rain", 82: "heavy_rain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow", 85: "snow", 86: "snow",
    95: "thunderstorm", 96: "thunderstorm", 99: "thunderstorm",
}

BASE_WEIGHTS = {
    "clear": {"grass": 3, "fire": 2, "ground": 1, "bug": 1, "normal": 1, "flying": 1},
    "partly_cloudy": {"normal": 3, "flying": 2, "grass": 2, "bug": 1, "fairy": 1},
    "cloudy": {"normal": 2, "rock": 2, "fighting": 2, "poison": 2},
    "fog": {"ghost": 3, "dark": 2, "psychic": 1},
    "drizzle": {"water": 3, "bug": 2, "grass": 1},
    "rain": {"water": 4, "bug": 2, "electric": 1},
    "heavy_rain": {"water": 4, "electric": 2, "bug": 1},
    "thunderstorm": {"electric": 5, "water": 1, "dragon": 1},
    "snow": {"ice": 5, "steel": 2},
    None: {"normal": 2, "grass": 1, "flying": 1, "bug": 1},
}
WINDY_BONUS = {"flying": 3, "dragon": 1, "psychic": 1}
HOT_BONUS = {"fire": 2, "ground": 1}
COLD_BONUS = {"ice": 3, "steel": 1}
TIME_BONUS = {
    "dawn": {"flying": 1, "normal": 1, "fairy": 1},
    "day": {},
    "dusk": {"ghost": 1, "dark": 1, "psychic": 1},
    "night": {"dark": 3, "ghost": 2},
}
DAYTIME_KINDS = ("grass", "fire", "ground", "normal", "bug")     # halved at night
FULL_MOON_BONUS = {"fairy": 4, "psychic": 1}


def weather_kind(code):
    """The kind of weather an Open-Meteo weather code means, or None."""
    if code is None or isinstance(code, bool):
        return None
    try:
        return _WEATHER_CODES.get(int(code))
    except (TypeError, ValueError):
        return None


def _minutes(moment):
    return moment.hour * 60 + moment.minute


def time_of_day(local, sunrise=None, sunset=None):
    """ "dawn", "day", "dusk" or "night" at `local` (a datetime on the
    place's clock). With that day's sunrise and sunset (datetimes on the same
    clock), dawn and dusk are TWILIGHT_MINUTES either side of them; without,
    fixed hours."""
    if sunrise is not None and sunset is not None and sunrise < sunset:
        twilight = datetime.timedelta(minutes=TWILIGHT_MINUTES)
        if abs(local - sunrise) <= twilight:
            return "dawn"
        if abs(local - sunset) <= twilight:
            return "dusk"
        return "day" if sunrise < local < sunset else "night"
    minute = _minutes(local)
    if FALLBACK_DAWN[0] <= minute < FALLBACK_DAWN[1]:
        return "dawn"
    if FALLBACK_DAWN[1] <= minute < FALLBACK_DUSK[0]:
        return "day"
    if FALLBACK_DUSK[0] <= minute < FALLBACK_DUSK[1]:
        return "dusk"
    return "night"


def moon_phase(when):
    """The Moon at `when` (an aware datetime; a naive one is taken as UTC):
    {"fraction": 0..1 of the month (0 new, 0.5 full), "name": "new",
    "waxing_crescent", ... "full" ..., "full": within FULL_MOON_DAYS of the
    full moon}."""
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    days = (when - NEW_MOON_EPOCH).total_seconds() / 86400.0
    fraction = (days / SYNODIC_MONTH) % 1.0
    name = next(n for limit, n in MOON_PHASES if fraction < limit)
    full = abs(fraction - 0.5) * SYNODIC_MONTH <= FULL_MOON_DAYS
    return {"fraction": fraction, "name": name, "full": full}


def illumination(fraction):
    """How much of the Moon is lit (0..1) at that fraction of the month."""
    return (1 - math.cos(2 * math.pi * fraction)) / 2


class Conditions:
    """One moment at one place. `weather` is a WEATHER_KINDS value or None
    (unknown: offline), `temperature` in °C and `wind` in km/h or None,
    `time_of_day` a TIMES_OF_DAY value, `moon` from moon_phase(), `birthday`
    whether today is the user's birthday, `place` the place's name as said
    ("Batam"), `local` the moment on the place's clock."""

    def __init__(self, weather=None, temperature=None, wind=None, time_of_day="day",
                 moon=None, birthday=False, place="", local=None):
        self.weather = weather if weather in WEATHER_KINDS else None
        self.temperature = temperature
        self.wind = wind
        self.time_of_day = time_of_day if time_of_day in TIMES_OF_DAY else "day"
        self.moon = moon or {"fraction": 0.0, "name": "new", "full": False}
        self.birthday = bool(birthday)
        self.place = place or ""
        self.local = local

    @property
    def windy(self):
        return self.wind is not None and self.wind >= WINDY_KMH

    @property
    def hot(self):
        return self.temperature is not None and self.temperature >= HOT_C

    @property
    def cold(self):
        return self.temperature is not None and self.temperature <= COLD_C

    @property
    def full_moon_night(self):
        return bool(self.moon.get("full")) and self.time_of_day in ("dusk", "night")

    def as_dict(self):
        return {"weather": self.weather, "temperature": self.temperature, "wind": self.wind,
                "time_of_day": self.time_of_day, "moon": dict(self.moon),
                "birthday": self.birthday, "place": self.place}

    def __repr__(self):
        return (f"Conditions({self.weather!r}, {self.temperature!r} C, {self.wind!r} km/h, "
                f"{self.time_of_day!r}, moon={self.moon.get('name')!r})")


def _add(weights, bonus):
    for kind, amount in bonus.items():
        weights[kind] = weights.get(kind, 0) + amount


def type_weights(conditions):
    """{type: weight > 0} for these conditions (see the module notes)."""
    weights = {k: float(v) for k, v in BASE_WEIGHTS[conditions.weather].items()}
    if conditions.windy:
        _add(weights, WINDY_BONUS)
    if conditions.hot:
        _add(weights, HOT_BONUS)
    if conditions.cold:
        _add(weights, COLD_BONUS)
    _add(weights, TIME_BONUS[conditions.time_of_day])
    if conditions.time_of_day == "night":
        for kind in DAYTIME_KINDS:
            if kind in weights:
                weights[kind] /= 2
    if conditions.full_moon_night:
        _add(weights, FULL_MOON_BONUS)
    return {k: v for k, v in weights.items() if v > 0 and k in TYPES}


def special_occasion(conditions):
    """ "birthday" on the user's birthday, "full_moon" on a full moon's dusk
    or night, else None. Only then may a legendary or mythical one appear."""
    if conditions.birthday:
        return "birthday"
    if conditions.full_moon_night:
        return "full_moon"
    return None


def sound_family(kind):
    """Which of the encounter sounds goes with a type."""
    return SOUND_FAMILIES.get(kind, "chime")


SOUND_FAMILIES = {
    "water": "water", "electric": "electric", "fire": "fire", "dragon": "fire",
    "grass": "leaf", "bug": "leaf", "poison": "leaf",
    "flying": "air", "psychic": "air",
    "ice": "sparkle", "fairy": "sparkle", "steel": "sparkle",
    "ghost": "shadow", "dark": "shadow",
    "ground": "earth", "rock": "earth",
    "normal": "chime", "fighting": "chime",
}
SOUND_NAMES = ("chime", "water", "electric", "fire", "leaf", "air", "sparkle", "shadow", "earth")
