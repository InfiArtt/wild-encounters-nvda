import datetime

import pytest

from wildEncounters import pacing
from wildEncounters.conditions import Conditions


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += minutes * 60


class Dice:
    """random() that returns what it is told."""

    def __init__(self, value=0.5):
        self.value = value

    def random(self):
        return self.value


def make(saved=None, dice=0.5):
    clock = Clock()
    store = {"state": saved}
    pacer = pacing.Pacer(load=lambda: store["state"],
                         save=lambda state: store.update(state=state), clock=clock,
                         rng=Dice(dice))
    return pacer, clock, store


TODAY = "2026-10-14"


# --- looking around yourself: every 20 minutes --------------------------------------------

def test_looking_around_once_an_hour():
    pacer, clock, store = make()
    assert pacer.manual_wait() == 0
    pacer.manual_done()
    assert pacer.manual_wait() == pacing.MANUAL_COOLDOWN
    clock.advance(19)
    assert 0 < pacer.manual_wait() <= 60
    clock.advance(1)
    assert pacer.manual_wait() == 0


def test_the_hour_survives_a_restart():
    pacer, clock, store = make()
    pacer.manual_done()
    again, _clock, _store = make(saved=store["state"])
    again.clock = clock
    clock.advance(10)
    assert again.manual_wait() == 10 * 60


def test_putting_the_clock_back_doesnt_lock_it_for_longer():
    pacer, clock, _store = make()
    pacer.manual_done()
    clock.advance(-600)                        # ten hours back
    assert pacer.manual_wait() == pacing.MANUAL_COOLDOWN


# --- by themselves -----------------------------------------------------------------------------

def sunny(**kw):
    return Conditions(weather=kw.pop("weather", "clear"), time_of_day=kw.pop("time", "day"), **kw)


def test_the_weather_sets_the_chance():
    assert pacing.chance(sunny()) == pacing.CHANCE["clear"]
    assert pacing.chance(sunny(weather="rain")) > pacing.chance(sunny())
    assert pacing.chance(sunny(weather="thunderstorm")) > pacing.chance(sunny(weather="rain"))


def test_a_change_in_the_weather_is_the_big_moment():
    started = pacing.chance(sunny(weather="rain"), before_weather="clear")
    stopped = pacing.chance(sunny(weather="clear"), before_weather="rain")
    steady = pacing.chance(sunny(weather="rain"), before_weather="rain")
    assert started == pytest.approx(pacing.CHANCE["rain"] + pacing.CHANGE_BONUS)
    assert stopped == pytest.approx(pacing.CHANCE["clear"] + pacing.CHANGE_BONUS)
    assert steady == pacing.CHANCE["rain"]
    # clouds coming and going isn't a moment
    assert pacing.chance(sunny(weather="cloudy"), before_weather="clear") == pacing.CHANCE["cloudy"]


def test_twilight_full_moon_and_wind_add_a_little_never_too_much():
    dusk = pacing.chance(sunny(time="dusk"), before_time="day")
    assert dusk == pytest.approx(pacing.CHANCE["clear"] + pacing.TWILIGHT_BONUS)
    assert pacing.chance(sunny(time="dusk"), before_time="dusk") == pacing.CHANCE["clear"]
    moon = {"fraction": 0.5, "name": "full", "full": True}
    wild = sunny(weather="thunderstorm", time="dusk", moon=moon, wind=60)
    assert pacing.chance(wild, before_weather="clear", before_time="day") == pacing.MAX_CHANCE


def test_a_roll_remembers_the_weather_and_may_bring_someone():
    pacer, clock, store = make(dice=0.99)
    assert not pacer.roll(sunny())
    assert store["state"]["last_weather"] == "clear"
    clock.advance(30)
    pacer.rng = Dice(pacing.CHANCE["rain"] + pacing.CHANGE_BONUS - 0.01)
    assert pacer.roll(sunny(weather="rain"))       # the rain just started
    assert pacer.pending_state() == "waiting"


def test_an_old_reading_isnt_a_change():
    pacer, clock, _store = make(dice=0.99)
    pacer.roll(sunny())
    clock.advance(pacing.CHANGE_MEMORY / 60 + 1)
    pacer.rng = Dice(pacing.CHANCE["rain"] + 0.01)
    assert not pacer.roll(sunny(weather="rain"))


