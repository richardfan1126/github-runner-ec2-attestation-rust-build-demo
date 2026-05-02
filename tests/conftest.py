"""Pytest configuration — ensure the project root is importable."""

import sys
from pathlib import Path

# Add the project root to sys.path so that `scripts.parse_markers` etc.
# can be imported without installing the package.
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)
