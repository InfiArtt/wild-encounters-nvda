# Wild Encounters for NVDA: an unofficial Pokémon fan add-on.
# Copyright (C) 2026 Rafli. Ported from the Wild Encounters extension for Hariku V2.
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License, version 2 or (at your option) any later version.
# Distributed WITHOUT ANY WARRANTY. See COPYING.txt.
#
# SPDX-License-Identifier: GPL-2.0-or-later


"""
Wild Encounters' state and its one big job: who is around, now, at home.
No wx or NVDA; everything it needs (home, the clock, storage, the birthday)
is handed in, so tests drive it with fakes.

An encounter (blocking: the plugin runs it on a worker thread):
  1. the conditions: the weather at home from Open-Meteo (offline: without
     it), the time of day there, the Moon, the user's birthday;
  2. a type, picked by the weights wild_encounters_conditions gives them;
  3. a few species of that type (SAMPLE, or SPECIAL_SAMPLE on a special
     occasion), none met today if possible, fetched from PokéAPI (or its
     cache), and one of them picked by rarity: its capture rate is its
     weight, so common ones come often and rare ones seldom. Legendary and
     mythical ones are left out, except on a full moon night or the user's
     birthday, when they weigh SPECIAL_WEIGHT.
If PokéAPI is resting after a failure, only species already cached are
looked at. When a type gives nothing, another is tried (MAX_TYPE_TRIES).
"""

import datetime
import logging
import random

from . import conditions as conditions_module
from . import pacing
from .pokeapi import PokeAPIError, display_from_slug
from .weather import WeatherError

logger = logging.getLogger(__name__)

SAMPLE = 5
SPECIAL_SAMPLE = 8
SPECIAL_WEIGHT = 30
MIN_WEIGHT = 3
DEFAULT_CAPTURE_RATE = 45
MAX_TYPE_TRIES = 3
UNITS = ("metric", "imperial")
MAX_NICKNAME = 30
DEFAULT_SETTINGS = {"enabled": False, "frequency": pacing.DEFAULT_FREQUENCY,
                    "sounds": True, "type_sounds": True, "units": "metric", "welcomed": False,
                    "place": None, "nickname": "", "quiet_from": 22, "quiet_until": 7,
                    "birthday": ""}


class EncounterError(Exception):
    """kind: "no_place", "offline" (PokéAPI couldn't be reached and nothing
    useful is cached), "nothing" (no species fitted)."""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


def _text(value, limit):
    value = "".join(c for c in str(value or "") if c.isprintable())
    return " ".join(value.split())[:limit]


def normalize_place(raw):
    """{"lat", "lon" (rounded to 2 decimals, about 1 km), "name"} or None."""
    if not isinstance(raw, dict):
        return None
    try:
        lat, lon = round(float(raw.get("lat")), 2), round(float(raw.get("lon")), 2)
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return {"lat": lat, "lon": lon, "name": _text(raw.get("name"), 60)}


def normalize_birthday(value):
    """ "MM-DD", or "" when it isn't a real day."""
    value = str(value or "")
    if len(value) != 5 or value[2] != "-":
        return ""
    try:
        return datetime.date(2000, int(value[:2]), int(value[3:])).strftime("%m-%d")
    except ValueError:
        return ""


def normalize_settings(raw):
    raw = raw if isinstance(raw, dict) else {}
    settings = dict(DEFAULT_SETTINGS)
    for key in ("enabled", "sounds", "type_sounds", "welcomed"):
        if isinstance(raw.get(key), bool):
            settings[key] = raw[key]
    if raw.get("frequency") in pacing.FREQUENCIES:
        settings["frequency"] = raw["frequency"]
    if raw.get("units") in UNITS:
        settings["units"] = raw["units"]
    for key in ("quiet_from", "quiet_until"):
        value = raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 23:
            settings[key] = value
    settings["place"] = normalize_place(raw.get("place"))
    settings["nickname"] = _text(raw.get("nickname"), MAX_NICKNAME)
    settings["birthday"] = normalize_birthday(raw.get("birthday"))
    return settings


def is_birthday(settings, day):
    return bool(settings.get("birthday")) and day.strftime("%m-%d") == settings["birthday"]


def place_label(place):
    """How home is said in an encounter: its town ("Batam"), else "" (the
    text then says "your neighbourhood")."""
    return _text((place or {}).get("name"), 60)


def rarity_weight(species, special=None):
    """How likely a species is among those looked at: its capture rate (so
    a common one, 255, is far likelier than a rare one, 3); legendary and
    mythical ones only on a special occasion."""
    if species.get("is_legendary") or species.get("is_mythical"):
        return SPECIAL_WEIGHT if special else 0
    rate = species.get("capture_rate")
    if rate is None:
        rate = DEFAULT_CAPTURE_RATE
    return max(MIN_WEIGHT, rate)


