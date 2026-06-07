from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.cloud


def test_cloud_real_services_are_healthy():
    spec = importlib.util.spec_from_file_location(
        "verificar_cloud", ROOT / "scripts" / "verificar_cloud.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["verificar_cloud"] = mod
    spec.loader.exec_module(mod)

    assert mod.main() == 0
