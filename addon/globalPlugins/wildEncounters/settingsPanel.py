# Wild Encounters for NVDA: an unofficial Pokémon fan add-on.
# Copyright (C) 2026 Rafli. Ported from the Wild Encounters extension for Hariku V2.
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License, version 2 or (at your option) any later version.
# Distributed WITHOUT ANY WARRANTY. See COPYING.txt.
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
The Wild Encounters page in NVDA's settings: whether Pokémon come by
themselves and how often at most, home (a map link, coordinates or a city,
looked up with Find this place on a worker thread), the name said in
encounters, a nickname, quiet hours, the birthday, the sounds and units, and
what the add-on sends where.

NVDA makes the panel itself, so the plugin is reached through `plugin`
(set by the GlobalPlugin while it runs).
"""

import threading
import time

import gui
import ui
import wx
from gui import guiHelper
from gui.settingsDialogs import SettingsPanel
from logHandler import log

from . import location, pacing, text
from .text import _

plugin = None
FIND_INTERVAL = 3.0             # seconds between two lookups, however often Find is pressed
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")


def home_text(place):
    if not place:
        return _("home_none")
    lat, lon = f"{place['lat']:.2f}", f"{place['lon']:.2f}"
    if place.get("name"):
        return _("home_value", name=place["name"], lat=lat, lon=lon)
    return _("home_value_unnamed", lat=lat, lon=lon)


def error_text(error, query=""):
    kind = getattr(error, "kind", "offline")
    if kind == "empty":
        return _("loc_empty")
    if kind == "bad":
        return _("loc_bad")
    if kind == "not_found":
        return _("loc_not_found", query=query)
    if kind == "no_coords":
        return _("loc_no_coords")
    if kind == "host":
        return _("loc_host")
    return _("loc_offline")


class WildEncountersPanel(SettingsPanel):
    title = "Wild Encounters"

    def makeSettings(self, settingsSizer):
        settings = plugin.app.settings
        self._found = None              # a place found and not saved yet
        self._found_for = ""            # what was typed to find it
        self._finding = False
        self._last_find = 0.0
        helper = guiHelper.BoxSizerHelper(self, sizer=settingsSizer)

        self.chk_enabled = helper.addItem(wx.CheckBox(self, label=_("chk_enabled")))
        self.chk_enabled.SetValue(settings["enabled"])
        self.cho_frequency = helper.addLabeledControl(
            _("lbl_frequency"), wx.Choice,
            choices=[_(f"freq_{f}") for f in pacing.FREQUENCIES])
        self.cho_frequency.SetSelection(pacing.FREQUENCIES.index(settings["frequency"]))

        self.txt_home = helper.addLabeledControl(
            _("lbl_home_now"), wx.TextCtrl, style=wx.TE_READONLY, size=(400, -1))
        self.txt_home.SetValue(home_text(settings["place"]))
        self.txt_location = helper.addLabeledControl(
            _("lbl_location"), wx.TextCtrl, style=wx.TE_PROCESS_ENTER, size=(400, -1))
        self.txt_location.Bind(wx.EVT_TEXT_ENTER, lambda event: self.on_find())
        self.btn_find = helper.addItem(wx.Button(self, label=_("btn_find")))
        self.btn_find.Bind(wx.EVT_BUTTON, lambda event: self.on_find())
        self.txt_name = helper.addLabeledControl(_("lbl_name"), wx.TextCtrl, size=(300, -1))
        self.txt_name.SetValue((settings["place"] or {}).get("name", ""))
        self.txt_name.SetMaxLength(60)

        self.txt_nickname = helper.addLabeledControl(_("lbl_nickname"), wx.TextCtrl)
        self.txt_nickname.SetValue(settings["nickname"])
        self.txt_nickname.SetMaxLength(30)

        hours = [text.hour_text(h) for h in range(24)]
        self.cho_quiet_from = helper.addLabeledControl(_("lbl_quiet_from"), wx.Choice,
                                                       choices=hours)
        self.cho_quiet_from.SetSelection(settings["quiet_from"])
        self.cho_quiet_until = helper.addLabeledControl(_("lbl_quiet_until"), wx.Choice,
                                                        choices=hours)
        self.cho_quiet_until.SetSelection(settings["quiet_until"])

        self.cho_month = helper.addLabeledControl(_("lbl_birthday_month"), wx.Choice,
                                                  choices=[_("birthday_none")] + list(MONTHS))
        self.spn_day = helper.addLabeledControl(_("lbl_birthday_day"), wx.SpinCtrl,
                                                min=1, max=31, initial=1)
        if settings["birthday"]:
            self.cho_month.SetSelection(int(settings["birthday"][:2]))
            self.spn_day.SetValue(int(settings["birthday"][3:]))
        else:
            self.cho_month.SetSelection(0)

        self.chk_sounds = helper.addItem(wx.CheckBox(self, label=_("chk_sounds")))
        self.chk_sounds.SetValue(settings["sounds"])
        self.chk_type_sounds = helper.addItem(wx.CheckBox(self, label=_("chk_type_sounds")))
        self.chk_type_sounds.SetValue(settings["type_sounds"])
        self.cho_units = helper.addLabeledControl(
            _("lbl_units"), wx.Choice, choices=[_("units_metric"), _("units_imperial")])
        self.cho_units.SetSelection(0 if settings["units"] == "metric" else 1)

        self.txt_about = helper.addLabeledControl(
            _("lbl_about"), wx.TextCtrl, style=wx.TE_MULTILINE | wx.TE_READONLY,
            size=(400, 120))
        self.txt_about.SetValue(text.about_page_text())

    # --- finding home -------------------------------------------------------------------------

    def on_find(self):
        query = self.txt_location.GetValue().strip()
        if not query:
            ui.message(_("loc_empty"))
            return
        if self._finding:
            ui.message(_("loc_working"))
            return
        wait = FIND_INTERVAL - (time.monotonic() - self._last_find)
        if wait > 0:
            return                      # pressed again right away: the answer is coming
        self._finding = True
        self._last_find = time.monotonic()
        ui.message(_("loc_working"))

        def work():
            found, error = None, None
            try:
                found = location.resolve(query)
            except location.LocationError as e:
                error = e
                log.info(f"Wild Encounters: no place for that: {e}")
            except Exception as e:
                error = e
                log.error("Wild Encounters: the place lookup failed", exc_info=True)
            wx.CallAfter(self._on_found, query, found, error)

        threading.Thread(target=work, daemon=True, name="wild-encounters-place").start()

    def _on_found(self, query, found, error):
        if not self:                    # the settings were closed meanwhile
            return
        self._finding = False
        if found is None:
            ui.message(error_text(error, query))
            return
        self._found, self._found_for = found, query
        if found.name:
            self.txt_name.SetValue(found.name)
        place = {"lat": found.lat, "lon": found.lon, "name": found.label or found.name}
        home = home_text(place)
        self.txt_home.SetValue(_("home_unsaved", home=home))
        ui.message(_("loc_found", home=home))

    # --- saving ----------------------------------------------------------------------------------

    def isValid(self):
        typed = self.txt_location.GetValue().strip()
        if typed and (self._found is None or typed != self._found_for):
            gui.messageBox(_("loc_press_find"), _("loc_press_find_title"),
                           wx.OK | wx.ICON_INFORMATION, self)
            self.txt_location.SetFocus()
            return False
        return super().isValid()

    def onSave(self):
        settings = plugin.app.settings
        place = settings["place"]
        if self._found is not None:
            place = {"lat": self._found.lat, "lon": self._found.lon}
        if place:
            place = dict(place, name=self.txt_name.GetValue().strip())
        month = self.cho_month.GetSelection()
        birthday = f"{month:02d}-{self.spn_day.GetValue():02d}" if month > 0 else ""
        was_on = settings["enabled"]
        plugin.save_settings({
            "enabled": self.chk_enabled.GetValue(),
            "frequency": pacing.FREQUENCIES[self.cho_frequency.GetSelection()],
            "place": place,
            "nickname": self.txt_nickname.GetValue().strip(),
            "quiet_from": self.cho_quiet_from.GetSelection(),
            "quiet_until": self.cho_quiet_until.GetSelection(),
            "birthday": birthday,
            "sounds": self.chk_sounds.GetValue(),
            "type_sounds": self.chk_type_sounds.GetValue(),
            "units": "metric" if self.cho_units.GetSelection() == 0 else "imperial",
        })
        self._found, self._found_for = None, ""
        self.txt_location.SetValue("")
        self.txt_home.SetValue(home_text(plugin.app.settings["place"]))
        if plugin.app.settings["enabled"] and not was_on:
            wx.CallAfter(plugin.turned_on)
