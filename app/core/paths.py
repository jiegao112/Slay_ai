from __future__ import annotations

from pathlib import Path


# app/core/paths.py -> app/core -> app -> project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOTENV_PATH = PROJECT_ROOT / ".env"
CONFIG_DIR = PROJECT_ROOT / "config"
REGIONS_PATH = CONFIG_DIR / "regions.json"
FAISS_DIR = PROJECT_ROOT / "indexes" / "faiss"

