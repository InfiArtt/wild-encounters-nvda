import pytest

from wildEncounters import location
from wildEncounters.location import LocationError


def coords(text):
    kind, value = location.parse(text)
    assert kind == "coords", (kind, value)
    return tuple(round(v, 4) for v in value)


@pytest.mark.parametrize("text, expected", [
    # Google Maps: the pin wins over the map's centre
    ("https://www.google.com/maps/place/Monumen+Nasional/@-6.1753924,106.8245779,17z/"
     "data=!3m1!4b1!4m6!3m5!1s0x2e69f5d2e764b12d:0x3d2ad6e1e0e9bcc8!8m2!3d-6.1753924"
     "!4d106.8271528!16zL20vMDJxMWJu?entry=ttu", (-6.1754, 106.8272)),
    ("https://www.google.com/maps/@1.1300000,104.0500000,15z", (1.13, 104.05)),
    ("https://www.google.co.id/maps/@-7.2504,112.7688,12z?hl=id", (-7.2504, 112.7688)),
    ("https://maps.google.com/?q=-6.2088,106.8456", (-6.2088, 106.8456)),
    ("https://www.google.com/maps?q=-6.2088%2C106.8456&z=15", (-6.2088, 106.8456)),
    ("https://www.google.com/maps/search/?api=1&query=47.5951518%2C-122.3316393",
     (47.5952, -122.3316)),
    ("https://www.google.com/maps/search/-6.2088,+106.8456?entry=tts", (-6.2088, 106.8456)),
    ("https://www.google.com/maps/dir/?api=1&destination=40.7128,-74.0060", (40.7128, -74.006)),
    ("https://maps.google.com/maps?ll=51.5074,-0.1278&z=12", (51.5074, -0.1278)),
    ("www.google.com/maps/@35.6762,139.6503,11z", (35.6762, 139.6503)),
    # OpenStreetMap
    ("https://www.openstreetmap.org/?mlat=1.1301&mlon=104.0529#map=15/1.1301/104.0529",
     (1.1301, 104.0529)),
    ("https://www.openstreetmap.org/#map=12/-8.6500/115.2167", (-8.65, 115.2167)),
    # Apple, Bing, Waze
    ("https://maps.apple.com/?ll=37.3349,-122.0090&q=Apple%20Park", (37.3349, -122.009)),
    ("https://maps.apple.com/place?coordinate=48.8584,2.2945&name=Eiffel", (48.8584, 2.2945)),
    ("https://www.bing.com/maps?cp=47.6062~-122.3321&lvl=11", (47.6062, -122.3321)),
    ("https://www.waze.com/ul?ll=-6.2,106.81&navigate=yes", (-6.2, 106.81)),
    ("https://example.com/map?lat=10.5&lng=-66.9", (10.5, -66.9)),
    # Not links
    ("geo:-6.2088,106.8456", (-6.2088, 106.8456)),
    ("-6.2088, 106.8456", (-6.2088, 106.8456)),
    ("1.13 104.05", (1.13, 104.05)),
    ("(1.13, 104.05)", (1.13, 104.05)),
    ("6°12'31.7\"S 106°50'44.2\"E", (-6.2088, 106.8456)),
    ("40°42′46″N 74°00′22″W", (40.7128, -74.0061)),
    ("106°50'44.2\"E 6°12'31.7\"S", (-6.2088, 106.8456)),
])
def test_coordinates_are_read(text, expected):
    assert coords(text) == expected


def test_short_links_are_followed_later():
    for url in ("https://maps.app.goo.gl/AbCdEf123", "maps.app.goo.gl/AbCdEf123",
                "https://goo.gl/maps/xyz", "https://g.co/kgs/abc"):
        kind, value = location.parse(url)
        assert kind == "short"
        assert value.startswith("https://")


@pytest.mark.parametrize("text", ["Batam", "Springfield, United States", "São Paulo"])
def test_names_are_searched(text):
    assert location.parse(text) == ("search", text)


@pytest.mark.parametrize("text, kind", [
    ("", "empty"), ("   ", "empty"),
    ("91.0, 20.0", "bad"), ("10, 200", "bad"), ("0, 0", "bad"), ("12345", "bad"),
    ("geo:95,10", "bad"),
    ("https://www.google.com/maps/place/Monumen+Nasional/data=!4m2!3m1!1s0x2e69", "no_coords"),
    ("https://example.com/somewhere", "no_coords"),
])
def test_what_is_not_a_place(text, kind):
    with pytest.raises(LocationError) as caught:
        location.parse(text)
    assert caught.value.kind == kind


def test_only_the_rounded_point_is_kept():
    place = location.Place(-6.208812, 106.845599, "Jakarta")
    assert (place.lat, place.lon) == (-6.21, 106.85)
    assert place.as_dict() == {"lat": -6.21, "lon": 106.85, "name": "Jakarta"}


