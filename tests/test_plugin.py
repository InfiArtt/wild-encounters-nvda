"""The plugin itself, with real wx and stand-ins for NVDA's modules: it
loads, looks around every 20 minutes, pages through the layer, opens the Field
Journal, saves the settings and lets someone come by itself."""

import datetime
import logging
import os
import random
import sys
import time
import types

import pytest

wx = pytest.importorskip("wx")

ADDON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "addon")


class Recorder:
    def __init__(self):
        self.spoken, self.sounds, self.beeps, self.clipboard, self.boxes = [], [], [], [], []


rec = Recorder()


def install_nvda_stand_ins(config_path):
    def module(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m

    class GlobalPlugin:
        def __init__(self):
            pass

        def getScript(self, gesture):
            return None

        def terminate(self):
            pass

    def script(description="", gesture=None, **kw):
        def wrap(f):
            f.__doc__ = description
            f.gesture = gesture
            return f
        return wrap

    class BoxSizerHelper:
        def __init__(self, parent, sizer=None, orientation=None):
            self.parent, self.sizer = parent, sizer or wx.BoxSizer(wx.VERTICAL)

        def addItem(self, item):
            self.sizer.Add(item)
            return item

        def addLabeledControl(self, label, cls, **kw):
            self.sizer.Add(wx.StaticText(self.parent, label=label))
            control = cls(self.parent, **kw)
            self.sizer.Add(control)
            return control

    class SettingsPanel(wx.Panel):
        def __init__(self, parent):
            super().__init__(parent)
            self.settingsSizer = wx.BoxSizer(wx.VERTICAL)
            self.makeSettings(self.settingsSizer)
            self.SetSizer(self.settingsSizer)

        def isValid(self):
            return True

    class NVDASettingsDialog:
        categoryClasses = []

    module("api", copyToClip=lambda value, notify=False: rec.clipboard.append(value) or True)
    module("globalPluginHandler", GlobalPlugin=GlobalPlugin)
    module("globalVars", appArgs=types.SimpleNamespace(secure=False, configPath=config_path))
    module("nvwave", playWaveFile=lambda path, asynchronous=True: rec.sounds.append(path))
    module("tones", beep=lambda hz, ms: rec.beeps.append(hz))
    module("ui", message=lambda text: rec.spoken.append(text))
    module("scriptHandler", script=script)
    module("logHandler", log=logging.getLogger("nvda"))
    gui = module("gui", messageBox=lambda message, title, style=0, parent=None:
                 rec.boxes.append(message) or wx.OK)
    gui.__path__ = []
    gui.guiHelper = module("gui.guiHelper", BoxSizerHelper=BoxSizerHelper)
    gui.settingsDialogs = module("gui.settingsDialogs", SettingsPanel=SettingsPanel,
                                 NVDASettingsDialog=NVDASettingsDialog)
    return gui


class MainFrame(wx.Frame):
    def __init__(self):
        super().__init__(None)
        self.sysTrayIcon = types.SimpleNamespace(toolsMenu=wx.Menu(), Bind=lambda *a: None)
        self.settings_opened = []

    def prePopup(self):
        pass

    def postPopup(self):
        pass

    def popupSettingsDialog(self, *args):
        self.settings_opened.append(args)


class FakeAPI:
    def resting(self):
        return 0

    def is_cached(self, kind, key):
        return True

    def type_members(self, kind):
        return {"name": kind, "pokemon": [{"name": "psyduck", "id": 54},
                                          {"name": "wingull", "id": 278}]}

    def species(self, number, offline=False):
        names = {54: "Psyduck", 278: "Wingull"}
        return {"id": number, "name": names[number].lower(), "display": names[number],
                "capture_rate": 190, "generation": 1, "habitat": "waters-edge",
                "pokemon_id": number, "chain_id": None}

    def pokemon(self, number, offline=False):
        return {"id": number, "types": ["water"], "height": 8, "weight": 196}


class FakeWeather:
    def current(self, lat, lon):
        return {"code": 61, "temperature": 27.0, "wind": 8.0, "utc_offset": 7 * 3600,
                "sunrise": None, "sunset": None}


class Gesture:
    def __init__(self, *identifiers):
        self.normalizedIdentifiers = list(identifiers)
        self.isModifier = False


@pytest.fixture(scope="module")
def nvda(tmp_path_factory):
    app = wx.App(False)
    config = str(tmp_path_factory.mktemp("nvda-config"))
    gui = install_nvda_stand_ins(config)
    gui.mainFrame = MainFrame()
    globalPlugins = types.ModuleType("globalPlugins")
    globalPlugins.__path__ = [os.path.join(ADDON, "globalPlugins")]
    sys.modules["globalPlugins"] = globalPlugins
    import globalPlugins.wildEncounters as plugin_module
    plugin = plugin_module.GlobalPlugin()
    plugin.app.api = FakeAPI()
    plugin.app.weather = FakeWeather()
    plugin.app.rng = random.Random(3)
    yield types.SimpleNamespace(module=plugin_module, plugin=plugin, gui=gui, config=config,
                                wx_app=app)
    plugin.terminate()
    gui.mainFrame.Destroy()


def pump(until, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        wx.GetApp().ProcessPendingEvents()
        wx.Yield()
        if until():
            return True
        time.sleep(0.02)
    return False


def test_it_loads_and_registers(nvda):
    panel_class = nvda.module.settingsPanel.WildEncountersPanel
    assert panel_class in nvda.gui.settingsDialogs.NVDASettingsDialog.categoryClasses
    assert nvda.gui.mainFrame.sysTrayIcon.toolsMenu.GetMenuItemCount() == 1
    assert nvda.module.GlobalPlugin.script_layer.gesture == "kb:NVDA+alt+o"


def test_without_home_it_says_so(nvda):
    rec.spoken.clear()
    nvda.plugin.look_around()
    assert "needs to know where home is" in rec.spoken[-1]
    assert rec.sounds[-1].endswith("error.wav")


def test_the_settings_find_and_save_home(nvda, monkeypatch):
    panels = nvda.module.settingsPanel
    found = nvda.module.location.Place(1.130123, 104.052987, "Batam", "Batam, Riau Islands")
    asked = []
    monkeypatch.setattr(panels.location, "resolve", lambda q: asked.append(q) or found)
    panel = panels.WildEncountersPanel(nvda.gui.mainFrame)
    panel.txt_location.SetValue("https://maps.app.goo.gl/AbC")
    assert not panel.isValid()                  # Find wasn't pressed
    panel.on_find()
    panel.on_find()                             # pressed again: only one lookup
    assert pump(lambda: panel._found is not None)
    assert asked == ["https://maps.app.goo.gl/AbC"]
    assert panel.txt_name.GetValue() == "Batam"
    assert "press OK" in panel.txt_home.GetValue()
    assert panel.isValid()
    panel.chk_enabled.SetValue(True)
    panel.cho_month.SetSelection(10)
    panel.spn_day.SetValue(14)
    panel.txt_nickname.SetValue("Rafli")
    panel.onSave()
    settings = nvda.plugin.app.settings
    assert settings["place"] == {"lat": 1.13, "lon": 104.05, "name": "Batam"}
    assert settings["enabled"] and settings["birthday"] == "10-14"
    assert settings["nickname"] == "Rafli"
    assert os.path.exists(os.path.join(nvda.config, "wildEncounters", "settings.json"))
    assert pump(lambda: rec.boxes)              # the welcome, once, when turned on
    panel.Destroy()


def test_looking_around_every_20_minutes(nvda):
    rec.spoken.clear()
    rec.sounds.clear()
    nvda.plugin.look_around()
    assert pump(lambda: not nvda.plugin._busy)
    said = rec.spoken[-1]
    assert ("Psyduck" in said or "Wingull" in said) and "Batam" in said and "Rafli" in said
    assert "is a Water-type Pokémon from generation 1" in said
    assert "0.8 metres tall" in said and said.endswith("It's new in your Field Journal.")
    assert "Nintendo" not in said                       # the disclaimer isn't read out
    assert os.path.basename(rec.sounds[0]) == "search.wav"
    assert all(os.path.exists(path) for path in rec.sounds)
    assert nvda.plugin.app.journal.species_count() == 1

    nvda.plugin.look_around()                   # again, right away: resting
    assert "look around again in 20 minutes" in rec.spoken[-1]
    assert rec.sounds[-1].endswith("rest.wav")
    assert nvda.plugin.app.journal.encounter_count() == 1


def test_the_layer(nvda):
    plugin = nvda.plugin
    assert plugin.getScript(Gesture("kb:s")) is None        # not in the layer: not ours
    plugin.script_layer(Gesture("kb:nvda+alt+o"))
    script = plugin.getScript(Gesture("kb:s"))
    assert plugin.getScript(Gesture("kb:s")) is not None    # looked up twice: still ours
    rec.spoken.clear()
    script(Gesture("kb:s"))
    assert "Home: Batam." in rec.spoken[-1] and "20 minutes" in rec.spoken[-1]
    assert plugin.getScript(Gesture("kb:s")) is None        # the layer is over

    plugin.script_layer(Gesture("kb:nvda+alt+o"))
    rec.beeps.clear()
    plugin.getScript(Gesture("kb:q"))(Gesture("kb:q"))
    assert rec.beeps == [220]                                # not a layer key

    plugin.script_layer(Gesture("kb:nvda+alt+o"))
    plugin.getScript(Gesture("kb:o"))(Gesture("kb:o"))
    assert pump(lambda: nvda.gui.mainFrame.settings_opened)


def test_the_field_journal(nvda):
    plugin = nvda.plugin
    plugin.open_journal()
    window = plugin._window
    assert window is not None and window.lst_entries.GetCount() == 1
    window.on_copy()
    assert rec.clipboard and rec.clipboard[-1].startswith("I met a")
    window.Close()
    assert pump(lambda: plugin._window is None)


def test_one_comes_by_itself_when_the_weather_says_so(nvda, monkeypatch):
    plugin = nvda.plugin
    pacer = plugin.app.pacer
    pacer.state.update(last_any=0.0, last_check=0.0, pending=0.0)
    monkeypatch.setattr(pacer, "rng", types.SimpleNamespace(random=lambda: 0.0))
    monkeypatch.setattr(nvda.module.pacing, "is_quiet", lambda *a: False)
    monkeypatch.setattr(plugin, "ready_to_speak", lambda: False)
    plugin.on_tick()                            # looks at the weather
    assert pump(lambda: not plugin._checking)
    assert pacer.pending_state() == "waiting"
    plugin.on_tick()                            # the user is busy: it waits
    assert not plugin._busy
    monkeypatch.setattr(plugin, "ready_to_speak", lambda: True)
    before = plugin.app.journal.encounter_count()
    plugin.on_tick()
    assert pump(lambda: not plugin._busy)
    assert plugin.app.journal.encounter_count() == before + 1
    assert pacer.state["count"] == 1 and pacer.pending_state() == "none"
    plugin.on_tick()                            # too soon for another
    assert not plugin._checking and not plugin._busy


def test_terminate_cleans_up(nvda):
    plugin = nvda.module.GlobalPlugin()
    classes = nvda.gui.settingsDialogs.NVDASettingsDialog.categoryClasses
    assert classes.count(nvda.module.settingsPanel.WildEncountersPanel) == 2
    plugin.terminate()
    assert classes.count(nvda.module.settingsPanel.WildEncountersPanel) == 1
    nvda.module.settingsPanel.plugin = nvda.plugin
