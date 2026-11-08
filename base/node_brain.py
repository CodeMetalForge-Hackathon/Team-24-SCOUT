"""Load the REAL node decision logic (firmware/node/assess.py) on the laptop.

assess.py is plain Python with no hardware imports, so the simulator and the
tests run exactly the same rules as the board: the demo can't drift from
what the node really does.
"""
import importlib.util
from pathlib import Path

ASSESS_PATH = Path(__file__).resolve().parent.parent / "firmware" / "node" / "assess.py"


def load_assess():
    spec = importlib.util.spec_from_file_location("node_assess", ASSESS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
