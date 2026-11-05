"""
Packs addon/ into dist/wildEncounters-<version>.nvda-addon (a zip NVDA
installs when the file is opened). The version comes from the manifest.

    python build.py
"""

import os
import re
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(ROOT, "addon")
SKIP_DIRS = {"__pycache__"}
SKIP_ENDINGS = (".pyc", ".pyo", ".tmp")


def version():
    with open(os.path.join(ADDON, "manifest.ini"), encoding="utf-8") as f:
        return re.search(r"^version\s*=\s*(\S+)", f.read(), re.M).group(1)


def build():
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    target = os.path.join(ROOT, "dist", f"wildEncounters-{version()}.nvda-addon")
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as package:
        for folder, dirs, files in os.walk(ADDON):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in sorted(files):
                if name.endswith(SKIP_ENDINGS):
                    continue
                path = os.path.join(folder, name)
                package.write(path, os.path.relpath(path, ADDON).replace(os.sep, "/"))
    print(f"{target} ({os.path.getsize(target) // 1024} KB)")
    return target


if __name__ == "__main__":
    build()
