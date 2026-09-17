import os
from pathlib import Path

# duck_soup.web.app reads DUCK_SOUP_ROOT at import time to resolve its file-browser
# and pipelines/ root. Pin it to the repo root before any test module imports the
# app, so tests see the same pipelines/ and data/ fixtures regardless of the real
# user's home directory (the app's actual runtime default).
os.environ.setdefault("DUCK_SOUP_ROOT", str(Path(__file__).resolve().parent.parent))