def test_offline_keeps_the_last_known_weather():
    pacer, _clock, store = make(dice=0.99)
    pacer.roll(sunny(weather="rain"))
    pacer.roll(sunny(weather=None))
    assert store["state"]["last_weather"] == "rain"


def test_checks_are_half_an_hour_apart():
    pacer, clock, _store = make(dice=0.99)
    assert pacer.check_due()
    pacer.roll(sunny())
    assert not pacer.check_due()
    clock.advance(29)
    assert not pacer.check_due()
    clock.advance(1)
    assert pacer.check_due()


def test_at_most_so_many_a_day_and_never_close_together():
    pacer, clock, _store = make()
    assert pacer.room_for_ambient(TODAY, "normal")
    pacer.ambient_done(TODAY, happened=True)
    assert not pacer.room_for_ambient(TODAY, "normal")      # too soon
    clock.advance(pacing.MIN_GAP / 60)
    assert pacer.room_for_ambient(TODAY, "normal")
    pacer.ambient_done(TODAY, happened=True)
    clock.advance(pacing.MIN_GAP / 60)
    assert not pacer.room_for_ambient(TODAY, "normal")      # two today already
    assert pacer.room_for_ambient(TODAY, "often")
    assert pacer.room_for_ambient("2026-10-15", "normal")    # a new day


def test_looking_around_yourself_also_keeps_them_apart():
    pacer, clock, _store = make()
    pacer.manual_done()
    assert not pacer.room_for_ambient(TODAY, "often")
    clock.advance(pacing.MIN_GAP / 60)
    assert pacer.room_for_ambient(TODAY, "often")
    assert pacer.ambient_left(TODAY, "often") == 3           # it doesn't use one up


def test_one_waiting_for_a_pause_doesnt_wait_forever():
    pacer, clock, _store = make(dice=0.0)
    pacer.roll(sunny())
    assert pacer.pending_state() == "waiting"
    clock.advance(pacing.PENDING_LIMIT / 60 + 1)
    assert pacer.pending_state() == "expired"
    pacer.drop_pending()
    assert pacer.pending_state() == "none"


def test_one_that_didnt_come_doesnt_count():
    pacer, _clock, _store = make(dice=0.0)
    pacer.roll(sunny())
    pacer.ambient_done(TODAY, happened=False)
    assert pacer.pending_state() == "none"
    assert pacer.ambient_left(TODAY, "rare") == 1


def test_seen_today():
    pacer, _clock, _store = make()
    pacer.add_seen(TODAY, 54)
    pacer.add_seen(TODAY, 54)
    assert pacer.seen(TODAY) == {54}
    assert pacer.seen("2026-10-15") == set()


def test_a_broken_state_is_ignored():
    pacer, _clock, _store = make(saved={"count": "lots", "seen": "x", "last_manual": -5,
                                        "pending": True, "date": 7})
    assert pacer.state["count"] == 0 and pacer.state["seen"] == []
    assert pacer.manual_wait() == 0 and pacer.pending_state() == "none"


# --- quiet hours ------------------------------------------------------------------------------------

@pytest.mark.parametrize("hour, quiet", [(21, False), (22, True), (23, True), (0, True),
                                          (6, True), (7, False), (12, False)])
def test_quiet_hours_over_midnight(hour, quiet):
    assert pacing.is_quiet(datetime.datetime(2026, 10, 14, hour, 30), 22, 7) is quiet


def test_quiet_hours_in_the_day_and_none():
    assert pacing.is_quiet(datetime.datetime(2026, 10, 14, 13, 0), 12, 14)
    assert not pacing.is_quiet(datetime.datetime(2026, 10, 14, 14, 0), 12, 14)
    assert not pacing.is_quiet(datetime.datetime(2026, 10, 14, 3, 0), 5, 5)


def test_quiet_hours_end():
    end = pacing.quiet_ends(datetime.datetime(2026, 10, 14, 23, 10), 22, 7)
    assert end == datetime.datetime(2026, 10, 15, 7, 0)
