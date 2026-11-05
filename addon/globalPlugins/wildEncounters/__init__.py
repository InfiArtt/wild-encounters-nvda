# Wild Encounters for NVDA: an unofficial Pokémon fan add-on.
# Copyright (C) 2026 Rafli. Ported from the Wild Encounters extension for Hariku V2.
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License, version 2 or (at your option) any later version.
# Distributed WITHOUT ANY WARRANTY. See COPYING.txt.
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Wild Encounters for NVDA. Now and then NVDA says which Pokémon is around
you, from the real weather, time of day and moon at your home: "Psst! It's
raining over Batam... a Wingull is gliding just above the rooftops near
you." No battles, no catching: every one you meet goes into the Field
Journal.

NVDA+Alt+O, then:
  W   look around (every 20 minutes; pacing.py)
  J   the Field Journal
  L   the last encounter
  C   how many you've met
  S   the status: home, today's count, when you can look again
  O   the settings
  H   the keys
Each is also in Input gestures, Wild Encounters, to give it a key of its own.

By themselves (off until turned on in NVDA's settings, Wild Encounters):
once a minute the plugin asks pacing.py whether the weather should be looked
at; the weather decides whether someone comes by, and then the encounter
waits until the user pauses (no key for IDLE_MIN_SECONDS, no say all), so it
never talks over what NVDA is reading.

  conditions.py  weather, time of day, moon -> types
  pokeapi.py     PokéAPI, its cache and its pace
  weather.py     Open-Meteo, the rounded point
  location.py    a map link, coordinates or a city -> home
  pacing.py      when a Pokémon may appear (never spammed)
  encounter.py   an encounter, the facts
  journal.py     the Field Journal
  text.py        what is said (strings.json)
  storage.py     the JSON files in NVDA's settings folder
  ui.py          the Field Journal window
  settingsPanel.py  the settings page

Wild Encounters is a free, unofficial fan add-on. It is not affiliated with,
endorsed or sponsored by Nintendo, Game Freak, Creatures or The Pokémon
Company. Pokémon and Pokémon character names are trademarks of Nintendo.
Data from PokéAPI.
"""

import ctypes
import datetime
import os
import threading
import time

import api
import globalPluginHandler
import globalVars
import gui
import nvwave
import tones
import ui as nvdaUI
import wx
from gui import settingsDialogs
from logHandler import log
from scriptHandler import script

from . import conditions, encounter, journal, pacing, pokeapi, settingsPanel, storage, text
from . import ui, weather
from .text import _

ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
SOUNDS_DIR = os.path.join(ADDON_DIR, "sounds")
CATEGORY = "Wild Encounters"
TICK_MS = 60 * 1000
IDLE_MIN_SECONDS = 12           # one coming by itself waits for a pause this long...
IDLE_MAX_SECONDS = 30 * 60      # ...but not for someone who is away
RETRY_SECONDS = 10 * 60         # after the weather couldn't be looked at
LOOKING_DELAY_MS = 1500         # "Looking around..." when PokéAPI is slow
LAYER = {"kb:w": "lookAround", "kb:j": "journal", "kb:l": "last", "kb:c": "count",
         "kb:s": "status", "kb:o": "settings", "kb:h": "help"}


class _LastInput(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint32)]


def idle_seconds():
    """Seconds since the last key or mouse movement (0 when unknown)."""
    try:
        info = _LastInput()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        tick = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        return 0.0


def say_all_running():
    try:
        from speech.sayAll import SayAllHandler
        return bool(SayAllHandler.isRunning())
    except Exception:
        return False


class _Ticker(wx.Timer):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    def Notify(self):
        self._callback()


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = CATEGORY

    def __init__(self):
        super().__init__()
        self.app = None
        self._layer = False             # NVDA+Alt+O was pressed: the next key is ours
        if globalVars.appArgs.secure:
            return                      # nothing on secure screens
        folder = os.path.join(globalVars.appArgs.configPath, "wildEncounters")
        self.store = storage.Store(folder)
        self.app = encounter.WildApp(
            api=pokeapi.PokeAPI(folder),
            weather=weather.WeatherClient(),
            journal=journal.Journal(load=lambda: self.store.load("journal"),
                                    save=lambda data: self.store.save("journal", data)),
            pacer=pacing.Pacer(load=lambda: self.store.load("pacing"),
                               save=lambda state: self.store.save("pacing", state)),
            load_settings=lambda: self.store.load("settings"),
            save_settings=lambda data: self.store.save("settings", data))
        text.set_nickname(self.app.settings["nickname"])
        self._busy = False
        self._checking = False
        self._retry_after = 0.0
        self._window = None
        self._menu_item = None
        settingsPanel.plugin = self
        settingsDialogs.NVDASettingsDialog.categoryClasses.append(settingsPanel.WildEncountersPanel)
        self._add_menu_item()
        self._ticker = _Ticker(self.on_tick)
        self._ticker.Start(TICK_MS)

    def terminate(self):
        if self.app is None:
            return
        try:
            self._ticker.Stop()
        except Exception:
            pass
        try:
            settingsDialogs.NVDASettingsDialog.categoryClasses.remove(
                settingsPanel.WildEncountersPanel)
        except ValueError:
            pass
        if self._window is not None:
            try:
                self._window.Close()
            except Exception:
                pass
        self._remove_menu_item()
        settingsPanel.plugin = None
        self.app = None
        super().terminate()

    # --- the NVDA+Alt+O layer ---------------------------------------------------------------

    def getScript(self, gesture):
        """After NVDA+Alt+O, the next key is ours. The layer ends when that
        key's script runs (NVDA may look a script up more than once)."""
        if not self._layer or getattr(gesture, "isModifier", False):
            return super().getScript(gesture)
        for identifier in gesture.normalizedIdentifiers:
            name = LAYER.get(identifier.lower())
            if name:
                return self._ending_layer(getattr(self, "script_" + name))
        return self._ending_layer(self._layer_unknown)

    def _ending_layer(self, script_function):
        def run(gesture):
            self._layer = False
            script_function(gesture)
        run.__name__ = getattr(script_function, "__name__", "script_layerKey")
        return run

    @staticmethod
    def _layer_unknown(gesture):
        tones.beep(220, 60)

    @script(description=_("script_layer"), gesture="kb:NVDA+alt+o")
    def script_layer(self, gesture):
        if self.app is None:
            return
        self._layer = True
        tones.beep(660, 40)

    @script(description=_("script_look"))
    def script_lookAround(self, gesture):
        if self.app is not None:
            self.look_around()

    @script(description=_("script_journal"))
    def script_journal(self, gesture):
        if self.app is not None:
            wx.CallAfter(self.open_journal)

    @script(description=_("script_last"))
    def script_last(self, gesture):
        if self.app is not None:
            self.speak(text.last_text(self.app.journal.last, self.today()))

    @script(description=_("script_count"))
    def script_count(self, gesture):
        if self.app is not None:
            self.speak(text.summary_text(self.app.journal, self.today()))

    @script(description=_("script_status"))
    def script_status(self, gesture):
        if self.app is not None:
            self.speak(self.status_text())

    @script(description=_("script_settings"))
    def script_settings(self, gesture):
        if self.app is not None:
            wx.CallAfter(gui.mainFrame.popupSettingsDialog, settingsDialogs.NVDASettingsDialog,
                         settingsPanel.WildEncountersPanel)

    @script(description=_("script_help"))
    def script_help(self, gesture):
        self.speak(_("layer_help"))

    # --- what the window and the panel may do ----------------------------------------------

    @staticmethod
    def speak(message, interrupt=True):
        if message:
            nvdaUI.message(message)

    @staticmethod
    def today():
        return datetime.date.today()

    @staticmethod
    def copy(value):
        try:
            return bool(api.copyToClip(value, notify=False))
        except TypeError:
            return bool(api.copyToClip(value))
        except Exception:
            return False

    @staticmethod
    def confirm(parent, message, title):
        style = wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION
        return gui.messageBox(message, title, style, parent) == wx.YES

    def window_closed(self, window):
        if self._window is window:
            self._window = None

    def clear_journal(self):
        self.app.journal.clear()

    def save_settings(self, new_settings):
        self.app.save_settings(new_settings)
        text.set_nickname(self.app.settings["nickname"])

    def play(self, name):
        if self.app is None or not self.app.settings["sounds"]:
            return
        try:
            nvwave.playWaveFile(os.path.join(SOUNDS_DIR, name + ".wav"), asynchronous=True)
        except Exception:
            log.error("Wild Encounters: a sound couldn't be played", exc_info=True)

    def encounter_sound(self, found):
        if found.legendary or found.mythical:
            return "rare"
        if not self.app.settings["type_sounds"]:
            return "chime"
        return conditions.sound_family(found.type)

    def welcome(self, parent=None):
        """The first-run message, once. True when it was shown now."""
        if not self.app.mark_welcomed():
            return False
        gui.messageBox(text.welcome_text(), _("welcome_title"), wx.OK | wx.ICON_INFORMATION,
                       parent)
        return True

    def turned_on(self):
        """Coming by themselves was just turned on."""
        if self.app is None:
            return
        self.welcome(gui.mainFrame)
        if not self.app.main_place():
            self.speak(_("no_place"))

    # --- the Field Journal --------------------------------------------------------------------

    def open_journal(self):
        if self.app is None:
            return
        if self._window is None:
            gui.mainFrame.prePopup()
            self._window = ui.JournalWindow(gui.mainFrame, self)
            self._window.Show()
            gui.mainFrame.postPopup()
        else:
            self._window.Raise()
            self._window.lst_entries.SetFocus()
        self.welcome(self._window)

    def _add_menu_item(self):
        try:
            tools = gui.mainFrame.sysTrayIcon.toolsMenu
            self._menu_item = tools.Append(wx.ID_ANY, _("menu_journal"))
            gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, lambda event: self.open_journal(),
                                           self._menu_item)
        except Exception:
            log.error("Wild Encounters: the Tools menu item couldn't be added", exc_info=True)
            self._menu_item = None

    def _remove_menu_item(self):
        if self._menu_item is None:
            return
        try:
            gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self._menu_item)
        except Exception:
            pass
        self._menu_item = None

    # --- status ----------------------------------------------------------------------------------

    def status_text(self):
        settings = self.app.settings
        place = settings["place"]
        if not place:
            return _("no_place")
        if place.get("name"):
            parts = [_("status_home", place=place["name"])]
        else:
            parts = [_("status_home_unnamed", lat=f"{place['lat']:.2f}", lon=f"{place['lon']:.2f}")]
        now = datetime.datetime.now()
        if settings["enabled"]:
            limit = pacing.PER_DAY[settings["frequency"]]
            left = self.app.pacer.ambient_left(now.date().isoformat(), settings["frequency"])
            parts.append(_("status_on", count=limit - left, limit=limit))
            if pacing.is_quiet(now, settings["quiet_from"], settings["quiet_until"]):
                parts.append(_("status_quiet", time=text.hour_text(settings["quiet_until"])))
        else:
            parts.append(_("status_off"))
        wait = self.app.pacer.manual_wait()
        parts.append(_("status_wait", minutes=text.minutes_text(wait)) if wait > 0
                     else _("status_ready"))
        return " ".join(parts)

    # --- encounters ----------------------------------------------------------------------------

    def look_around(self):
        """W: an encounter now, every 20 minutes."""
        if self._busy:
            self.speak(_("busy"))
            return
        if not self.app.main_place():
            self.play("error")
            self.speak(_("no_place"))
            return
        wait = self.app.pacer.manual_wait()
        if wait > 0:
            self.play("rest")
            self.speak(_("cooldown", minutes=text.minutes_text(wait)))
            return
        self.play("search")
        self.run_encounter(ambient=False)

    def run_encounter(self, ambient=False):
        """Find who is around, on a worker thread, then record and announce."""
        if self._busy or self.app is None:
            return False
        self._busy = True
        app = self.app
        seen = app.seen_today()

        def work():
            found, error, facts = None, None, None
            try:
                found = app.encounter(ambient=ambient, seen=seen)
            except encounter.EncounterError as e:
                error = e.kind
                log.info(f"Wild Encounters: no encounter: {e}")
            except Exception:
                error = "offline"
                log.error("Wild Encounters: the encounter failed", exc_info=True)
            if found is not None:
                try:
                    facts = app.facts(found.species_id)
                except Exception as e:      # the scene is said without them
                    log.info(f"Wild Encounters: no facts for {found.species_id}: {e}")
            wx.CallAfter(self._encounter_done, app, found, error, ambient, facts)

        threading.Thread(target=work, daemon=True, name="wild-encounters").start()
        if not ambient:
            wx.CallLater(LOOKING_DELAY_MS, self._say_looking)
        return True

    def _say_looking(self):
        if self._busy and self.app is not None:
            self.speak(_("looking"))

    def _encounter_done(self, app, found, error, ambient, facts=None):
        self._busy = False
        if self.app is None or self.app is not app:
            return                      # NVDA is reloading its plugins
        today = self.today().isoformat()
        if found is None:
            if ambient:
                self.app.pacer.ambient_done(today, happened=False)
                if error == "offline":
                    self._retry_after = time.time() + RETRY_SECONDS
            else:
                self.play("error")
                self.speak(text.error_text(error))
            return
        entry, _first = self.app.record(found)
        if ambient:
            self.app.pacer.ambient_done(today, happened=True)
        else:
            self.app.pacer.manual_done()
        message = text.announcement(found)
        more = text.encounter_facts(facts, entry, self.app.settings["units"])
        if more:
            message = f"{message} {more}"
        self.play(self.encounter_sound(found))
        self.speak(message)
        if self._window is not None:
            try:
                self._window.refresh()
            except RuntimeError:
                pass

    def tell_about(self, number, done=None):
        """Say the facts about a species (fetched on a worker thread)."""
        app = self.app
        if app is None:
            return

        def work():
            facts = None
            try:
                facts = app.facts(number)
            except Exception as e:
                log.info(f"Wild Encounters: no facts for {number}: {e}")
            wx.CallAfter(finished, facts)

        def finished(facts):
            if self.app is not app:
                return
            entry = app.journal.get(number)
            if facts is not None:
                message = text.about_text(facts, entry, app.settings["units"], self.today())
            else:
                message = _("about_offline", name=entry["name"] if entry else str(number))
            self.speak(message)
            if done is not None:
                done(facts)

        threading.Thread(target=work, daemon=True, name="wild-encounters-facts").start()

    # --- by themselves ---------------------------------------------------------------------

    def ready_to_speak(self):
        """The user has paused (so NVDA said what it was saying) but isn't
        away, and NVDA isn't reading on and on."""
        idle = idle_seconds()
        if idle < IDLE_MIN_SECONDS or idle > IDLE_MAX_SECONDS:
            return False
        return not say_all_running()

    def on_tick(self):
        try:
            self._tick()
        except Exception:
            log.error("Wild Encounters: the minute check failed", exc_info=True)

    def _tick(self):
        app = self.app
        if app is None or self._busy or self._checking:
            return
        settings = app.settings
        place = settings["place"]
        if not settings["enabled"] or not place:
            return
        pacer = app.pacer
        now = datetime.datetime.now()
        if pacing.is_quiet(now, settings["quiet_from"], settings["quiet_until"]):
            pacer.drop_pending()
            return
        state = pacer.pending_state()
        if state == "waiting":
            if self.ready_to_speak():
                self.run_encounter(ambient=True)
            return
        if state == "expired":
            pacer.drop_pending()
        if time.time() < self._retry_after:
            return
        if not pacer.room_for_ambient(now.date().isoformat(), settings["frequency"]):
            return
        if not pacer.check_due():
            return
        self._checking = True

        def work():
            found = None
            try:
                found = app.conditions(place)
            except Exception:
                log.error("Wild Encounters: the weather check failed", exc_info=True)
            wx.CallAfter(self._checked, app, found)

        threading.Thread(target=work, daemon=True, name="wild-encounters-weather").start()

    def _checked(self, app, found):
        self._checking = False
        if self.app is not app:
            return
        if found is None:
            self._retry_after = time.time() + RETRY_SECONDS
            return
        if found.weather is None:
            self._retry_after = time.time() + RETRY_SECONDS     # offline: quietly, later
        if app.pacer.roll(found):
            log.debug(f"Wild Encounters: someone is coming by ({found!r})")
