# Install

duck soup is one Python package (`duck-soup-etl`) containing the engine, the command line
and the web editor. You can run it with Docker (no Python needed) or install it with pipx.

!!! note "Internet access on first run"
    The first time duck soup starts, DuckDB downloads its `spatial` extension from
    `extensions.duckdb.org`. After that it's cached and works offline.

## Option 1: Docker

```bash
docker run -p 8000:8000 -v "$PWD":/data ghcr.io/henrik716/duck-soup
```

Open <http://localhost:8000>.

The container mounts your current directory as `/data`, which is also its working
directory. Keep your `pipelines/`, `data/` and `output/` folders there and the editor will
read and write them directly. Relative paths in a pipeline (`data/ponds.gpkg`) resolve against
that folder.

To upgrade: `docker pull ghcr.io/henrik716/duck-soup`.

## Option 2: pipx (Python 3.11+)

```bash
pipx install duck-soup-etl
duck-soup serve
```

Open <http://localhost:8000>.

[pipx](https://pipx.pypa.io/) installs a Python command-line app into its own isolated
environment and puts its command on your `PATH`. If you don't have it yet:

```bash
python -m pip install --user pipx
python -m pipx ensurepath
# open a new terminal so the PATH change takes effect
```

To upgrade: `pipx upgrade duck-soup-etl`.

??? question "I used `pip install` and `duck-soup` isn't found"
    That's a PATH issue, not a broken install. Plain `pip` puts the `duck-soup` script into a
    per-user (or virtual-environment) `Scripts`/`bin` folder that isn't always on `PATH`,
    especially on Windows. Either add that folder to `PATH` (pip prints its location as a
    warning) or run the module directly, which always works:

    ```bash
    python -m duck_soup.cli serve
    ```

## Where the editor keeps your files

The web editor works inside a **project folder**:

| | Docker | pipx / pip |
|---|---|---|
| Project folder | the folder you mounted as `/data` | `~/duck-soup` (your home directory) |
| Override with | — | the `DUCK_SOUP_ROOT` environment variable |

Saved configs go in `<project folder>/pipelines/`, and the editor's file browser opens at the
project folder.

!!! tip "Start the server from your project folder"
    Relative paths in a pipeline (`data/ponds.gpkg`, `output/ducks.gpkg`) are resolved
    against the folder the server was **started from**. When you run outside Docker, start
    `duck-soup serve` from the same folder you set as `DUCK_SOUP_ROOT`, so relative paths
    and the file browser agree:

    === "macOS / Linux"

        ```bash
        cd ~/pond-survey
        DUCK_SOUP_ROOT=$PWD duck-soup serve
        ```

    === "Windows (PowerShell)"

        ```powershell
        cd C:\pond-survey
        $env:DUCK_SOUP_ROOT = (Get-Location).Path
        duck-soup serve
        ```

    Absolute paths work from anywhere: a source's `uri` can point at any file on disk.

## Option 3: from source

Only needed if you want to change the code or the editor frontend.

```bash
git clone https://github.com/henrik716/duck-soup.git
cd duck-soup
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .                 # add ".[test]" for pytest
uvicorn duck_soup.web.app:app --reload
```

The built editor is committed, so you don't need Node.js unless you change the frontend. See
[Development](../development.md).

## Version notes

duck soup pins DuckDB to `>=1.5.4,<1.6`. DuckDB always installs the `spatial` extension build
that matches its own version, so the pin is what keeps spatial behaviour reproducible.
Curved geometry (CircularString, CompoundCurve, CurvePolygon, MultiCurve, MultiSurface), which
DuckDB's spatial extension can't parse, is linearized automatically with `pyogrio`, so no
extras are needed.
