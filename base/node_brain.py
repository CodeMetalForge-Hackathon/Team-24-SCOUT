"""Load the REAL node logic files (firmware/node/assess.py, co2logic.py) on the laptop.

They are plain Python with no hardware imports, so the simulator and the tests
run exactly the same rules as the board: the demo can't drift from what the
node really does.
"""
import importlib.util
from pathlib import Path

NODE_DIR = Path(__file__).resolve().parent.parent / "firmware" / "node"


def load_node_module(name):
    spec = importlib.util.spec_from_file_location("node_" + name, NODE_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_assess():
    return load_node_module("assess")


def load_co2logic():
    return load_node_module("co2logic")


def load_caller():
    return load_node_module("caller")


def load_beeper():
    return load_node_module("beeper")      # no speaker on a laptop: runs silently
