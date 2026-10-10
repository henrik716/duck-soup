"""Retake the editor screenshots in docs/assets/screenshots/ (a light and a dark one of each).

    pip install -e ".[screenshots]"
    python scripts/build_screenshots.py                       # every shot
    python scripts/build_screenshots.py overview steps-tab    # just these
    python scripts/build_screenshots.py --browser chrome      # installed Chrome instead of Edge

Shot names: lineage overview map-preview map-popup sources-tab mapping-tab output-layers
step-gallery codelist-drawer expr-drawer steps-tab resized-panels flow-editor sql-tab yaml-tab
step-preview run-logs run-history problems-tab.

It sets up a throwaway project from the packaged tutorial data, rewrites its config into the
two-pipeline Pondsworth showcase the screenshots show (`showcase()`), starts the editor from
*inside* that folder (relative source paths resolve against the server's cwd, so started
anywhere else every source shows "error"), and drives it with Playwright.

It drives an installed browser (Edge by default), not Playwright's bundled Chromium: that one
renders MapLibre with software WebGL so slowly that clicks take a minute or more and the
capture times out. So no `playwright install` is needed.

Retake the shots whenever the UI they show changes. The top bar is in editor-overview,
resized-panels and flow-editor.
"""
from __future__ import annotations

import argparse
import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import yaml
from PIL import Image
from playwright.sync_api import sync_playwright

from duck_soup import tutorial

OUT = Path(__file__).resolve().parent.parent / "docs" / "assets" / "screenshots"
CRS = "EPSG:32631"


# --- the project ---------------------------------------------------------------------------

