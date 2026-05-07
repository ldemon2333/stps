from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_runtest_setup(item):
    random.seed(1234)
    np.random.seed(1234)
