"""Every step type has what the editor needs to show it: a gallery card, a card icon, label
and hint, a flow-diagram icon and a TypeScript interface. A new step type missed in one of
them still works from YAML, but can't be added in the editor, or shows as a bare type name."""
import re
from pathlib import Path

import pytest

from duck_soup.config import STEP_TYPES

UI = Path(__file__).resolve().parents[1] / "duck_soup" / "web" / "ui" / "src"


def _map_keys(source: str, name: str) -> set[str]:
    """The keys of the object literal `const <name> ... = { ... }` in `source`."""
    m = re.search(rf"const {name}\b[^=]*=\s*\{{(.*?)\n\}}", source, re.S)
    assert m, f"{name} not found"
    return set(re.findall(r"(?:^|[\s,{])([a-z_]+):", m.group(1)))


@pytest.fixture(scope="module")
def sources() -> dict[str, str]:
    return {f: (UI / f).read_text(encoding="utf-8") for f in (
        "step-gallery.ts", "cards/step-card.ts", "lineage.ts", "types.ts",
    )}


@pytest.mark.parametrize("step_type", STEP_TYPES)
def test_step_type_is_complete_in_the_editor(sources, step_type):
    assert f"type: '{step_type}'" in sources["step-gallery.ts"], "no gallery card"
    card = sources["cards/step-card.ts"]
    for name in ("STEP_ICONS", "STEP_LABELS", "STEP_HINTS"):
        assert step_type in _map_keys(card, name), f"missing from {name}"
    assert step_type in _map_keys(sources["lineage.ts"], "STEP_ICON"), "no flow-diagram icon"
    assert f"type: '{step_type}'" in sources["types.ts"], "no interface in types.ts"
