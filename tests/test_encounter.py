import datetime
import random

import pytest

from wildEncounters import encounter, journal, pacing, text
from wildEncounters.pokeapi import PokeAPIError


class FakeAPI:
    def __init__(self, species, fail=False):
        self._species = {s["id"]: s for s in species}
        self.fail = fail

    def resting(self):
        return 0

    def is_cached(self, kind, key):
        return True

    def type_members(self, kind):
        if self.fail:
            raise PokeAPIError("offline", "x")
        return {"name": kind, "pokemon": [{"name": s["name"], "id": s["id"]}
                                          for s in self._species.values()]}

    def species(self, number, offline=False):
        return self._species[number]


class FakeWeather:
    def __init__(self, code=61):
        self.code = code
        self.asked = []

    def current(self, lat, lon):
        self.asked.append((lat, lon))
        return {"code": self.code, "temperature": 26.0, "wind": 10.0, "utc_offset": 7 * 3600,
                "sunrise": datetime.datetime(2026, 10, 14, 5, 50),
                "sunset": datetime.datetime(2026, 10, 14, 17, 55)}


PSYDUCK = {"id": 54, "name": "psyduck", "display": "Psyduck", "capture_rate": 190}
MEW = {"id": 151, "name": "mew", "display": "Mew", "capture_rate": 45, "is_mythical": True}
HOME = {"lat": 1.13, "lon": 104.05, "name": "Batam"}


def make(species=(PSYDUCK,), settings=None, fail=False):
    return encounter.WildApp(
        api=FakeAPI(species, fail), weather=FakeWeather(), journal=journal.Journal(),
        pacer=pacing.Pacer(), rng=random.Random(4),
        utcnow=lambda: datetime.datetime(2026, 10, 14, 7, 0, tzinfo=datetime.timezone.utc),
        today=lambda: datetime.date(2026, 10, 14),
        load_settings=lambda: dict({"place": HOME}, **(settings or {})))


def test_an_encounter_at_home_in_the_rain():
    app = make()
    found = app.encounter()
    assert found.name == "Psyduck"
    assert found.conditions.weather == "rain" and found.conditions.place == "Batam"
    assert found.local == datetime.datetime(2026, 10, 14, 14, 0)        # Batam's clock
    assert app.weather.asked == [(1.13, 104.05)]
    app.record(found)
    assert app.seen_today() == {54}
    assert app.journal.get(54)["count"] == 1


def test_without_home_there_is_nothing():
    app = make(settings={"place": None})
    with pytest.raises(encounter.EncounterError) as caught:
        app.encounter()
    assert caught.value.kind == "no_place"


def test_offline_is_said_as_offline():
    with pytest.raises(encounter.EncounterError) as caught:
        make(fail=True).encounter()
    assert caught.value.kind == "offline"


def test_mythical_ones_only_on_the_birthday():
    with pytest.raises(encounter.EncounterError):
        make(species=(MEW,)).encounter()
    found = make(species=(MEW,), settings={"birthday": "10-14"}).encounter()
    assert found.special == "birthday" and found.mythical


@pytest.mark.parametrize("raw, expected", [
    ({"lat": 1.130123, "lon": 104.052987, "name": "  Batam\n"},
     {"lat": 1.13, "lon": 104.05, "name": "Batam"}),
    ({"lat": "x", "lon": 1}, None), ({"lat": 95, "lon": 1}, None), ({"lat": 0, "lon": 0}, None),
    ("Batam", None), (None, None),
])
def test_home_is_kept_rounded(raw, expected):
    assert encounter.normalize_place(raw) == expected


def test_settings_are_cleaned():
    settings = encounter.normalize_settings({
        "enabled": "yes", "frequency": "always", "quiet_from": 25, "quiet_until": 6,
        "nickname": "Rafli " * 20, "birthday": "02-30", "units": "imperial"})
    assert settings["enabled"] is False and settings["frequency"] == "normal"
    assert settings["quiet_from"] == 22 and settings["quiet_until"] == 6
    assert len(settings["nickname"]) <= encounter.MAX_NICKNAME
    assert settings["birthday"] == "" and settings["units"] == "imperial"
    assert encounter.normalize_settings({"birthday": "10-14"})["birthday"] == "10-14"


# --- what is said ----------------------------------------------------------------------------------

def test_the_announcement_with_and_without_a_nickname():
    found = make().encounter()
    text.set_nickname("")
    plain = text.announcement(found, rng=random.Random(1))
    assert "Psyduck" in plain and "Batam" in plain
    assert "{" not in plain and " ," not in plain and "!!" not in plain
    text.set_nickname("Rafli")
    assert "Rafli" in text.announcement(found, rng=random.Random(1))
    text.set_nickname("")


def test_waiting_and_hours_in_words():
    assert text.minutes_text(30) == "less than a minute"
    assert text.minutes_text(60) == "1 minute"
    assert text.minutes_text(41 * 60 + 5) == "42 minutes"
    assert [text.hour_text(h) for h in (0, 7, 12, 22)] == ["midnight", "7 AM", "noon", "10 PM"]


def test_every_sentence_fills_in():
    for key, template in text.STRINGS.items():
        assert template.count("{") == template.count("}"), key
        assert "Hariku" not in template and "Aruna" not in template, key


def test_the_facts_after_an_encounter():
    facts = {"name": "Cacnea", "types": ["grass"], "generation": 3, "height": 4, "weight": 513,
             "habitat": "rough-terrain", "evolves_to": ["Cacturne"], "capture_rate": 190}
    said = text.encounter_facts(facts, {"count": 1})
    assert said.startswith("Cacnea is a Grass-type Pokémon from generation 3.")
    assert "0.4 metres" in said and "Cacturne" in said
    assert said.endswith("It's new in your Field Journal.")
    assert text.encounter_facts(facts, {"count": 3}).endswith("You've met it 3 times now.")
    assert text.encounter_facts(None, {"count": 2}) == "You've met it twice now."
    assert text.encounter_facts(None, None) == ""