# --- short links ------------------------------------------------------------------------

def test_a_short_link_is_followed_to_the_place():
    hops = {
        "https://maps.app.goo.gl/AbC": (302, "https://www.google.com/maps/place/X/data=!4m2", b""),
        "https://www.google.com/maps/place/X/data=!4m2":
            (302, "/maps/place/X/@1.1,104.05,17z/data=!3d1.1301!4d104.0529", b""),
    }
    asked = []

    def request(url):
        asked.append(url)
        return hops[url]

    assert location.expand_short_link("https://maps.app.goo.gl/AbC", request) == (1.1301, 104.0529)
    assert len(asked) == 2                     # stopped as soon as the place was known


def test_the_consent_page_is_skipped():
    consent = ("https://consent.google.com/m?continue=https://www.google.com/maps/place/"
               "X/@1.13,104.05,17z&gl=DE")

    def request(url):
        assert "consent" not in url
        return 302, consent, b""

    assert location.expand_short_link("https://maps.app.goo.gl/AbC", request) == (1.13, 104.05)


def test_a_page_without_the_place_in_its_address():
    def request(url):
        return 200, "", b'<html>window.APP=[[null,null,-6.2088,106.8456]]</html>'

    assert location.expand_short_link("https://maps.app.goo.gl/AbC", request) == (-6.2088, 106.8456)


@pytest.mark.parametrize("hop, kind", [
    ((302, "https://evil.example.com/maps/@1,2,3z", b""), "host"),
    ((302, "http://www.google.com/maps/@1.1,2.2,3z", b""), "host"),
    ((404, "", b""), "no_coords"),
    ((500, "", b""), "offline"),
    ((200, "", b"<html>nothing here</html>"), "no_coords"),
])
def test_short_links_that_go_wrong(hop, kind):
    with pytest.raises(LocationError) as caught:
        location.expand_short_link("https://maps.app.goo.gl/AbC", lambda url: hop)
    assert caught.value.kind == kind


def test_redirects_dont_go_on_forever():
    with pytest.raises(LocationError) as caught:
        location.expand_short_link("https://maps.app.goo.gl/AbC",
                                   lambda url: (302, "https://maps.app.goo.gl/AbC", b""))
    assert caught.value.kind == "no_coords"


def test_a_failed_connection_is_offline():
    def request(url):
        raise OSError("no network")

    with pytest.raises(LocationError) as caught:
        location.expand_short_link("https://maps.app.goo.gl/AbC", request)
    assert caught.value.kind == "offline"


# --- names -------------------------------------------------------------------------------------

def test_a_city_is_found_and_the_hint_picks_among_namesakes():
    answer = {"results": [
        {"name": "Springfield", "latitude": 39.80, "longitude": -89.64, "admin1": "Illinois",
         "country": "United States"},
        {"name": "Springfield", "latitude": -43.33, "longitude": 172.18,
         "admin1": "Canterbury", "country": "New Zealand"},
    ]}
    asked = []

    def get_json(url, host):
        asked.append((url, host))
        return answer

    place = location.geocode("Springfield, New Zealand", get_json)
    assert (place.lat, place.lon, place.name) == (-43.33, 172.18, "Springfield")
    assert place.label == "Springfield, Canterbury, New Zealand"
    assert "name=Springfield&" in asked[0][0] and asked[0][1] == location.GEOCODE_HOST
    assert location.geocode("Springfield", get_json).lat == 39.80


def test_no_city_by_that_name():
    with pytest.raises(LocationError) as caught:
        location.geocode("Qwxzy", lambda url, host: {"generationtime_ms": 0.1})
    assert caught.value.kind == "not_found"


def test_the_town_name_comes_from_the_rounded_point():
    asked = []

    def get_json(url, host):
        asked.append(url)
        return {"address": {"city": "Batam", "state": "Riau Islands", "country": "Indonesia"}}

    name, label = location.reverse(1.130123, 104.052987, get_json, sleep=lambda s: None)
    assert (name, label) == ("Batam", "Batam, Riau Islands, Indonesia")
    assert "lat=1.13&lon=104.05&" in asked[0]


def test_no_town_name_is_fine():
    def get_json(url, host):
        raise LocationError("offline")

    assert location.reverse(1.13, 104.05, get_json, sleep=lambda s: None) == ("", "")


def test_resolve_puts_it_together():
    def get_json(url, host):
        return {"address": {"town": "Nongsa"}}

    place = location.resolve("https://www.google.com/maps/@1.1912,104.1105,14z", get_json=get_json)
    assert place.as_dict() == {"lat": 1.19, "lon": 104.11, "name": "Nongsa"}
