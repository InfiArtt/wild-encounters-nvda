# Wild Encounters for NVDA: an unofficial Pokémon fan add-on.
# Copyright (C) 2026 Rafli. Ported from the Wild Encounters extension for Hariku V2.
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License, version 2 or (at your option) any later version.
# Distributed WITHOUT ANY WARRANTY. See COPYING.txt.
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
The Field Journal: every Pokémon the user has met, saved on this computer
("WildEncountersJournal"). Pure logic: no wx, no network.

An entry, by species number: {"id", "slug", "name", "type" (the kind that
brought it the first time), "first_met" and "last_met" ("YYYY-MM-DD"),
"first_place" and "last_place" (the place's name as said, "Batam"),
"first_weather" and "last_weather" (a weather kind, or None),
"first_time" and "last_time" ("dawn", "day", "dusk", "night"),
"first_full_moon" and "last_full_moon", "count", "legendary", "mythical"}.

The last encounter is kept too ({"id", "name", "slug", "type", "at" (the
local time, ISO), "place", "weather", "time_of_day", "full_moon",
"special", "ambient"}).
"""

import datetime

VERSION = 1
MAX_ENTRIES = 2000
MAX_TEXT = 60


def _text(value, limit=MAX_TEXT):
    return " ".join(str(value or "").split())[:limit]


def _date(value):
    try:
        return datetime.date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError):
        return ""


def _clean_entry(raw):
    if not isinstance(raw, dict):
        return None
    number = raw.get("id")
    if not isinstance(number, int) or isinstance(number, bool) or not 0 < number < 100000:
        return None
    count = raw.get("count")
    entry = {
        "id": number,
        "slug": _text(raw.get("slug")),
        "name": _text(raw.get("name")) or _text(raw.get("slug")).capitalize(),
        "type": _text(raw.get("type"), 20),
        "count": count if isinstance(count, int) and not isinstance(count, bool)
        and count > 0 else 1,
        "legendary": raw.get("legendary") is True,
        "mythical": raw.get("mythical") is True,
    }
    for when in ("first", "last"):
        entry[f"{when}_met"] = _date(raw.get(f"{when}_met"))
        entry[f"{when}_place"] = _text(raw.get(f"{when}_place"))
        weather = raw.get(f"{when}_weather")
        entry[f"{when}_weather"] = _text(weather, 20) if weather else None
        entry[f"{when}_time"] = _text(raw.get(f"{when}_time"), 10)
        entry[f"{when}_full_moon"] = raw.get(f"{when}_full_moon") is True
    if not entry["name"] or not entry["first_met"]:
        return None
    entry["last_met"] = entry["last_met"] or entry["first_met"]
    return entry


def _clean_last(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), int):
        return None
    try:
        at = datetime.datetime.fromisoformat(str(raw.get("at"))).isoformat(timespec="minutes")
    except (TypeError, ValueError):
        return None
    return {"id": raw["id"], "name": _text(raw.get("name")), "slug": _text(raw.get("slug")),
            "type": _text(raw.get("type"), 20), "at": at, "place": _text(raw.get("place")),
            "weather": _text(raw.get("weather"), 20) or None,
            "time_of_day": _text(raw.get("time_of_day"), 10),
            "full_moon": raw.get("full_moon") is True,
            "special": _text(raw.get("special"), 20) or None,
            "ambient": raw.get("ambient") is True}


class Journal:
    """`load()` -> the stored dict (or None), `save(dict)`."""

    def __init__(self, load=lambda: None, save=lambda data: None):
        self._save = save
        raw = load()
        raw = raw if isinstance(raw, dict) else {}
        entries = raw.get("entries") if isinstance(raw.get("entries"), list) else []
        self.entries = {}
        for item in entries:
            entry = _clean_entry(item)
            if entry is not None:
                self.entries[entry["id"]] = entry
        self.last = _clean_last(raw.get("last"))

    def save(self):
        self._save({"version": VERSION, "entries": list(self.entries.values()),
                    "last": self.last})

    def record(self, encounter):
        """Write down one encounter (wild_encounters_app.Encounter). Returns
        (entry, first time?)."""
        c = encounter.conditions
        day = encounter.local.date().isoformat()
        details = {"met": day, "place": encounter.place, "weather": c.weather,
                   "time": c.time_of_day, "full_moon": c.full_moon_night}
        entry = self.entries.get(encounter.species_id)
        first = entry is None
        if first:
            entry = {"id": encounter.species_id, "slug": encounter.slug, "name": encounter.name,
                     "type": encounter.type, "count": 0, "legendary": encounter.legendary,
                     "mythical": encounter.mythical}
            for key, value in details.items():
                entry[f"first_{key}"] = value
            if len(self.entries) >= MAX_ENTRIES:
                return entry, first           # full: nothing more is written down
            self.entries[encounter.species_id] = entry
        entry["count"] += 1
        entry["name"] = encounter.name or entry["name"]
        for key, value in details.items():
            entry[f"last_{key}"] = value
        self.last = {"id": encounter.species_id, "name": encounter.name, "slug": encounter.slug,
                     "type": encounter.type, "at": encounter.local.isoformat(timespec="minutes"),
                     "place": encounter.place, "weather": c.weather,
                     "time_of_day": c.time_of_day, "full_moon": c.full_moon_night,
                     "special": encounter.special, "ambient": encounter.ambient}
        self.save()
        return entry, first

    def sorted_entries(self):
        """Most recently met first, then by name."""
        by_name = sorted(self.entries.values(), key=lambda e: e["name"].casefold())
        return sorted(by_name, key=lambda e: e["last_met"], reverse=True)

    def get(self, number):
        return self.entries.get(number)

    def species_count(self):
        return len(self.entries)

    def encounter_count(self):
        return sum(e["count"] for e in self.entries.values())

    def clear(self):
        self.entries = {}
        self.last = None
        self.save()
