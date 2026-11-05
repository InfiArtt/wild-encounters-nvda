# Wild Encounters for NVDA: an unofficial Pokémon fan add-on.
# Copyright (C) 2026 Rafli. Ported from the Wild Encounters extension for Hariku V2.
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License, version 2 or (at your option) any later version.
# Distributed WITHOUT ANY WARRANTY. See COPYING.txt.
#
# SPDX-License-Identifier: GPL-2.0-or-later


"""
What Wild Encounters says and shows, in English (the add-on's only
language; the sentences are in strings.json). No wx or NVDA here.

An announcement is a frame with the user's nickname (when they gave one),
around a scene built from the conditions and the kind of Pokémon: "Psst,
Rafli! It's raining over Batam... a Wingull is gliding just above the
rooftops near you." Every sentence is the add-on's own; the facts about a
Pokémon (types, size, colour, habitat, evolutions) are turned into sentences
here, never taken from the games.
"""

import datetime
import json
import os
import random
import re

from . import conditions as conditions_module

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(ADDON_DIR, "strings.json"), encoding="utf-8") as _file:
    STRINGS = json.load(_file)
_nickname = [""]


def _(key, **values):
    """The sentence `key`, with its {placeholders} filled in."""
    template = STRINGS.get(key, key)
    try:
        return template.format(**values) if values else template
    except (KeyError, IndexError, ValueError):
        return template

FRAME_VARIANTS = 4
SCENE_VARIANTS = 2
INTRO_VARIANTS = 2
ACTION_VARIANTS = 2
RARE_VARIANTS = 2
HABITATS = ("cave", "forest", "grassland", "mountain", "rough-terrain", "sea", "urban",
            "waters-edge")
COLOURS = ("black", "blue", "brown", "gray", "green", "pink", "purple", "red", "white", "yellow")
INTROS = ("rain", "heavy_rain", "drizzle", "thunderstorm", "snow", "fog", "cloudy", "windy",
          "hot", "cold", "dawn", "day", "dusk", "night", "full_moon")
_last_variant = {}


def set_nickname(name):
    _nickname[0] = " ".join(str(name or "").split())


def nickname():
    """What the user asked to be called, or ""."""
    return _nickname[0]


def tidy(text):
    """A text whose {name} was empty, put right: "Psst, ! It's" -> "Psst!
    It's", "Look, ~" -> "Look~"."""
    text = re.sub(r"\s+([,.!?:~])", r"\1", text)
    text = re.sub(r",+([,.!?:~])", r"\1", text)
    return " ".join(text.split())


def cap(text):
    return text[:1].upper() + text[1:] if text else text


def variant(prefix, count, rng=random):
    """One of prefix_1 .. prefix_count; with more than two, not the one
    picked last time (with two, that would only ever alternate)."""
    choices = list(range(1, count + 1))
    if len(choices) > 2 and _last_variant.get(prefix) in choices:
        choices.remove(_last_variant[prefix])
    picked = rng.choice(choices)
    _last_variant[prefix] = picked
    return f"{prefix}_{picked}"


def article(word):
    """ "a" or "an" before a name ("an Eevee", "a Psyduck")."""
    return "an" if str(word or "")[:1].lower() in "aeiou" else "a"


def creature(name):
    """ "a Psyduck", "an Abra"."""
    return f"{article(name)} {name}"


def place_text(place):
    return place or _("place_fallback")


def join_names(names):
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return _("list_and", first=", ".join(names[:-1]), last=names[-1])


# ------------------------------------------------------------
# An encounter
# ------------------------------------------------------------

def intro_kind(c):
    """Which intro the conditions call for (see INTROS), most striking first."""
    weather = c.weather
    if weather in ("thunderstorm", "snow", "heavy_rain", "rain", "drizzle", "fog"):
        return weather
    if c.full_moon_night and weather in (None, "clear", "partly_cloudy"):
        return "full_moon"
    if c.windy:
        return "windy"
    if c.hot and c.time_of_day == "day":
        return "hot"
    if c.cold:
        return "cold"
    if weather == "cloudy":
        return "cloudy"
    return c.time_of_day


def scene(encounter, rng=random):
    c = encounter.conditions
    intro = _(variant(f"intro_{intro_kind(c)}", INTRO_VARIANTS, rng), place=place_text(c.place))
    action = _(variant(f"act_{encounter.type}", ACTION_VARIANTS, rng))
    return _(variant("scene", SCENE_VARIANTS, rng), intro=intro,
             creature=creature(encounter.name), action=action)


def announcement(encounter, name=None, rng=random):
    """What an encounter says, in the persona, with the nickname."""
    name = nickname() if name is None else name
    parts = [tidy(_(variant("enc", FRAME_VARIANTS, rng), name=name, scene=scene(encounter, rng)))]
    if encounter.legendary or encounter.mythical:
        parts.append(_(variant("rare", RARE_VARIANTS, rng)))
    if encounter.special == "birthday" and encounter.first_today:
        parts.append(tidy(_("birthday", name=name)))
    return " ".join(parts)


