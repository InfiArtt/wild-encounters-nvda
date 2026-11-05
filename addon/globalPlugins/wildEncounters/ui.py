# Wild Encounters for NVDA: an unofficial Pokémon fan add-on.
# Copyright (C) 2026 Rafli. Ported from the Wild Encounters extension for Hariku V2.
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License, version 2 or (at your option) any later version.
# Distributed WITHOUT ANY WARRANTY. See COPYING.txt.
#
# SPDX-License-Identifier: GPL-2.0-or-later


"""
The Field Journal window: every Pokémon the user has met, most recent first
("Psyduck: met 3 times, last today"), what the selected one's entry says,
and the buttons Tell me about it (Enter in the list does the same: the facts
are said, and shown under Details), Copy (a sentence to share, "I met a
Psyduck in the rain today!"), Clear the journal (after asking) and Close
(Escape). Under them, the disclaimer.

Every label is created right before the control it names (screen readers
name a control after the static text created just before it; SetName doesn't
change that).
"""

import wx

from . import text
from .text import _

_BORDER = 8


def _plain(label):
    return " ".join(label.replace("&&", "\0").replace("&", "").replace("\0", "&")
                    .strip().rstrip(":").split())


def _labeled(parent, sizer, label, make, proportion=0):
    """A label, then the control it names (created after it), with the same
    accessible name."""
    sizer.Add(wx.StaticText(parent, label=label), 0, wx.LEFT | wx.RIGHT | wx.TOP, _BORDER)
    ctrl = make()
    ctrl.SetName(_plain(label))
    sizer.Add(ctrl, proportion, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, _BORDER // 2)
    return ctrl


class JournalWindow(wx.Dialog):
    """`host` is the GlobalPlugin: app (journal, settings), speak(text),
    copy(text), confirm(parent, message, title), tell_about(number, done),
    clear_journal(), today(), window_closed(window)."""

    def __init__(self, parent, host):
        super().__init__(parent, title=_("window_title"), size=(600, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.host = host
        self.entries = []
        self._closed = False
        root = wx.BoxSizer(wx.VERTICAL)
        self.lst_entries = _labeled(self, root, _("lbl_list"),
                                    lambda: wx.ListBox(self, style=wx.LB_SINGLE), proportion=1)
        self.txt_details = _labeled(self, root, _("lbl_details"), lambda: wx.TextCtrl(
            self, size=(-1, 110), style=wx.TE_MULTILINE | wx.TE_READONLY))
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_tell = wx.Button(self, label=_("btn_tell"))
        self.btn_copy = wx.Button(self, label=_("btn_copy"))
        self.btn_clear = wx.Button(self, label=_("btn_clear"))
        self.btn_close = wx.Button(self, wx.ID_CANCEL, label=_("btn_close"))
        for button in (self.btn_tell, self.btn_copy, self.btn_clear, self.btn_close):
            buttons.Add(button, 0, wx.RIGHT, _BORDER // 2)
        root.Add(buttons, 0, wx.ALL, _BORDER)
        self.txt_about = _labeled(self, root, _("lbl_about"), lambda: wx.TextCtrl(
            self, value=text.disclaimer(), size=(-1, 70),
            style=wx.TE_MULTILINE | wx.TE_READONLY))
        root.AddSpacer(_BORDER)
        self.SetSizer(root)
        self.SetMinSize((440, 420))
        self.lst_entries.Bind(wx.EVT_LISTBOX, lambda e: self.show_details())
        self.btn_tell.Bind(wx.EVT_BUTTON, lambda e: self.on_tell())
        self.btn_copy.Bind(wx.EVT_BUTTON, lambda e: self.on_copy())
        self.btn_clear.Bind(wx.EVT_BUTTON, lambda e: self.on_clear())
        self.btn_close.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        self.Bind(wx.EVT_CHAR_HOOK, self._on_key)
        self.Bind(wx.EVT_CLOSE, self._on_close)
        self.SetEscapeId(wx.ID_CANCEL)
        self.refresh()
        self.lst_entries.SetFocus()
        self.CentreOnParent()

    # --- the list ------------------------------------------------------------------------

    def refresh(self):
        """The entries again, keeping the selected one."""
        chosen = self.selected()
        today = self.host.today()
        self.entries = self.host.app.journal.sorted_entries()
        self.lst_entries.Set([text.journal_line(e, today) for e in self.entries])
        if self.entries:
            ids = [e["id"] for e in self.entries]
            index = ids.index(chosen["id"]) if chosen and chosen["id"] in ids else 0
            self.lst_entries.SetSelection(index)
        for button in (self.btn_tell, self.btn_copy, self.btn_clear):
            button.Enable(bool(self.entries))
        self.show_details()

    def selected(self):
        index = self.lst_entries.GetSelection() if self.entries else wx.NOT_FOUND
        return self.entries[index] if 0 <= index < len(self.entries) else None

    def show_details(self):
        entry = self.selected()
        if entry is None:
            self.txt_details.ChangeValue(_("empty_journal"))
        else:
            self.txt_details.ChangeValue(text.details_text(entry, self.host.today()))

    # --- the buttons -----------------------------------------------------------------------

    def on_tell(self):
        entry = self.selected()
        if entry is None:
            return
        number = entry["id"]

        def done(facts):
            if self._closed or facts is None:
                return
            current = self.selected()
            if current is not None and current["id"] == number:
                try:
                    self.txt_details.ChangeValue(text.about_text(
                        facts, current, self.host.app.settings["units"], self.host.today()))
                except RuntimeError:
                    pass                    # the window is gone

        self.host.tell_about(number, done=done)

    def on_copy(self):
        entry = self.selected()
        if entry is None:
            return
        line = text.share_text(entry, self.host.today())
        if self.host.copy(line):
            self.host.speak(_("copied", text=line))
        else:
            self.host.speak(_("copy_failed"))

    def on_clear(self):
        if not self.entries:
            return
        if not self.host.confirm(self, _("confirm_clear"), _("confirm_clear_title")):
            return
        self.host.clear_journal()
        self.refresh()
        self.host.speak(_("journal_cleared"))

    # --- keys and closing ----------------------------------------------------------------------

    def _on_key(self, event):
        key = event.GetKeyCode()
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and \
                event.GetEventObject() is self.lst_entries:
            self.on_tell()
            return
        if key == wx.WXK_ESCAPE:
            self.Close()
            return
        event.Skip()

    def _on_close(self, event):
        self._closed = True
        self.host.window_closed(self)
        self.Destroy()
