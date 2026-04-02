"""Bootstrap: path setup and .env loading for all entry points.

Usage in scripts (must be the very first import):
    import sys; sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))
    import src.bootstrap  # noqa: F401
"""

import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load .env file (pydantic-settings reads it too, but scripts that
# import before settings need env vars set early)
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())
