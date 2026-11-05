# Wild Encounters for NVDA: an unofficial Pokémon fan add-on.
# Copyright (C) 2026 Rafli. Ported from the Wild Encounters extension for Hariku V2.
#
# This program is free software: you can redistribute it and/or modify it under the terms of
# the GNU General Public License, version 2 or (at your option) any later version.
# Distributed WITHOUT ANY WARRANTY. See COPYING.txt.
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""
Where home is, from whatever the user pastes. No wx or NVDA here; resolve()
blocks, so the settings panel runs it on a worker thread.

What is understood:
  * a Google Maps link: the place's pin (!3d...!4d...), q= / query= /
    ll= / center= / destination=, the map's centre (@lat,lon,zoom), or
    coordinates in the path (/search/-6.2,106.8);
  * a short Google Maps link (maps.app.goo.gl/..., goo.gl/maps/...): its
    redirects are followed, one at a time, only to Google's own addresses,
    until one shows where the place is;
  * OpenStreetMap (mlat/mlon, #map=zoom/lat/lon), Apple Maps (ll=, q=,
    coordinate=), Bing (cp=lat~lon), Waze (ll=), any link with lat= and
    lon= (or lng=);
  * a geo: link, plain coordinates ("-6.2088, 106.8456"), or degrees,
    minutes and seconds (6°12'31.7"S 106°50'44.2"E);
  * anything else is taken as a city's name and looked up with Open-Meteo's
    geocoding ("Batam", or "Springfield, United States").
Then the town's name is looked up from the coordinates with OpenStreetMap's
Nominatim, so encounters can say "raining over Batam"; the user can change
it.

Privacy: only the point rounded to DECIMALS (2, about 1 km) is kept and sent
anywhere; the exact one is forgotten here.
"""

import http.client
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import meta

DECIMALS = 2
TIMEOUT = 10
MAX_BYTES = 1024 * 1024
MAX_HOPS = 6
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
GEOCODE_HOST = "geocoding-api.open-meteo.com"
REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
REVERSE_HOST = "nominatim.openstreetmap.org"
REVERSE_INTERVAL = 1.1          # Nominatim asks for at most one request a second
SHORT_HOSTS = ("maps.app.goo.gl", "goo.gl", "g.co", "maps.google.com", "www.google.com",
               "google.com")
MAX_NAME = 60

_NUM = r"[-+]?\d{1,3}(?:\.\d+)?"
_PIN_RE = re.compile(rf"!3d({_NUM})!4d({_NUM})")
_AT_RE = re.compile(rf"/@({_NUM}),\s*({_NUM})")
_PAIR_RE = re.compile(rf"^\s*\(?\s*({_NUM})\s*[,;\s]\s*({_NUM})\s*\)?\s*$")
_PATH_PAIR_RE = re.compile(rf"/({_NUM}),\s*({_NUM})(?=[/,?]|$)")
_OSM_MAP_RE = re.compile(rf"map=\d+(?:\.\d+)?/({_NUM})/({_NUM})")
_GEO_RE = re.compile(rf"^geo:({_NUM}),({_NUM})", re.I)
_DMS_PART = r"(\d{1,3})\s*[°º:\s]\s*(?:(\d{1,2}(?:\.\d+)?)\s*['′’:\s]\s*)?" \
            r"(?:(\d{1,2}(?:\.\d+)?)\s*(?:\"|″|”|''|’’)?\s*)?([NSEWnsew])"
_DMS_RE = re.compile(rf"^\s*{_DMS_PART}[\s,;]*{_DMS_PART}\s*$")
# In a page's text, when a link leads to a page instead of a place.
_BODY_PATTERNS = (_PIN_RE, _AT_RE,
                  re.compile(rf"center=({_NUM})%2C({_NUM})"),
                  re.compile(rf"\[null,null,({_NUM}),({_NUM})\]"))
_last_reverse = [0.0]
_reverse_lock = threading.Lock()


class LocationError(Exception):
    """kind: "empty", "no_coords" (a link that doesn't say where), "bad"
    (not a place on Earth), "not_found" (no city by that name), "offline",
    "host" (a link that leads somewhere unexpected)."""

    def __init__(self, kind, detail=""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


class Place:
    """A home: `lat`, `lon` (rounded), `name` (said in encounters, may be
    ""), `label` (longer, for the settings: "Batam, Riau Islands,
    Indonesia")."""

    def __init__(self, lat, lon, name="", label=""):
        self.lat, self.lon = rounded(lat, lon)
        self.name = clean_name(name)
        self.label = clean_name(label, 120) or self.name

    def as_dict(self):
        return {"lat": self.lat, "lon": self.lon, "name": self.name}

    def __repr__(self):
        return f"Place({self.lat}, {self.lon}, {self.name!r})"


def clean_name(text, limit=MAX_NAME):
    text = "".join(c for c in str(text or "") if c.isprintable())
    return " ".join(text.split())[:limit]


def rounded(lat, lon):
    return round(float(lat), DECIMALS), round(float(lon), DECIMALS)


def valid(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


def _pair(match):
    if match is None:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    return (lat, lon) if valid(lat, lon) else None


# ------------------------------------------------------------
# Reading what was pasted
# ------------------------------------------------------------

def _unquote(text):
    for _ in range(3):
        decoded = urllib.parse.unquote(text)
        if decoded == text:
            break
        text = decoded
    return text


def _from_text_pair(value):
    """ "-6.2,106.8" (or with a space, or ~ as Bing has it) -> (lat, lon)."""
    return _pair(_PAIR_RE.match(_unquote(str(value or "")).replace("+", " ").replace("~", ",")))


def coords_from_url(url):
    """(lat, lon) from a map link, or None when it doesn't say."""
    url = str(url or "").strip()
    whole = _unquote(url)
    pins = list(_PIN_RE.finditer(whole))
    if pins:
        found = _pair(pins[-1])                     # the place's own pin
        if found:
            return found
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qs(parts.query)
    fragment = urllib.parse.parse_qs(parts.fragment)
    for key in ("q", "query", "ll", "sll", "center", "destination", "daddr", "coordinate",
                "cp", "latlng", "location", "point"):
        for value in query.get(key, []):
            found = _from_text_pair(value)
            if found:
                return found
    for source in (query, fragment):
        for lat_key, lon_key in (("mlat", "mlon"), ("lat", "lon"), ("lat", "lng"),
                                 ("latitude", "longitude")):
            if source.get(lat_key) and source.get(lon_key):
                try:
                    lat, lon = float(source[lat_key][0]), float(source[lon_key][0])
                except ValueError:
                    continue
                if valid(lat, lon):
                    return lat, lon
    for pattern in (_OSM_MAP_RE, _AT_RE):
        found = _pair(pattern.search(whole))
        if found:
            return found
    return _pair(_PATH_PAIR_RE.search(_unquote(parts.path).replace("+", "")))


def _dms(match, start):
    degrees = float(match.group(start))
    minutes = float(match.group(start + 1) or 0)
    seconds = float(match.group(start + 2) or 0)
    if minutes >= 60 or seconds >= 60:
        return None, None
    value = degrees + minutes / 60 + seconds / 3600
    side = match.group(start + 3).upper()
    return (-value if side in "SW" else value), side


def coords_from_dms(text):
    match = _DMS_RE.match(text)
    if not match:
        return None
    first, first_side = _dms(match, 1)
    second, second_side = _dms(match, 5)
    if first is None or second is None:
        return None
    if first_side in "EW" and second_side in "NS":
        first, second = second, first
    elif not (first_side in "NS" and second_side in "EW"):
        return None
    return (first, second) if valid(first, second) else None


def looks_like_link(text):
    lowered = text.lower()
    return lowered.startswith(("http://", "https://", "www.", "maps.", "goo.gl/",
                               "maps.app.goo.gl/", "g.co/"))


def is_short_link(url):
    parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    host = (parts.hostname or "").lower()
    if host == "maps.app.goo.gl":
        return True
    if host == "goo.gl" and parts.path.startswith("/maps"):
        return True
    if host == "g.co" and parts.path.startswith("/kgs"):
        return True
    return False


def parse(text):
    """What the user pasted -> ("coords", (lat, lon)), ("short", url) or
    ("search", name). Raises LocationError."""
    text = " ".join(str(text or "").split())
    if not text:
        raise LocationError("empty")
    geo = _GEO_RE.match(text)
    if geo:
        found = _pair(geo)
        if not found:
            raise LocationError("bad", text)
        return "coords", found
    if looks_like_link(text):
        url = text if "://" in text else "https://" + text
        found = coords_from_url(url)
        if found:
            return "coords", found
        if is_short_link(url):
            return "short", url
        raise LocationError("no_coords", url)
    plain = _PAIR_RE.match(text)
    if plain:
        found = _pair(plain)
        if not found:
            raise LocationError("bad", text)
        return "coords", found
    dms = coords_from_dms(text)
    if dms:
        return "coords", dms
    if any(c.isdigit() for c in text) and not any(c.isalpha() for c in text):
        raise LocationError("bad", text)
    return "search", text


# ------------------------------------------------------------
# The network (blocking)
# ------------------------------------------------------------

def _google_host(host):
    host = (host or "").lower()
    return (host in SHORT_HOSTS or host == "consent.google.com"
            or re.fullmatch(r"(www\.|maps\.)?google\.[a-z]{2,3}(\.[a-z]{2})?", host) is not None)


def _request_once(url):
    """GET without following redirects -> (status, Location header, body)."""
    parts = urllib.parse.urlsplit(url)
    connection = http.client.HTTPSConnection(parts.hostname, timeout=TIMEOUT)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    try:
        connection.request("GET", path, headers={"User-Agent": meta.USER_AGENT,
                                                 "Accept-Language": "en"})
        response = connection.getresponse()
        location = response.getheader("Location") or ""
        body = response.read(MAX_BYTES) if response.status == 200 else b""
        return response.status, location, body
    finally:
        connection.close()


def expand_short_link(url, request=None):
    """Follow a short map link's redirects (only to Google) until one says
    where the place is -> (lat, lon). Raises LocationError."""
    request = request or _request_once
    for _hop in range(MAX_HOPS):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or not _google_host(parts.hostname):
            raise LocationError("host", url)
        found = coords_from_url(url)
        if found:
            return found
        if (parts.hostname or "").lower() == "consent.google.com":
            target = urllib.parse.parse_qs(parts.query).get("continue", [""])[0]
            if target:
                url = target
                continue
            raise LocationError("no_coords", url)
        try:
            status, location, body = request(url)
        except (OSError, http.client.HTTPException) as e:
            raise LocationError("offline", str(e)) from None
        if status in (301, 302, 303, 307, 308) and location:
            url = urllib.parse.urljoin(url, location)
            continue
        if status == 200:
            text = _unquote(body.decode("utf-8", "replace"))
            for pattern in _BODY_PATTERNS:
                found = _pair(pattern.search(text))
                if found:
                    return found
            raise LocationError("no_coords", url)
        if status == 404:
            raise LocationError("no_coords", f"{status} {url}")
        raise LocationError("offline", f"HTTP {status}")
    raise LocationError("no_coords", "too many redirects")


def _get_json(url, host):
    request = urllib.request.Request(url, headers={"User-Agent": meta.USER_AGENT,
                                                   "Accept": "application/json",
                                                   "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_BYTES + 1)
            final = response.geturl()
    except urllib.error.HTTPError as e:
        raise LocationError("offline", f"HTTP {e.code}") from None
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        raise LocationError("offline", str(getattr(e, "reason", e))) from None
    if urllib.parse.urlsplit(final).hostname != host or len(body) > MAX_BYTES:
        raise LocationError("host", final)
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise LocationError("offline", "not JSON") from None


def _label(*parts):
    seen = []
    for part in parts:
        part = clean_name(part)
        if part and part not in seen:
            seen.append(part)
    return ", ".join(seen)


def geocode(query, get_json=None):
    """A city by name, with Open-Meteo's geocoding -> Place. "Springfield,
    United States": the words after the comma pick among the results.
    Raises LocationError."""
    get_json = get_json or _get_json
    name, _comma, hint = query.partition(",")
    name, hint = name.strip(), hint.strip().casefold()
    if len(name) < 2:
        raise LocationError("not_found", query)
    url = GEOCODE_URL + "?" + urllib.parse.urlencode(
        {"name": name, "count": 10, "language": "en", "format": "json"})
    answer = get_json(url, GEOCODE_HOST)
    results = [r for r in (answer.get("results") or []) if isinstance(r, dict)
               and valid(r.get("latitude"), r.get("longitude"))]
    if not results:
        raise LocationError("not_found", query)
    chosen = results[0]
    if hint:
        for result in results:
            where = " ".join(str(result.get(k) or "") for k in
                             ("country", "country_code", "admin1", "admin2")).casefold()
            if hint in where:
                chosen = result
                break
    return Place(chosen["latitude"], chosen["longitude"], chosen.get("name"),
                 _label(chosen.get("name"), chosen.get("admin1"), chosen.get("country")))


def reverse(lat, lon, get_json=None, sleep=time.sleep, clock=time.monotonic):
    """The town at a (rounded) point with Nominatim -> (name, label); ("",
    "") when it can't be had (the user can type a name)."""
    get_json = get_json or _get_json
    lat, lon = rounded(lat, lon)
    url = REVERSE_URL + "?" + urllib.parse.urlencode(
        {"format": "jsonv2", "lat": f"{lat:.{DECIMALS}f}", "lon": f"{lon:.{DECIMALS}f}",
         "zoom": 10, "addressdetails": 1, "accept-language": "en"})
    with _reverse_lock:
        wait = REVERSE_INTERVAL - (clock() - _last_reverse[0])
        if wait > 0:
            sleep(wait)
        try:
            answer = get_json(url, REVERSE_HOST)
        except LocationError:
            return "", ""
        finally:
            _last_reverse[0] = clock()
    address = answer.get("address") if isinstance(answer, dict) else None
    if not isinstance(address, dict):
        return "", ""
    name = ""
    for key in ("city", "town", "village", "municipality", "suburb", "county", "state_district",
                "state"):
        if address.get(key):
            name = address[key]
            break
    return clean_name(name), _label(name, address.get("state"), address.get("country"))


def resolve(text, request=None, get_json=None):
    """What the user pasted -> Place (with a name when one can be had).
    Raises LocationError. Blocking."""
    kind, value = parse(text)
    if kind == "search":
        return geocode(value, get_json)
    if kind == "short":
        value = expand_short_link(value, request)
    place = Place(*value)
    name, label = reverse(place.lat, place.lon, get_json)
    place.name, place.label = name, label or name
    return place
