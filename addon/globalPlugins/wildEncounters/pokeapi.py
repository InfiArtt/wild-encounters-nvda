# Wild Encounters for NVDA: an unofficial Pokémon fan add-on.
# Copyright (C) 2026 Rafli. Ported from the Wild Encounters extension for Hariku V2.
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License, version 2 or (at your option) any later version.
# Distributed WITHOUT ANY WARRANTY. See COPYING.txt.
#
# SPDX-License-Identifier: GPL-2.0-or-later


"""
Names and facts about Pokémon from PokéAPI (https://pokeapi.co, free, no
key). Nothing about Pokémon comes with the add-on: it is all fetched here,
when it is first needed. Blocking calls: the plugin runs them on a worker
thread. No wx or NVDA here.

PokéAPI asks everyone who uses it to cache what they fetch, and to be gentle.
So:
  * every resource is kept on this computer (the add-on's folder in NVDA's settings,
    pokeapi\\<kind>\\<key>.json) for CACHE_DAYS (30) days, and used without
    asking again; an older copy is still used when PokéAPI can't be reached;
  * requests are at least MIN_INTERVAL (0.6) seconds apart, one at a time,
    at most DAILY_LIMIT (300) a day, with a User-Agent that names Wild
    Encounters for NVDA;
  * after a failure nothing is asked for a while: 5 minutes, doubling up to
    an hour, and an hour straight away after "too many requests" (429).

Only the facts the add-on uses are kept: names, types, height, weight,
generation, evolutions, legendary or mythical, capture rate, habitat and
colour. Each answer is trimmed to those fields (a list of what is kept, never
of what is left out) before anything is written: no pictures, no sounds, no
descriptions from the games. What is sent: the address of the resource, such
as /pokemon-species/54/. Nothing about the user.
"""

import datetime
import http.client
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import meta

logger = logging.getLogger(__name__)

API = "https://pokeapi.co/api/v2/"
HOST = "pokeapi.co"
CACHE_DAYS = 30
MIN_INTERVAL = 0.6
TIMEOUT = 12
MAX_BYTES = 6 * 1024 * 1024
DAILY_LIMIT = 300
BACKOFF_FIRST = 5 * 60
BACKOFF_MAX = 60 * 60
RATE_BACKOFF = 60 * 60
MAX_ID = 10000                  # higher ids are alternate forms (megas, regional forms...)
MAX_CHAIN_DEPTH = 6
INDEX_KEY = "all"
CACHE_VERSION = 1

KINDS = ("type", "pokemon-species", "pokemon", "evolution-chain", "pokemon-species-index")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ID_RE = re.compile(r"/(?:pokemon|pokemon-species|evolution-chain)/(\d+)/?$")
_GENERATION_RE = re.compile(r"^generation-([ivx]+)$")
_ROMAN = {"i": 1, "v": 5, "x": 10}


class PokeAPIError(Exception):
    """kind: "offline", "http", "rate", "not_found", "bad_data", "host",
    "backoff" (resting after a failure), "limit" (the day's requests are
    used up), "bad_key"."""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


def user_agent():
    return meta.USER_AGENT