# ------------------------------------------------------------
# Weather and dates in words
# ------------------------------------------------------------

def weather_phrase(weather, time_of_day, full_moon=False):
    """ "in the rain", "under the stars", "at dawn"."""
    night = time_of_day in ("dusk", "night")
    if weather in ("rain", "heavy_rain", "drizzle", "thunderstorm", "snow", "fog"):
        return _(f"w_{weather}")
    if full_moon and night and weather in (None, "clear", "partly_cloudy"):
        return _("w_full_moon")
    if weather == "cloudy":
        return _("w_cloudy_night" if night else "w_cloudy_day")
    if weather == "clear":
        return _(f"w_clear_{time_of_day}") if time_of_day in conditions_module.TIMES_OF_DAY \
            else _("w_clear_day")
    if weather == "partly_cloudy":
        return _("w_partly_night" if night else "w_partly_day")
    return _(f"w_time_{time_of_day}") if time_of_day in conditions_module.TIMES_OF_DAY \
        else _("w_time_day")


def date_text(day, today):
    key = "date_this_year" if day.year == today.year else "date_other_year"
    return _(key, day=day.day, month=_(f"month_{day.month}"), year=day.year)


def when_text(day, today):
    """ "today", "yesterday", "on 3 October" (a date or "YYYY-MM-DD")."""
    if isinstance(day, str):
        try:
            day = datetime.date.fromisoformat(day)
        except ValueError:
            return ""
    if day == today:
        return _("when_today")
    if day == today - datetime.timedelta(days=1):
        return _("when_yesterday")
    return _("when_date", date=date_text(day, today))


def clock_text(moment):
    """ "3:40 PM"."""
    return moment.strftime("%I:%M %p").lstrip("0")


def count_text(count):
    if count == 1:
        return _("count_once")
    if count == 2:
        return _("count_twice")
    return _("count_n", n=count)


# ------------------------------------------------------------
# Facts about a Pokémon, in the add-on's own words
# ------------------------------------------------------------

def type_name(kind):
    return _(f"type_{kind}") if kind in conditions_module.TYPES else kind.capitalize()


def types_phrase(types):
    """ "a Water-type", "a Grass- and Poison-type", "an Electric-type"."""
    names = [type_name(t) for t in types if t]
    if not names:
        return ""
    if len(names) == 1:
        return _("types_one", article=article(names[0]), first=names[0])
    return _("types_two", article=article(names[0]), first=names[0], second=names[1])


def _number(value):
    """ "0.8", "2", "1,234.5"."""
    text = f"{value:,.1f}"
    return text[:-2] if text.endswith(".0") else text


def height_text(decimetres, units="metric"):
    if units == "imperial":
        inches_total = int(round(decimetres * 3.937008))
        feet, inches = divmod(inches_total, 12)
        parts = []
        if feet:
            parts.append(_("len_foot" if feet == 1 else "len_feet", n=feet))
        if inches or not feet:
            parts.append(_("len_inch" if inches == 1 else "len_inches", n=inches))
        if len(parts) == 2:
            return _("len_feet_inches", feet=parts[0], inches=parts[1])
        return parts[0]
    metres = decimetres / 10
    return _("len_metre" if metres == 1 else "len_metres", n=_number(metres))


def weight_text(hectograms, units="metric"):
    if units == "imperial":
        pounds = round(hectograms / 10 * 2.2046226, 1)
        return _("mass_pound" if pounds == 1 else "mass_pounds", n=_number(pounds))
    kilograms = hectograms / 10
    return _("mass_kilogram" if kilograms == 1 else "mass_kilograms", n=_number(kilograms))


def rarity_key(capture_rate):
    if capture_rate is None:
        return None
    if capture_rate >= 190:
        return "rarity_common"
    if capture_rate >= 100:
        return "rarity_fair"
    if capture_rate >= 45:
        return "rarity_uncommon"
    return "rarity_rare"


def about_text(facts, entry=None, units="metric", today=None):
    """Sentences about one Pokémon from `facts` (encounter.WildApp.facts())
    and, when the user met it, its Field Journal entry."""
    today = today or datetime.date.today()
    return " ".join(fact_sentences(facts, units) + [met_sentence(entry, today)])


def encounter_facts(facts, entry=None, units="metric"):
    """What follows an encounter's scene: the facts, then whether it is new
    in the Field Journal ("" without facts and entry)."""
    sentences = fact_sentences(facts, units) if facts else []
    if entry:
        if entry["count"] == 1:
            sentences.append(_("journal_new"))
        else:
            sentences.append(_("journal_again", count=count_text(entry["count"])))
    return " ".join(sentences)


