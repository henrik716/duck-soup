# Install

duck soup is one Python package (`duck-soup-etl`) containing the engine, the command line
and the web editor. Most people install it as a command with pipx or uv. Docker is the
alternative for servers, scheduled jobs, or machines where you can't install Python.

!!! note "Internet access on first run"
    The first time duck soup starts, DuckDB downloads its `spatial` extension from
    `extensions.duckdb.org`. After that it's cached and works offline.

## Option 1: pipx or uv (recommended)

Both install duck soup as a command in its own isolated environment and put `duck-soup` on
your `PATH`. Neither needs admin rights.

=== "pipx (you have Python 3.11+)"

    ```bash
    pipx install duck-soup-etl
    duck-soup serve
    ```

    [pipx](https://pipx.pypa.io/) installs Python command-line apps. If you don't have it yet:

    ```bash
    python -m pip install --user pipx
    python -m pipx ensurepath
    # open a new terminal so the PATH change takes effect
    ```

    To upgrade: `pipx upgrade duck-soup-etl`.

=== "uv (no Python needed)"

    [uv](https://docs.astral.sh/uv/) downloads a matching Python by itself, so it works on a
    machine with no Python, or with one that's too old. Install uv:

    ```bash
    # Windows (PowerShell)
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    # macOS / Linux
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

    Then, in a new terminal:

    ```bash
    uv tool install duck-soup-etl
    duck-soup serve
    ```

    If `duck-soup` isn't found, run `uv tool update-shell` and open a new terminal.
    To upgrade: `uv tool upgrade duck-soup-etl`.

Open <http://localhost:8000>.

??? question "I used `pip install` and `duck-soup` isn't found"
    That's a PATH issue, not a broken install. Plain `pip` puts the `duck-soup` script into a
    per-user (or virtual-environment) `Scripts`/`bin` folder that isn't always on `PATH`,
    especially on Windows. Either add that folder to `PATH` (pip prints its location as a
    warning) or run the module directly, which always works:

    ```bash
    python -m duck_soup.cli serve
    ```

## Option 2: Docker

```bash
docker run -p 8000:8000 -v "$PWD":/data ghcr.io/henrik716/duck-soup
```

Open <http://localhost:8000>.

The container mounts your current directory as `/data`, which is also its working
directory. Keep your `pipelines/`, `data/` and `output/` folders there and the editor will
read and write them directly. Relative paths in a pipeline (`data/ponds.gpkg`) resolve against
that folder. Files outside it, such as another drive or a network share, are only visible if
you mount them too (`-v D:/gis:/gis`).

Docker suits servers and [scheduled runs](../scheduling.md#docker). On a desktop it's usually
more setup than pipx or uv: Docker Desktop needs admin rights to install, and larger
organisations need a paid Docker subscription to use it.

To upgrade: `docker pull ghcr.io/henrik716/duck-soup`.

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

## Where the editor keeps your files

The web editor works inside a **project folder**:

| | pipx / uv / pip | Docker |
|---|---|---|
| Project folder | `~/duck-soup` (your home directory) | the folder you mounted as `/data` |
| Override with | the `DUCK_SOUP_ROOT` environment variable | — |

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

## Version notes

duck soup pins DuckDB to `>=1.5.4,<1.6`. DuckDB always installs the `spatial` extension build
that matches its own version, so the pin is what keeps spatial behaviour reproducible.
Curved geometry (CircularString, CompoundCurve, CurvePolygon, MultiCurve, MultiSurface), which
DuckDB's spatial extension can't parse, is linearized automatically with `pyogrio`, so no
extras are needed.

## Next steps

Try the [Quickstart](quickstart.md), or get the [tutorial](../tutorials/index.md) sample town
with `duck-soup tutorial` and explore every step and mapping option on real data.