def _open(url, timeout=TIMEOUT):
    """GET url -> (body bytes, final url)."""
    request = urllib.request.Request(url, headers={"User-Agent": user_agent(),
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read(MAX_BYTES + 1)
        return body, response.geturl()


# ------------------------------------------------------------
# Small checks
# ------------------------------------------------------------

def check_key(key):
    """A cache key (a name like "mr-mime" or a number) that is safe as a file
    name and in a URL, as a string; PokeAPIError("bad_key") otherwise."""
    if isinstance(key, bool):
        raise PokeAPIError("bad_key", repr(key))
    if isinstance(key, int):
        if 0 < key < 100000:
            return str(key)
        raise PokeAPIError("bad_key", repr(key))
    key = str(key or "").strip().lower()
    if not _KEY_RE.match(key):
        raise PokeAPIError("bad_key", repr(key))
    return key


def id_from_url(url):
    """The number at the end of a PokéAPI resource URL, or None."""
    match = _ID_RE.search(str(url or ""))
    return int(match.group(1)) if match else None


def generation_number(name):
    """ "generation-iv" -> 4; None for anything else."""
    match = _GENERATION_RE.match(str(name or ""))
    if not match:
        return None
    total, previous = 0, 0
    for letter in reversed(match.group(1)):
        value = _ROMAN[letter]
        total = total - value if value < previous else total + value
        previous = max(previous, value)
    return total or None


def _name(value):
    """A resource's "name" (a slug), or None."""
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, str) and _KEY_RE.match(value):
        return value
    return None


def _int(value, low=0, high=10 ** 6):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = int(value)
    return value if low <= value <= high else None


def _english_name(names):
    """The English name from a "names" list, cleaned up, or ""."""
    for item in names if isinstance(names, list) else []:
        if not isinstance(item, dict):
            continue
        language = item.get("language")
        if _name(language) == "en" and isinstance(item.get("name"), str):
            text = "".join(c for c in item["name"] if c.isprintable()).strip()
            return " ".join(text.split())[:40]
    return ""


def display_from_slug(slug):
    """A readable name made from a slug, for when the real one isn't known:
    "mr-mime" -> "Mr Mime"."""
    return " ".join(part.capitalize() for part in str(slug or "").split("-") if part)


# ------------------------------------------------------------
# Trimming: only the facts the add-on uses are kept
# ------------------------------------------------------------

def trim_type(raw):
    if not isinstance(raw, dict) or not _name(raw):
        raise PokeAPIError("bad_data", "no type")
    members = []
    seen = set()
    for item in raw.get("pokemon") if isinstance(raw.get("pokemon"), list) else []:
        entry = item.get("pokemon") if isinstance(item, dict) else None
        slug = _name(entry)
        number = id_from_url(entry.get("url")) if isinstance(entry, dict) else None
        if slug and number and number < MAX_ID and number not in seen:
            seen.add(number)
            members.append({"name": slug, "id": number})
    return {"name": _name(raw), "pokemon": members}


def trim_species(raw):
    if not isinstance(raw, dict) or _int(raw.get("id"), 1, MAX_ID) is None or not _name(raw):
        raise PokeAPIError("bad_data", "no species")
    evolves = raw.get("evolves_from_species")
    chain = raw.get("evolution_chain")
    default = None
    for variety in raw.get("varieties") if isinstance(raw.get("varieties"), list) else []:
        if isinstance(variety, dict) and variety.get("is_default") is True:
            default = id_from_url((variety.get("pokemon") or {}).get("url"))
    return {
        "id": int(raw["id"]),
        "name": _name(raw),
        "display": _english_name(raw.get("names")) or display_from_slug(_name(raw)),
        "capture_rate": _int(raw.get("capture_rate"), 0, 255),
        "is_legendary": raw.get("is_legendary") is True,
        "is_mythical": raw.get("is_mythical") is True,
        "is_baby": raw.get("is_baby") is True,
        "generation": generation_number(_name(raw.get("generation"))),
        "color": _name(raw.get("color")),
        "habitat": _name(raw.get("habitat")),
        "evolves_from": ({"name": _name(evolves), "id": id_from_url(evolves.get("url"))}
                         if isinstance(evolves, dict) and _name(evolves) else None),
        "chain_id": id_from_url(chain.get("url")) if isinstance(chain, dict) else None,
        "pokemon_id": default if default and default < MAX_ID else int(raw["id"]),
    }


def trim_pokemon(raw):
    if not isinstance(raw, dict) or _int(raw.get("id"), 1, MAX_ID) is None or not _name(raw):
        raise PokeAPIError("bad_data", "no pokemon")
    slots = []
    for item in raw.get("types") if isinstance(raw.get("types"), list) else []:
        if isinstance(item, dict) and _name(item.get("type")):
            slots.append((_int(item.get("slot"), 0, 9) or 9, _name(item.get("type"))))
    return {
        "id": int(raw["id"]),
        "name": _name(raw),
        "height": _int(raw.get("height"), 0, 100000),     # decimetres
        "weight": _int(raw.get("weight"), 0, 100000),     # hectograms
        "types": [name for _slot, name in sorted(slots)],
    }


def _trim_link(node, depth=0):
    if not isinstance(node, dict) or depth > MAX_CHAIN_DEPTH:
        return None
    species = node.get("species")
    slug = _name(species)
    if not slug:
        return None
    later = node.get("evolves_to")
    nexts = [_trim_link(n, depth + 1) for n in (later if isinstance(later, list) else [])]
    return {"name": slug, "id": id_from_url(species.get("url")),
            "to": [n for n in nexts if n]}


def trim_chain(raw):
    if not isinstance(raw, dict) or _int(raw.get("id"), 1, 100000) is None:
        raise PokeAPIError("bad_data", "no chain")
    chain = _trim_link(raw.get("chain"))
    if chain is None:
        raise PokeAPIError("bad_data", "empty chain")
    return {"id": int(raw["id"]), "chain": chain}


def trim_index(raw):
    results = raw.get("results") if isinstance(raw, dict) else None
    if not isinstance(results, list):
        raise PokeAPIError("bad_data", "no list")
    species = []
    for item in results:
        slug = _name(item)
        number = id_from_url(item.get("url")) if isinstance(item, dict) else None
        if slug and number and number < MAX_ID:
            species.append({"name": slug, "id": number})
    if not species:
        raise PokeAPIError("bad_data", "empty list")
    return {"species": species}


# ------------------------------------------------------------
# The cache on disk
# ------------------------------------------------------------

class Cache:
    """pokeapi\\<kind>\\<key>.json under `folder`: {"v", "fetched_at",
    "data"}. Written whole (a temporary file, then replaced)."""

    def __init__(self, folder, clock=time.time):
        self.folder = os.path.join(folder, "pokeapi")
        self._clock = clock

    def path(self, kind, key):
        if kind not in KINDS:
            raise PokeAPIError("bad_key", kind)
        return os.path.join(self.folder, kind, check_key(key) + ".json")

    def read(self, kind, key):
        """(data, fetched_at), or (None, None) when it isn't there or is broken."""
        try:
            with open(self.path(kind, key), encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, ValueError, PokeAPIError):
            return None, None
        if (not isinstance(stored, dict) or stored.get("v") != CACHE_VERSION
                or not isinstance(stored.get("data"), dict)
                or not isinstance(stored.get("fetched_at"), (int, float))):
            return None, None
        return stored["data"], float(stored["fetched_at"])

    def write(self, kind, key, data):
        path = self.path(kind, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temporary = path + ".tmp"
        try:
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump({"v": CACHE_VERSION, "fetched_at": self._clock(), "data": data}, f,
                          ensure_ascii=False, separators=(",", ":"))
            os.replace(temporary, path)
        except OSError:
            logger.exception("[Wild Encounters] A PokéAPI answer couldn't be cached")
            try:
                os.remove(temporary)
            except OSError:
                pass

    def size(self):
        """(files, bytes) in the cache."""
        files = total = 0
        for root, _dirs, names in os.walk(self.folder):
            for name in names:
                if name.endswith(".json"):
                    files += 1
                    try:
                        total += os.path.getsize(os.path.join(root, name))
                    except OSError:
                        pass
        return files, total


# ------------------------------------------------------------
# The client
# ------------------------------------------------------------

class PokeAPI:
    """`folder`: where the cache goes (the add-on's data folder).
    `opener(url)` returns (body, final url) and raises urllib's errors;
    `clock()` is the wall clock (the cache's age, the day's count),
    `monotonic()` and `sleep(seconds)` are for the pause between requests."""

    def __init__(self, folder, opener=None, clock=time.time, monotonic=time.monotonic,
                 sleep=time.sleep, cache_days=CACHE_DAYS):
        self.cache = Cache(folder, clock)
        self._opener = opener or _open
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self.max_age = cache_days * 86400
        self._lock = threading.Lock()
        self._last = None
        self._day = None
        self._failures = 0
        self._resting_until = 0.0
        self.requests = 0            # sent today
        self.sent = []               # every URL sent (for the tests and the log)

    # --- the pause, the day's limit, resting after failures --------------------------------

    def wait_needed(self):
        if self._last is None:
            return 0.0
        return max(0.0, MIN_INTERVAL - (self._monotonic() - self._last))

    def resting(self):
        """Seconds until PokéAPI is asked again after a failure (0: now)."""
        return max(0.0, self._resting_until - self._clock())

    def _today(self):
        return datetime.date.fromtimestamp(self._clock()).isoformat()

    def _failed(self, rate=False):
        self._failures += 1
        if rate:
            pause = RATE_BACKOFF
        else:
            pause = min(BACKOFF_FIRST * 2 ** (self._failures - 1), BACKOFF_MAX)
        self._resting_until = self._clock() + pause

    def _fetch(self, path):
        """GET API + path -> its JSON (a dict). Raises PokeAPIError."""
        with self._lock:
            if self.resting() > 0:
                raise PokeAPIError("backoff", f"{int(self.resting())} s")
            today = self._today()
            if self._day != today:
                self._day, self.requests = today, 0
            if self.requests >= DAILY_LIMIT:
                raise PokeAPIError("limit", str(DAILY_LIMIT))
            wait = self.wait_needed()
            if wait > 0:
                self._sleep(wait)
            url = API + path
            self.requests += 1
            self.sent.append(url)
            try:
                body, final = self._opener(url)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise PokeAPIError("not_found", path) from None
                self._failed(rate=e.code == 429)
                raise PokeAPIError("rate" if e.code == 429 else "http", str(e.code)) from None
            except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
                self._failed()
                raise PokeAPIError("offline", str(getattr(e, "reason", e))) from None
            finally:
                self._last = self._monotonic()
        if urllib.parse.urlsplit(str(final)).hostname != HOST:
            raise PokeAPIError("host", str(final))
        if len(body) > MAX_BYTES:
            raise PokeAPIError("bad_data", "too large")
        try:
            answer = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise PokeAPIError("bad_data", "not JSON") from None
        if not isinstance(answer, dict):
            raise PokeAPIError("bad_data", "not an object")
        self._failures = 0
        return answer

    # --- resources -----------------------------------------------------------------------------

    def _get(self, kind, key, path, trim, offline=False):
        """The resource from the cache when it is fresh; else fetched,
        trimmed and cached; else the old copy. offline: the cache only."""
        key = check_key(key)
        cached, fetched_at = self.cache.read(kind, key)
        if cached is not None and 0 <= self._clock() - fetched_at <= self.max_age:
            return cached
        if offline:
            if cached is not None:
                return cached
            raise PokeAPIError("offline", "not cached")
        try:
            data = trim(self._fetch(path))
        except PokeAPIError as e:
            if cached is not None and e.kind != "not_found":
                logger.info(f"[Wild Encounters] Using the cached {kind}/{key}: {e}")
                return cached
            raise
        self.cache.write(kind, key, data)
        return data

    def type_members(self, name, offline=False):
        """{"name", "pokemon": [{"name", "id"}]}: the species of one type
        (their usual forms only)."""
        key = check_key(name)
        return self._get("type", key, f"type/{key}/", trim_type, offline)

    def species(self, number, offline=False):
        number = int(number)
        return self._get("pokemon-species", number, f"pokemon-species/{check_key(number)}/",
                         trim_species, offline)

    def pokemon(self, number, offline=False):
        number = int(number)
        return self._get("pokemon", number, f"pokemon/{check_key(number)}/", trim_pokemon,
                         offline)

    def evolution_chain(self, number, offline=False):
        number = int(number)
        return self._get("evolution-chain", number, f"evolution-chain/{check_key(number)}/",
                         trim_chain, offline)

    def species_index(self, offline=False):
        """{"species": [{"name", "id"}]}: every species, one request."""
        return self._get("pokemon-species-index", INDEX_KEY,
                         "pokemon-species/?limit=100000&offset=0", trim_index, offline)

    def is_cached(self, kind, key):
        return self.cache.read(kind, key)[0] is not None
