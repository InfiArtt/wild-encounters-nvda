# Wild Encounters for NVDA

> **A fan project.** Wild Encounters is a free, unofficial fan add-on, made by a fan for fun.
> It is not affiliated with, endorsed or sponsored by Nintendo, Game Freak, Creatures or
> The Pokémon Company. Pokémon and Pokémon character names are trademarks of Nintendo.
> No game data, art or music comes with it: names and facts are fetched from
> [PokéAPI](https://pokeapi.co), every sentence is written for the add-on, and the sounds
> were made for it from scratch. It is free, with nothing to buy.

A sweet, ambient Pokémon add-on for the [NVDA](https://www.nvaccess.org) screen reader. It is
not a game. Now and then NVDA tells you which wild Pokémon is around you, based on the real
weather, time of day and moon where you live:

> Psst! It's raining over Batam... a Wingull is gliding just above the rooftops near you.
> Wingull is a Water- and Flying-type Pokémon from generation 3. It's about 0.6 metres tall
> and weighs about 9.5 kilograms. It's mostly white. It likes the sea. It evolves into
> Pelipper. It's a common sight. It's new in your Field Journal.

There are no battles, no catching and nothing to grind. Every Pokémon you meet goes into your
Field Journal. Each kind of Pokémon has its own little sound, and rare ones have their own too.

## Install

1. Download the latest `wildEncounters-<version>.nvda-addon` from
   [Releases](https://github.com/InfiArtt/wild-encounters-nvda/releases/latest).
2. Open the file. NVDA asks whether to install it. Say yes and restart NVDA.
3. Open NVDA's menu, then Preferences, Settings, and choose **Wild Encounters**. Paste where
   you live (a map link, coordinates or a city) into **New home** and press
   **Find this place**.

You need NVDA 2024.1 or newer. The add-on is English only.

## Keys

Press `NVDA+Alt+O`, then:

| Key | What it does |
|---|---|
| `W` | Look around for a wild Pokémon (once every 20 minutes) |
| `J` | Open your Field Journal |
| `L` | Say your last encounter |
| `C` | Say how many Pokémon you've met |
| `S` | Say the status |
| `O` | Open the settings |
| `H` | Say these keys |

You can change all of them in NVDA's Input gestures dialog, under Wild Encounters.

Pokémon don't come by themselves until you turn that on in the settings. When you do, the
weather decides when they come. You get at most one to three a day, never during your quiet
hours, and never while NVDA is reading something to you.

The full guide is [addon/doc/en/readme.html](addon/doc/en/readme.html). It is also in NVDA's
Add-on Store dialog under the add-on's Help.

## Privacy

- Your home is kept rounded to about 1 kilometre. The exact point you paste is never kept or
  sent anywhere.
- The weather comes from [Open-Meteo](https://open-meteo.com). A city name is looked up with
  Open-Meteo's geocoding, the town's name with OpenStreetMap's Nominatim, and a short Google
  Maps link is followed only on Google's own addresses.
- Pokémon names and facts come from PokéAPI. The requests carry nothing about you, and the
  answers are cached on your computer for 30 days.
- Your settings and your Field Journal stay on your computer.

## For developers

Wild Encounters started as an extension for [Hariku](https://github.com/InfiArtt/hariku).

- `addon/` holds everything that goes into the `.nvda-addon` package: the manifest,
  `COPYING.txt`, the guide, and the global plugin in `globalPlugins/wildEncounters/`.
- `tools/make_sounds.py` makes the 13 sounds from scratch with numpy. The sounds are
  committed, so you only need to run it after changing a recipe.
- `tests/` tests the plain modules, plus a smoke test that runs the plugin with real wxPython
  and stand-ins for NVDA's modules.
- `build.py` packs `addon/` into `dist/wildEncounters-<version>.nvda-addon`.

```bash
python -m pytest
```

```bash
python build.py
```

## License

GNU General Public License, version 2 or (at your option) any later version, like NVDA. See
[COPYING.txt](COPYING.txt).