def showcase(project: Path) -> None:
    """Replace the tutorial config with the showcase: duck_places (joins, a merge, a snapshot
    branch with a filter and its rejects layer, a codelist) and dry_park_land (erase, overlay)."""
    path = project / "pipelines" / "pondsworth.yaml"
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    src = {}
    for pl in d["pipelines"]:
        for s in pl["sources"]:
            src.setdefault(s["id"], s)
    duck_places = {
        "name": "duck_places", "working_crs": CRS,
        "sources": [src[i] for i in ("places", "districts", "categories", "stops", "new_places")],
        "base": "places",
        "steps": [
            {"type": "spatial_join", "source": "districts", "fields": {"district": "district_name"}},
            {"type": "attribute_join", "source": "categories", "left": "category", "right": "code",
             "fields": {"category_label": "label"}},
            {"type": "nearest_neighbor", "source": "stops", "max_distance": 300,
             "distance_field": "waddle_m", "fields": {"nearest_stop": "stop_name"}},
            {"type": "merge", "source": "new_places"},
            {"type": "snapshot", "id": "crumb_hotspots"},
            {"type": "filter", "branch": "crumb_hotspots", "where": "bread_crumbs >= 50000",
             "rejects": "quiet_spots"},
            {"type": "buffer", "branch": "crumb_hotspots", "distance": 250},
        ],
        "mapping": [
            {"to": "name", "from": "name"}, {"to": "category", "from": "category_label"},
            {"to": "district", "from": "district"}, {"to": "nearest_stop", "from": "nearest_stop"},
            {"to": "waddle_m", "expr": "round(waddle_m)"}, {"to": "crumbs", "from": "bread_crumbs"},
            {"to": "kind", "codelist": {"source": "category", "cases": [
                {"match": "MUS", "value": "Museum"}, {"match": "LIB", "value": "Library"},
                {"like": "CA%", "value": "Café"}, {"regex": "^(SCH|UNI)$", "value": "Education"},
                {"is_blank": True, "value": "Not categorised"}], "default": "Other"}},
            {"to": "geohash", "func": "geohash"},
        ],
        "layers": [
            {"layer": "duck_places", "crs": CRS},
            {"layer": "places_in_town", "crs": CRS, "filter": "district IS NOT NULL"},
            {"layer": "places_off_the_map", "crs": CRS, "filter": "district IS NULL"},
        ],
    }
    dry_park_land = {
        "name": "dry_park_land", "working_crs": CRS,
        "sources": [src[i] for i in ("parks", "river", "districts")],
        "base": "parks",
        "steps": [
            {"type": "erase", "source": "river"},
            {"type": "intersect_overlay", "source": "districts", "fields": {"district": "district_name"}},
        ],
        "mapping": [],
        "layers": [{"layer": "dry_park_land", "crs": CRS}],
    }
    cfg = {"name": "pondsworth", "output": "output/pondsworth.gpkg",
           "pipelines": [duck_places, dry_park_land]}
    path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(project: Path, port: int) -> subprocess.Popen:
    """The editor, started from inside the project (see the module docstring)."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "duck_soup.web.app:app", "--port", str(port)],
        cwd=project, env={**os.environ, "DUCK_SOUP_ROOT": str(project)},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(120):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
            return proc
        except OSError:
            if proc.poll() is not None:
                raise RuntimeError("the editor server exited on startup")
            time.sleep(0.5)
    proc.kill()
    raise RuntimeError("the editor server didn't come up")


# --- page helpers --------------------------------------------------------------------------

def ready(page):
    page.wait_for_selector("#status.ok", timeout=60000)
    page.wait_for_timeout(4000)  # sources inspected, map tiles in
    settled(page)


def settled(page):
    """No preview running and the table no longer faded as stale."""
    page.wait_for_function("""() => !document.querySelector('#preview-table-container.is-stale')
      && !document.querySelector('#preview-progress.active')""", timeout=60000)
    page.wait_for_timeout(800)


def open_page(url, b, theme, width=1200, height=750, scale=2, builder_pct=None,
              map_collapsed=False, lineage_collapsed=False):
    page = b.new_page(viewport={"width": width, "height": height}, device_scale_factor=scale)
    layout = (f"localStorage.setItem('duckSoup.layout', JSON.stringify({{cols: {builder_pct}, map: 35, "
              f"mapCollapsed: {'true' if map_collapsed else 'false'}}}))"
              if builder_pct else "localStorage.removeItem('duckSoup.layout')")
    page.add_init_script(f"""localStorage.setItem('ducksoup.lastConfig', 'pondsworth');
      localStorage.setItem('ds-theme', '{theme}');
      localStorage.setItem('ducksoup.lineageCollapsed', '{'1' if lineage_collapsed else '0'}');
      {layout}""")
    page.goto(url)
    ready(page)
    return page


def count_sample(page):
    page.click("#countSampleBtn")
    page.wait_for_function("document.querySelector('#lineageCountStatus').textContent.startsWith('rows:')",
                           timeout=60000)
    page.wait_for_timeout(400)


def pl_tab(page, sec, open_first=False):
    """Open the first pipeline card on section `sec`, optionally expanding its first card."""
    page.evaluate("""() => { const c = document.querySelector('.pipeline-card');
      c.classList.remove('collapsed'); c._syncCollapse?.() }""")
    page.locator(f".pipeline-card >> nth=0 >> .pl-tab-btn[data-sec={sec}]").click()
    page.wait_for_timeout(500)
    if open_first:
        page.evaluate("""sec => { const panel = document.querySelector(`.pipeline-card .pl-panel[data-sec=${sec}]`);
          const c = [...panel.querySelectorAll('.collapsed')].find(e => e._syncCollapse);
          if (c) { c.classList.remove('collapsed'); c._syncCollapse() } }""", sec)
        page.wait_for_timeout(500)


def card_from_tabs(page, path):
    """The pipeline card, cropped from its section tabs to the bottom of the window."""
    tabs = page.locator(".pipeline-card >> nth=0 >> .pl-tabs")
    top = page.locator("main.builder").bounding_box()["y"]
    page.evaluate("y => document.querySelector('main.builder').scrollBy(0, y)", tabs.bounding_box()["y"] - top - 2)
    page.mouse.move(5, page.viewport_size["height"] - 10)
    page.wait_for_timeout(400)
    card = page.locator(".pipeline-card").first.bounding_box()
    t = tabs.bounding_box()
    page.screenshot(path=path, clip={"x": card["x"], "y": t["y"], "width": card["width"],
                                     "height": page.viewport_size["height"] - t["y"]})


def collapse_all(page):
    page.evaluate("""() => { for (const c of document.querySelectorAll('.pipeline-card .pl-panel .card')) {
      if (c._syncCollapse) { c.classList.add('collapsed'); c._syncCollapse() } }
      document.querySelector('main.builder').scrollTo(0, 0) }""")


def click_feature(page):
    """Click the first preview point on the map (found by its fill colour)."""
    m = page.locator("#map")
    box = m.bounding_box()
    img = Image.open(io.BytesIO(m.screenshot())).convert("RGB")
    sx = img.width / box["width"]
    w, h = img.size
    for y in range(int(h * 0.2), h, 2):
        for x in range(int(w * 0.25), int(w * 0.75), 2):
            r, g, b = img.getpixel((x, y))
            if r > 140 and b > 160 and g < 110:
                page.mouse.click(box["x"] + x / sx, box["y"] + y / sx + 3)
                return
    raise RuntimeError("no feature found on map")


def aside(page, name):
    page.locator("aside.side").screenshot(path=str(OUT / name))


# --- the shots -----------------------------------------------------------------------------

def capture(url: str, channel: str, only: set[str]) -> None:
    def want(name):
        return not only or name in only

    with sync_playwright() as p:
        b = p.chromium.launch(channel=channel, headless=True)
        for theme in ("light", "dark"):
            page = open_page(url, b, theme)
            count_sample(page)
            page.mouse.move(5, 740)  # no hover effects

            if want("lineage"):
                # Wide enough for the whole flow, rejects branch included.
                wide = open_page(url, b, theme, 2400, 1300, 1, builder_pct=75)
                count_sample(wide)
                wide.mouse.move(5, 1290)
                wide.wait_for_timeout(400)
                wide.locator("#lineage-diagram-container").screenshot(path=str(OUT / f"lineage-{theme}.png"))
                wide.close()
            if want("overview"):
                settled(page)
                page.screenshot(path=str(OUT / f"editor-overview-{theme}.png"))
            if want("map-preview"):
                aside(page, f"map-preview-{theme}.png")

            tabs = [t for t in (("sources", "sources-tab", True), ("mapping", "mapping-tab", False),
                                ("output", "output-layers", True)) if want(t[1])]
            if tabs or want("map-popup") or want("step-gallery"):
                # Wider than the main framing at a smaller scale, so the mapping grid isn't
                # squeezed and these shots aren't zoomed in.
                cp = open_page(url, b, theme, 1600, 1000, 1.5, lineage_collapsed=True)
                cp.mouse.move(5, 990)
                if want("map-popup"):
                    click_feature(cp)
                    cp.wait_for_timeout(800)
                    c = cp.locator("#map-container").bounding_box()
                    cp.screenshot(path=str(OUT / f"map-popup-{theme}.png"), clip=c)
                    if cp.locator(".maplibregl-popup-close-button").count():
                        cp.locator(".maplibregl-popup-close-button").click()
                    cp.mouse.move(5, 990)
                if want("step-gallery"):
                    pl_tab(cp, "steps")
                    cp.locator(".pipeline-card >> nth=0 >> .pl-add-step").click()
                    cp.mouse.move(0, 0)
                    cp.wait_for_timeout(600)
                    cp.locator("#stepGalleryModal .modal-card").screenshot(path=str(OUT / f"step-gallery-{theme}.png"))
                    cp.keyboard.press("Escape")
                    cp.wait_for_timeout(300)
                    collapse_all(cp)
                for sec, name, first in tabs:
                    pl_tab(cp, sec, open_first=first)
                    card_from_tabs(cp, str(OUT / f"{name}-{theme}.png"))
                    collapse_all(cp)
                cp.close()
            if want("codelist-drawer") or want("expr-drawer"):
                dr = open_page(url, b, theme, 1360, 860, 1.5)
                pl_tab(dr, "mapping")
                if want("codelist-drawer"):
                    dr.locator(".pipeline-card >> nth=0 >> .codelist-btn").first.click()
                    dr.wait_for_timeout(800)
                    dr.screenshot(path=str(OUT / f"codelist-drawer-{theme}.png"))
                    dr.keyboard.press("Escape")
                    dr.wait_for_timeout(400)
                if want("expr-drawer"):
                    dr.locator(".pipeline-card >> nth=0 >> .expr-edit-btn").first.click()
                    dr.wait_for_timeout(1500)
                    dr.screenshot(path=str(OUT / f"expr-drawer-{theme}.png"))
                dr.close()
            if page.locator(".pipeline-card >> nth=0 >> .pl-tab-btn[data-sec=sources]").is_visible():
                pl_tab(page, "sources")

            if want("steps-tab"):
                # The steps tab with the first step (the spatial join) open, scrolled so the
                # card's section tabs are at the top of the shot.
                page.evaluate("""() => {
                  const card = document.querySelector('.pipeline-card')
                  card.classList.remove('collapsed'); card._syncCollapse?.()
                  card._activateSection('steps')
                  const step = card.querySelector('.pl-steps > .card')
                  step.classList.remove('collapsed'); step._syncCollapse?.()
                  card.querySelector('.pl-tabs').scrollIntoView({ block: 'start' })
                  document.querySelector('main.builder').scrollBy(0, -2)
                }""")
                page.mouse.move(5, 740)
                page.wait_for_timeout(500)
                box = page.locator(".pipeline-card").first.bounding_box()
                tb = page.locator(".pipeline-card .pl-tabs").first.bounding_box()
                page.screenshot(path=str(OUT / f"steps-tab-{theme}.png"),
                                clip={"x": box["x"], "y": tb["y"], "width": box["width"],
                                      "height": min(688, 750 - tb["y"])})
                page.evaluate("""() => {
                  const card = document.querySelector('.pipeline-card')
                  const step = card.querySelector('.pl-steps > .card')
                  step.classList.add('collapsed'); step._syncCollapse?.()
                  card._activateSection('sources')
                  document.querySelector('main.builder').scrollTo(0, 0)
                }""")

            if want("resized-panels"):
                # A wider preview panel with the map collapsed, so the table gets the full height.
                rp = open_page(url, b, theme, builder_pct=44, map_collapsed=True, lineage_collapsed=True)
                rp.mouse.move(5, 740)
                settled(rp)
                rp.screenshot(path=str(OUT / f"resized-panels-{theme}.png"))
                rp.close()

            if want("flow-editor"):
                # Wider, so the panel sits beside the diagram as it does on a desktop screen.
                wide = open_page(url, b, theme, 2400, 1300, 1)
                count_sample(wide)
                wide.click(".lineage-row >> nth=0 >> .lineage-step-node >> nth=0")
                wide.wait_for_selector(".flow-editor:not([hidden])")
                wide.wait_for_timeout(500)
                wide.mouse.move(5, 1290)
                wide.wait_for_timeout(400)
                wide.screenshot(path=str(OUT / f"flow-editor-{theme}.png"))
                wide.close()

            if want("sql-tab"):
                page.click(".lineage-row >> nth=0 >> .lineage-step-node >> nth=0")
                page.wait_for_selector(".flow-editor:not([hidden])")
                page.click(".card.flow-popped [data-step-sql]")
                page.wait_for_timeout(1200)
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                aside(page, f"sql-tab-{theme}.png")

            if want("yaml-tab"):
                page.click("#tab-btn-yaml")
                page.wait_for_timeout(300)
                aside(page, f"yaml-tab-{theme}.png")

            if want("step-preview"):
                page.click("#tab-btn-table")
                page.evaluate("document.querySelector('.pipeline-card .pl-steps > .card .data-step-preview').click()")
                page.wait_for_selector("#preview-banner")
                page.wait_for_timeout(2500)
                aside(page, f"step-preview-{theme}.png")
                page.click("#reset-preview-btn")
                page.wait_for_timeout(1500)

            if want("run-logs") or want("run-history"):
                page.click("#runBtn")
                page.wait_for_function("document.querySelector('#status').textContent === 'run complete'",
                                       timeout=120000)
                # "run complete" wraps the top bar at this width and pushes the preview panel
                # down; the status pill isn't in these shots, so hide it.
                page.add_style_tag(content="#status { display: none !important }")
                page.wait_for_timeout(800)
                if want("run-logs"):
                    page.evaluate("document.querySelector('#log').scrollTop = 1e6")
                    aside(page, f"run-logs-{theme}.png")
                if want("run-history"):
                    page.click("#tab-btn-runs")
                    page.wait_for_selector(".run-entry")
                    page.click(".run-entry summary >> nth=0")
                    page.wait_for_selector("[data-run-log][data-loaded]")
                    page.wait_for_timeout(500)
                    aside(page, f"run-history-{theme}.png")

            if want("problems-tab"):
                # Point the first step at a source that doesn't exist.
                page.evaluate("""() => {
                  const i = document.querySelector('.pipeline-card .pl-steps > .card [data-k="source"]')
                  i.value = 'district'; i.dispatchEvent(new Event('input', { bubbles: true })) }""")
                page.wait_for_selector("#status.bad", state="attached", timeout=20000)
                page.add_style_tag(content="#status { display: none !important }")  # as above
                page.click("#tab-btn-problems")
                page.wait_for_timeout(500)
                aside(page, f"problems-tab-{theme}.png")
            page.close()
        b.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Retake the docs screenshots.")
    ap.add_argument("shots", nargs="*", help="shot names (default: all)")
    ap.add_argument("--browser", default="msedge", choices=["msedge", "chrome"],
                    help="installed browser to drive (default: msedge)")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="duck_soup_shots_", ignore_cleanup_errors=True) as tmp:
        project = Path(tmp)
        tutorial.install(project)
        showcase(project)
        port = free_port()
        server = start_server(project, port)
        try:
            capture(f"http://127.0.0.1:{port}/", args.browser, set(args.shots))
        finally:
            server.kill()
            server.wait()
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
