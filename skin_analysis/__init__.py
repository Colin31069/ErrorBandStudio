"""Skin analysis viewer package."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path(tempfile.gettempdir()) / "skin_analysis_matplotlib"),
)