def weighted_choice(pairs, rng=random):
    """One item of [(item, weight)], likelier with more weight; None for none."""
    pairs = [(item, w) for item, w in pairs if w > 0]
    if not pairs:
        return None
    total = sum(w for _item, w in pairs)
    point = rng.random() * total
    for item, w in pairs:
        point -= w
        if point < 0:
            return item
    return pairs[-1][0]


class Encounter:
    """One Pokémon met: who (species number, slug, name), the type that
    brought it, the conditions, the place's name, the moment on the place's
    clock, whether it is legendary or mythical, the special occasion, whether
    it came by itself (ambient) or was asked for, and whether it is the first
    one today."""

    def __init__(self, species, kind, conditions, local, special=None, ambient=True,
                 first_today=False):
        self.species_id = species["id"]
        self.slug = species["name"]
        self.name = species.get("display") or display_from_slug(species["name"])
        self.type = kind
        self.conditions = conditions
        self.place = conditions.place
        self.local = local
        self.legendary = bool(species.get("is_legendary"))
        self.mythical = bool(species.get("is_mythical"))
        self.capture_rate = species.get("capture_rate")
        self.special = special
        self.ambient = ambient
        self.first_today = first_today

    def __repr__(self):
        return f"Encounter({self.name!r}, {self.type!r}, {self.conditions!r})"


