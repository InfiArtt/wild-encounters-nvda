# Wild Encounters for NVDA: an unofficial Pokémon fan add-on.
# Copyright (C) 2026 Rafli. Ported from the Wild Encounters extension for Hariku V2.
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License, version 2 or (at your option) any later version.
# Distributed WITHOUT ANY WARRANTY. See COPYING.txt.
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
When a Pokémon may appear, so it can never be spammed. Pure logic: no wx, no
NVDA, no network.

Looking around yourself (the W command): once every 20 minutes
(MANUAL_COOLDOWN). The wait starts only when someone was actually met, so a try that failed
(offline) doesn't cost anything. It is kept on disk, so restarting NVDA doesn't
start it again. While it runs, the command only says how long is left: it
asks nothing of PokéAPI or Open-Meteo.

By themselves (when turned on): every CHECK_EVERY (30 minutes) outside quiet
hours the weather at home is looked at (Open-Meteo, kept for 30 minutes), and
the weather decides whether someone comes by:
  * each kind of weather has its own chance (CHANCE): small on a plain
    sunny or cloudy day, bigger in rain, fog, snow and storms;
  * a change is the big moment (CHANGE_BONUS): the rain just started, the
    rain just stopped, fog rolled in, a storm began;
  * dawn and dusk, a full moon night and a strong wind add a little.
Never more than PER_DAY a day (the "at most" setting), never within MIN_GAP
(90 minutes) of the last encounter of any kind. When the chance says yes, the
encounter waits for a pause (the plugin checks that nobody is typing or
listening to a long read), for at most PENDING_LIMIT; then it is dropped.

