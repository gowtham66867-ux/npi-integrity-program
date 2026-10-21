"""Run the dependency-free JavaScript Leads behavior harness during release checks."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_government_leads_rendering_and_navigation_race():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the JavaScript view harness")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / "tests" / "leads_render_harness.cjs")],
                            cwd=root, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_saved_directory_rendering_and_navigation_race():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the JavaScript view harness")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([node, str(root / "tests" / "roster_render_harness.cjs")],
                            cwd=root, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
