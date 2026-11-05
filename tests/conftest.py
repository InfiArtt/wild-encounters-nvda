"""The add-on's package without running its __init__ (which needs NVDA), so
the plain modules can be imported as wildEncounters.<module>."""

import os
import sys
import types

PACKAGE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "addon",
                           "globalPlugins", "wildEncounters")

if "wildEncounters" not in sys.modules:
    package = types.ModuleType("wildEncounters")
    package.__path__ = [PACKAGE_DIR]
    sys.modules["wildEncounters"] = package