The state, saved as "pacing.json": {"date", "count" (by themselves today),
"seen" (species met today, so none comes twice a day), "last_any",
"last_manual", "last_check" (wall-clock seconds), "last_weather",
"last_time_of_day", "pending" (since when one waits for a pause, or 0)}.
"""

import datetime
import random
import time

MANUAL_COOLDOWN = 20 * 60
CHECK_EVERY = 30 * 60
MIN_GAP = 90 * 60
PENDING_LIMIT = 20 * 60
CHANGE_MEMORY = 2 * 60 * 60     # an older reading doesn't count as "before"
MAX_SEEN = 50

FREQUENCIES = ("rare", "normal", "often")
PER_DAY = {"rare": 1, "normal": 2, "often": 3}
DEFAULT_FREQUENCY = "normal"

CHANCE = {
    "clear": 0.04, "partly_cloudy": 0.04, "cloudy": 0.04,
    "fog": 0.20, "drizzle": 0.12, "rain": 0.15, "heavy_rain": 0.15,
    "thunderstorm": 0.30, "snow": 0.25,
    None: 0.03,                                    # the weather couldn't be had
}
WET = ("fog", "drizzle", "rain", "heavy_rain", "thunderstorm", "snow")
CHANGE_BONUS = 0.35
TWILIGHT_BONUS = 0.08
FULL_MOON_BONUS = 0.12
WINDY_BONUS = 0.05
MAX_CHANCE = 0.8


def is_quiet(moment, start_hour, end_hour):
    """Whether `moment` (a datetime) is in the quiet hours from start_hour
    to end_hour (they may wrap past midnight). The same hour twice: none."""
    if start_hour == end_hour:
        return False
    hour = moment.hour
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def weather_changed(before, now):
    """A change worth coming out for: into or out of wet weather, or from
    one wet kind to another (rain into a storm)."""
    if before == now or before is None or now is None:
        return False
    return before in WET or now in WET


def chance(conditions, before_weather=None, before_time=None):
    """How likely someone comes by now (0..MAX_CHANCE), from the
    conditions (a conditions.Conditions) and the last reading."""
    p = CHANCE.get(conditions.weather, CHANCE[None])
    if weather_changed(before_weather, conditions.weather):
        p += CHANGE_BONUS
    if conditions.time_of_day in ("dawn", "dusk") and before_time != conditions.time_of_day:
        p += TWILIGHT_BONUS
    if conditions.full_moon_night:
        p += FULL_MOON_BONUS
    if conditions.windy:
        p += WINDY_BONUS
    return min(MAX_CHANCE, p)


def _seconds(value):
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) \
        and value >= 0 else 0.0


def normalize_state(raw):
    raw = raw if isinstance(raw, dict) else {}
    seen = raw.get("seen") if isinstance(raw.get("seen"), list) else []
    count = raw.get("count")
    weather = raw.get("last_weather")
    time_of_day = raw.get("last_time_of_day")
    return {
        "date": raw.get("date") if isinstance(raw.get("date"), str) else "",
        "count": count if isinstance(count, int) and not isinstance(count, bool)
        and count >= 0 else 0,
        "seen": [s for s in seen if isinstance(s, int) and not isinstance(s, bool)][-MAX_SEEN:],
        "last_any": _seconds(raw.get("last_any")),
        "last_manual": _seconds(raw.get("last_manual")),
        "last_check": _seconds(raw.get("last_check")),
        "last_weather": weather if isinstance(weather, str) else None,
        "last_time_of_day": time_of_day if isinstance(time_of_day, str) else None,
        "pending": _seconds(raw.get("pending")),
    }


class Pacer:
    """`load()`/`save(state)` keep the state; `clock()` is the wall clock
    (seconds); `rng` rolls the chances."""

    def __init__(self, load=lambda: None, save=lambda state: None, clock=time.time,
                 rng=None):
        self._save = save
        self.clock = clock
        self.rng = rng or random.Random()
        self.state = normalize_state(load())

    def save(self):
        self._save(dict(self.state, seen=list(self.state["seen"])))

    def _new_day(self, today):
        if self.state["date"] != today:
            self.state.update(date=today, count=0, seen=[])
            return True
        return False

    def _since(self, key):
        """Seconds since that moment; a moment in the future (the clock was
        put back) counts as just now."""
        return max(0.0, self.clock() - self.state[key])

    # --- looking around yourself -----------------------------------------------------------

    def manual_wait(self):
        """Seconds until looking around is allowed again (0: now)."""
        if not self.state["last_manual"]:
            return 0.0
        return max(0.0, MANUAL_COOLDOWN - self._since("last_manual"))

    def manual_done(self):
        """Someone was met by looking around: the wait starts now."""
        now = self.clock()
        self.state["last_manual"] = self.state["last_any"] = now
        self.save()

    # --- by themselves ---------------------------------------------------------------------------

    def ambient_left(self, today, frequency):
        """How many may still come by themselves today."""
        self._new_day(today)
        return max(0, PER_DAY.get(frequency, PER_DAY[DEFAULT_FREQUENCY]) - self.state["count"])

    def room_for_ambient(self, today, frequency):
        """Whether one may come by now: some left today, and the last one of
        any kind was long enough ago."""
        if self.ambient_left(today, frequency) <= 0:
            return False
        return not self.state["last_any"] or self._since("last_any") >= MIN_GAP

    def check_due(self):
        return not self.state["last_check"] or self._since("last_check") >= CHECK_EVERY

    def roll(self, conditions):
        """Look at the weather now (a conditions.Conditions, fetched by the
        plugin): remember it, and roll whether someone comes by. True: one
        is waiting for a pause now."""
        now = self.clock()
        fresh = self.state["last_check"] and now - self.state["last_check"] <= CHANGE_MEMORY
        before_weather = self.state["last_weather"] if fresh else None
        before_time = self.state["last_time_of_day"] if fresh else None
        p = chance(conditions, before_weather, before_time)
        self.state["last_check"] = now
        if conditions.weather is not None:
            self.state["last_weather"] = conditions.weather
        self.state["last_time_of_day"] = conditions.time_of_day
        hit = self.rng.random() < p
        if hit:
            self.state["pending"] = now
        self.save()
        return hit

    def pending_state(self):
        """ "none", "waiting" (for a pause) or "expired"."""
        if not self.state["pending"]:
            return "none"
        return "waiting" if self._since("pending") <= PENDING_LIMIT else "expired"

    def drop_pending(self):
        if self.state["pending"]:
            self.state["pending"] = 0.0
            self.save()

    def ambient_done(self, today, happened=True):
        """The waiting one is over: it came (and counts), or it didn't."""
        self.state["pending"] = 0.0
        if happened:
            self._new_day(today)
            self.state["count"] += 1
            self.state["last_any"] = self.clock()
        self.save()

    # --- who was met today ----------------------------------------------------------------

    def seen(self, today):
        return set(self.state["seen"]) if self.state["date"] == today else set()

    def add_seen(self, today, species_id):
        self._new_day(today)
        if species_id not in self.state["seen"]:
            self.state["seen"].append(species_id)
            self.state["seen"] = self.state["seen"][-MAX_SEEN:]
        self.save()


def quiet_ends(now, start_hour, end_hour):
    """When the quiet hours `now` is in end (a datetime)."""
    end = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if end <= now:
        end += datetime.timedelta(days=1)
    return end