def fact_sentences(facts, units="metric"):
    """The facts about one Pokémon, as sentences (a list)."""
    name = facts["name"]
    types = types_phrase(facts.get("types") or [])
    generation = facts.get("generation")
    if types and generation:
        sentences = [_("about_intro", name=name, types=types, generation=generation)]
    elif types:
        sentences = [_("about_intro_no_generation", name=name, types=types)]
    elif generation:
        sentences = [_("about_intro_plain", name=name, generation=generation)]
    else:
        sentences = [_("about_intro_nothing", name=name)]
    if facts.get("height") and facts.get("weight"):
        sentences.append(_("about_size", height=height_text(facts["height"], units),
                           weight=weight_text(facts["weight"], units)))
    if facts.get("color") in COLOURS:
        sentences.append(_("about_colour", colour=_(f"colour_{facts['color']}")))
    habitat = facts.get("habitat")
    if habitat == "rare":
        sentences.append(_("about_habitat_rare"))
    elif habitat in HABITATS:
        sentences.append(_("about_habitat", habitat=_(f"habitat_{habitat}")))
    if facts.get("evolves_from"):
        sentences.append(_("about_from", name=facts["evolves_from"]))
    if facts.get("evolves_to"):
        sentences.append(_("about_into", names=join_names(facts["evolves_to"])))
    elif not facts.get("evolves_from") and facts.get("chain_known"):
        sentences.append(_("about_no_evolution"))
    if facts.get("mythical"):
        sentences.append(_("about_mythical"))
    elif facts.get("legendary"):
        sentences.append(_("about_legendary"))
    else:
        if facts.get("baby"):
            sentences.append(_("about_baby"))
        key = rarity_key(facts.get("capture_rate"))
        if key:
            sentences.append(_(key))
    return sentences


def met_sentence(entry, today):
    if not entry:
        return _("about_not_met")
    weather = weather_phrase(entry.get("first_weather"), entry.get("first_time"),
                             entry.get("first_full_moon"))
    when = when_text(entry["first_met"], today)
    place = place_text(entry.get("first_place"))
    if entry["count"] == 1:
        return _("about_met_once", when=when, place=place, weather=weather)
    return _("about_met", when=when, place=place, weather=weather,
             count=count_text(entry["count"]))


# ------------------------------------------------------------
# The Field Journal
# ------------------------------------------------------------

def share_text(entry, today):
    """ "I met a Psyduck in the rain today!" (the latest time it was met; the
    place stays out of it)."""
    weather = weather_phrase(entry.get("last_weather"), entry.get("last_time"),
                             entry.get("last_full_moon"))
    return _("share", creature=creature(entry["name"]), weather=weather,
             when=when_text(entry["last_met"], today))


def journal_line(entry, today):
    """ "Psyduck: met 3 times, last today" (the list's item)."""
    return _("journal_item", name=entry["name"], count=count_text(entry["count"]),
             when=when_text(entry["last_met"], today))


def details_text(entry, today):
    weather = weather_phrase(entry.get("first_weather"), entry.get("first_time"),
                             entry.get("first_full_moon"))
    return _("details_entry", name=entry["name"], when=when_text(entry["first_met"], today),
             place=place_text(entry.get("first_place")), weather=weather,
             count=count_text(entry["count"]))


def summary_text(journal, today):
    """ "You've met 12 different Pokémon, 17 encounters in all. The latest
    was a Psyduck, today." """
    entries = journal.sorted_entries()
    if not entries:
        return _("summary_none")
    if len(entries) == 1:
        return _("summary_one", name=entries[0]["name"], count=count_text(entries[0]["count"]))
    text = _("summary_many", species=len(entries), encounters=journal.encounter_count())
    last = journal.last
    if last:
        try:
            day = datetime.datetime.fromisoformat(last["at"]).date()
        except ValueError:
            return text
        text += " " + _("summary_latest", creature=creature(last["name"]),
                        when=when_text(day, today))
    return text


def last_text(last, today):
    """The last encounter, said."""
    if not last:
        return _("last_none")
    try:
        moment = datetime.datetime.fromisoformat(last["at"])
    except (KeyError, ValueError):
        return _("last_none")
    weather = weather_phrase(last.get("weather"), last.get("time_of_day"),
                             last.get("full_moon"))
    return _("last_text", creature=creature(last["name"]), when=when_text(moment.date(), today),
             time=clock_text(moment), weather=weather, place=place_text(last.get("place")))


# ------------------------------------------------------------
# Errors, the first run
# ------------------------------------------------------------

def error_text(kind):
    if kind == "no_place":
        return _("no_place")
    if kind == "nothing":
        return _("error_nothing")
    return _("error_offline")


def disclaimer():
    return _("disclaimer")


def welcome_text():
    return _("welcome", disclaimer=disclaimer())


def about_page_text():
    return _("about_text", disclaimer=disclaimer())


# ------------------------------------------------------------
# Waiting, the status
# ------------------------------------------------------------

def minutes_text(seconds):
    """ "42 minutes", "1 minute", "less than a minute"."""
    minutes = int((seconds + 59) // 60)
    if seconds < 60:
        return _("minutes_less")
    if minutes == 1:
        return _("minutes_one")
    return _("minutes_n", n=minutes)


def hour_text(hour):
    """ "10 PM", "7 AM", "midnight", "noon"."""
    if hour == 0:
        return _("hour_midnight")
    if hour == 12:
        return _("hour_noon")
    return _("hour_am" if hour < 12 else "hour_pm", n=hour % 12)
