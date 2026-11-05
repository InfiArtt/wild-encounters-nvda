# Wild Encounters for NVDA: an unofficial Pokémon fan add-on.
# Copyright (C) 2026 Rafli. Ported from the Wild Encounters extension for Hariku V2.
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License, version 2 or (at your option) any later version.
# Distributed WITHOUT ANY WARRANTY. See COPYING.txt.
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""Small JSON files in the add-on's own folder in NVDA's settings
(%APPDATA%\\nvda\\wildEncounters, or the portable copy's), written whole: a
temporary file, then replaced. No NVDA here."""

import json
import logging
import os

logger = logging.getLogger(__name__)


class Store:
    def __init__(self, folder):
        self.folder = folder

    def path(self, name):
        return os.path.join(self.folder, name + ".json")

    def load(self, name):
        """The saved dict, or None when there is none (or it is broken)."""
        try:
            with open(self.path(name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def save(self, name, data):
        path = self.path(name)
        temporary = path + ".tmp"
        try:
            os.makedirs(self.folder, exist_ok=True)
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(temporary, path)
        except OSError:
            logger.exception(f"[Wild Encounters] {name} couldn't be saved")
            try:
                os.remove(temporary)
            except OSError:
                pass
