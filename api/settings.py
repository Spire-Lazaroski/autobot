"""Ports and paths. Ports come from .env so they can be changed without code edits."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

HOST = "127.0.0.1"  # local only, never 0.0.0.0
API_PORT = int(os.getenv("API_PORT", "8710"))
WEB_PORT = int(os.getenv("WEB_PORT", "5190"))
CONFIG_PATH = ROOT / "config.yaml"
LOGS = ROOT / "logs"