class WildApp:
    """`api`: a pokeapi.PokeAPI; `weather`: a weather.WeatherClient;
    `journal`: a Journal; `pacer`: a pacing.Pacer; `utcnow()` -> an aware
    UTC datetime; `today()` -> the computer's date (the day's list, the
    birthday); `load_settings()`/`save_settings(dict)`. Home and the
    birthday are in the settings."""

    def __init__(self, api, weather, journal, pacer, utcnow=None, today=None,
                 load_settings=lambda: None, save_settings=lambda settings: None, rng=None):
        self.api = api
        self.weather = weather
        self.journal = journal
        self.pacer = pacer
        self.utcnow = utcnow or (lambda: datetime.datetime.now(datetime.timezone.utc))
        self.today = today or datetime.date.today
        self._save_settings = save_settings
        self.settings = normalize_settings(load_settings())
        self.rng = rng or random.Random()

    def main_place(self):
        return self.settings["place"]

    # --- settings --------------------------------------------------------------------------

    def save_settings(self, new_settings):
        merged = dict(self.settings)
        merged.update(new_settings or {})
        self.settings = normalize_settings(merged)
        self._save_settings(dict(self.settings))
        return self.settings

    def mark_welcomed(self):
        if not self.settings["welcomed"]:
            self.save_settings({"welcomed": True})
            return True
        return False

    # --- the conditions -----------------------------------------------------------------------

    def conditions(self, place):
        """The Conditions at `place` now. The weather may be missing (offline);
        everything else is worked out here. Blocking."""
        now = self.utcnow()
        local = now.astimezone().replace(tzinfo=None)     # the computer's clock, until the
        found = None                                      # weather says the place's
        try:
            found = self.weather.current(place["lat"], place["lon"])
        except WeatherError as e:
            logger.info(f"[Wild Encounters] No weather: {e}")
        except Exception:
            logger.exception("[Wild Encounters] No weather")
        sunrise = sunset = None
        if found:
            offset = found.get("utc_offset")
            if offset is not None:
                local = (now + datetime.timedelta(seconds=offset)).replace(tzinfo=None)
            sunrise, sunset = _same_day(found.get("sunrise"), local), \
                _same_day(found.get("sunset"), local)
        try:
            birthday = is_birthday(self.settings, self.today())
        except Exception:
            birthday = False
        return conditions_module.Conditions(
            weather=conditions_module.weather_kind(found["code"]) if found else None,
            temperature=found.get("temperature") if found else None,
            wind=found.get("wind") if found else None,
            time_of_day=conditions_module.time_of_day(local, sunrise, sunset),
            moon=conditions_module.moon_phase(now), birthday=birthday,
            place=place_label(place), local=local)

    # --- an encounter --------------------------------------------------------------------------

    def encounter(self, ambient=True, seen=(), place=None):
        """Who is around now (an Encounter). Raises EncounterError. Blocking."""
        place = place or self.main_place()
        if not place:
            raise EncounterError("no_place")
        c = self.conditions(place)
        special = conditions_module.special_occasion(c)
        weights = conditions_module.type_weights(c)
        seen = set(seen)
        tried, failures = {}, []
        for _attempt in range(MAX_TYPE_TRIES):
            kind = weighted_choice([(k, w) for k, w in weights.items() if k not in tried],
                                   self.rng)
            if kind is None:
                break
            try:
                tried[kind] = self.api.type_members(kind)["pokemon"]
            except PokeAPIError as e:
                tried[kind] = []
                failures.append(e)
                logger.info(f"[Wild Encounters] Type {kind}: {e}")
                continue
            species = self._pick(tried[kind], seen, special, failures)
            if species is not None:
                return Encounter(species, kind, c, c.local, special, ambient,
                                 first_today=not seen)
        if seen:
            # Everyone of those kinds was met today already: one of them again.
            for kind, members in tried.items():
                species = self._pick(members, (), special, failures)
                if species is not None:
                    return Encounter(species, kind, c, c.local, special, ambient)
        if failures:
            raise EncounterError("offline", str(failures[-1]))
        raise EncounterError("nothing")

    def _pick(self, members, seen, special, failures):
        """A species among `members` not in `seen`, picked by rarity from a
        few looked at; None when none fits."""
        pool = [m for m in members if m["id"] not in seen]
        if self.api.resting() > 0:
            pool = [m for m in pool if self.api.is_cached("pokemon-species", m["id"])]
        if not pool:
            return None
        size = SPECIAL_SAMPLE if special else SAMPLE
        weighted = []
        for member in self.rng.sample(pool, min(size, len(pool))):
            try:
                species = self.api.species(member["id"])
            except PokeAPIError as e:
                failures.append(e)
                if e.kind in ("backoff", "limit"):
                    break
                continue
            weighted.append((species, rarity_weight(species, special)))
        return weighted_choice(weighted, self.rng)

    # --- after an encounter (on the UI thread) ---------------------------------------------------

    def record(self, encounter):
        """Write it in the Field Journal and today's list. Returns (entry,
        first time?)."""
        self.pacer.add_seen(self._today_iso(), encounter.species_id)
        return self.journal.record(encounter)

    def _today_iso(self):
        return self.today().isoformat()

    def seen_today(self):
        return self.pacer.seen(self._today_iso())

    # --- facts --------------------------------------------------------------------------------

    def _name_of(self, slug, number):
        if number:
            try:
                return self.api.species(number, offline=True)["display"]
            except PokeAPIError:
                pass
        return display_from_slug(slug)

    def facts(self, number):
        """What is known about one species: {"id", "name", "types",
        "generation", "height", "weight", "color", "habitat", "legendary",
        "mythical", "baby", "capture_rate", "evolves_from", "evolves_to",
        "chain_known"}. Raises PokeAPIError when the species itself can't
        be had; the rest is left out when it can't. Blocking."""
        species = self.api.species(number)
        facts = {"id": species["id"], "name": species["display"],
                 "generation": species.get("generation"), "color": species.get("color"),
                 "habitat": species.get("habitat"), "legendary": species.get("is_legendary"),
                 "mythical": species.get("is_mythical"), "baby": species.get("is_baby"),
                 "capture_rate": species.get("capture_rate"), "types": [],
                 "height": None, "weight": None, "evolves_from": None, "evolves_to": [],
                 "chain_known": False}
        try:
            body = self.api.pokemon(species.get("pokemon_id") or species["id"])
            facts.update(types=body["types"], height=body["height"], weight=body["weight"])
        except PokeAPIError as e:
            logger.info(f"[Wild Encounters] No body facts for {number}: {e}")
        parent = species.get("evolves_from")
        if parent:
            facts["evolves_from"] = self._name_of(parent["name"], parent.get("id"))
        if species.get("chain_id"):
            try:
                chain = self.api.evolution_chain(species["chain_id"])["chain"]
                node = _find_link(chain, species["name"])
                if node is not None:
                    facts["chain_known"] = True
                    facts["evolves_to"] = [self._name_of(n["name"], n.get("id"))
                                           for n in node["to"]]
            except PokeAPIError as e:
                logger.info(f"[Wild Encounters] No evolutions for {number}: {e}")
        return facts


def _same_day(moment, local):
    """Sunrise or sunset moved to `local`'s day (a forecast kept from
    before midnight still answers)."""
    if moment is None:
        return None
    return datetime.datetime.combine(local.date(), moment.time())


def _find_link(node, slug, depth=0):
    if node is None or depth > 8:
        return None
    if node.get("name") == slug:
        return node
    for child in node.get("to") or []:
        found = _find_link(child, slug, depth + 1)
        if found is not None:
            return found
    return None
