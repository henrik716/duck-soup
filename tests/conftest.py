import os
from pathlib import Path

# duck_soup.web.app reads DUCK_SOUP_ROOT at import time to resolve its file-browser
# and pipelines/ root. Pin it to the repo root before any test module imports the
# app, so tests see the same pipelines/ and data/ fixtures regardless of the real
# user's home directory (the app's actual runtime default).
os.environ.setdefault("DUCK_SOUP_ROOT", str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_run_history(tmp_path_factory, monkeypatch):
    """Runs record their history under runs/ (duck_soup/history.py): the CLI in
    $DUCK_SOUP_ROOT, the editor in web.app.RUNS_ROOT. Point both at a temp folder so tests
    don't leave run records in the repo. The app is imported first, while DUCK_SOUP_ROOT
    still names the repo, since it resolves its pipelines/ folder at import time."""
    import duck_soup.web.app as web_app

    root = tmp_path_factory.mktemp("history")
    monkeypatch.setattr(web_app, "RUNS_ROOT", root)
    monkeypatch.setenv("DUCK_SOUP_ROOT", str(root))
    return root
