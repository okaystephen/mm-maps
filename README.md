# Metro Manila Rail Walkshed

How much of Metro Manila is within a 15-minute walk of a rail station?

Compares the straight-line 1.2 km circle around each station with the area you can actually reach along walkable streets in 15 minutes. All figures are computed from OpenStreetMap at run time.

Covers LRT-1, LRT-2, MRT-3 and PNR stations inside the National Capital Region.

## Scripts

| Script | Output | Run time |
|---|---|---|
| `mm_map.py` | Print version: static infographic (PNG) | Longer |
| `mm_map_quick.py` | Print version: static infographic (PNG) | Quicker |
| `mm_map_web.py` | Web version: interactive local map with zoom and station search | First run builds data (slow), later runs start immediately |

All three use the same data sources and method (below).

## How it works

1. Resolves the NCR boundary from OSM (Nominatim), falling back to the union of the 16 city + Pateros boundaries.
2. Pulls rail stations (`railway=station|halt|stop`, `public_transport=station`), drops under-construction/disused entries, and merges duplicate nodes within 120 m.
3. Downloads the pedestrian street network (`network_type="walk"`) and projects it to UTM 51N (EPSG:32651).
4. Runs multi-source Dijkstra from all stations at 4.8 km/h with a 900 s cutoff (15 min = 1.2 km).
5. Marks a street as reached if both endpoints are reachable.
6. Computes:
   - **Reach area:** reached streets buffered by 100 m, clipped to the boundary
   - **Circle area:** union of 1.2 km buffers around stations, clipped to the boundary
   - **Percentage:** reach area / land area
7. Renders the output: a PNG infographic (`mm_map.py`, `mm_map_quick.py`) or a local web map (`mm_map_web.py`).

## Setup

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install osmnx geopandas networkx matplotlib scipy scikit-learn shapely
```

If `geopandas` or `shapely` fails to install, run `python -m pip install --upgrade pip` and retry. Run `source .venv/bin/activate` again in each new terminal session.

The first run of any script downloads the full NCR walk network, which is slow and memory-heavy. osmnx caches it afterwards.

## Print version

```bash
python mm_map.py          # longer run
python mm_map_quick.py    # quicker run
```

Both write a PNG infographic: headline percentage, reached streets in blue, 1.2 km circles dashed red, stations, and a footer with attribution and method.

## Web version

`mm_map_web.py` builds the walkshed data and serves it as a local website:

```bash
python mm_map_web.py              # first run builds data (slow), then serves and opens the browser
python mm_map_web.py --rebuild    # recompute after changing config or station lists
python mm_map_web.py --port 9000 --no-open
```

- Zoom/pan over a light basemap with reachable streets (blue), 1.2 km circles (dashed red) and stations.
- Search stations by name (accent-insensitive, arrow keys + Enter). Selecting one flies to it and highlights that station's own 15-minute street reach and the km of streets reachable.
- Layer toggles for streets, circles and the NCR boundary.
- Output goes to `mm_map_web_site/` (`index.html`, `data.json`) and is cached between runs.
- Needs internet in the browser for Leaflet and the basemap tiles. Basemap: © OpenStreetMap contributors, © CARTO.
- Station and area settings (`EXCLUDE_NAMES`, `MANUAL_STATIONS`, `LAND_KM2`, ...) are at the top of `mm_map_web.py`; the script is self-contained.

## Configuration

Edit the constants at the top of each script. `AUTHOR` and `LABELS` apply to the print versions only.

| Variable | Purpose |
|---|---|
| `AUTHOR` | Name shown in the footer |
| `SPEED_KMH`, `MINUTES` | Walking speed and time budget (radius is derived) |
| `REACH_BUFFER_M` | Buffer around reached streets for the "within reach" area |
| `MERGE_M` | Distance under which station nodes are merged |
| `LAND_KM2` | Land-area denominator; default 619.54 km² (PSA 2020 census release). `None` uses the OSM boundary area |
| `LABELS` | Station names to annotate on the map |
| `EXCLUDE_NAMES` | Stations to drop (e.g. not in service) |
| `MANUAL_STATIONS` | `{"Name": (lat, lon)}` for stations missing from OSM; use verified coordinates only |

## Validate before publishing

- **Station list:** each script prints all stations found and a completeness check for LRT-1, LRT-2 and MRT-3. Expected names are hand-typed; verify against official DOTr/LRTA/MRT-3 lists. Antipolo (LRT-2) is outside NCR and excluded by design.
- **PNR:** OSM may list stops that are not currently in service. Remove them via `EXCLUDE_NAMES`.
- **Land area:** the percentage uses `LAND_KM2` = 619.54 km² (PSA 2020). Other sources cite 636 km²; change it if you prefer that figure. The OSM boundary may include water, so the "inside the circles" area can be slightly overstated near the coast.
- **Reach definition:** the buffered-street area is one reasonable definition; the footer states it. Change `REACH_BUFFER_M` if you need a different one.

## Limitations

- Stations are snapped to the nearest street node; entrances and platform access are not modeled.
- Walk time ignores slopes, crossings, flooding and informal paths absent from OSM.
- Results depend on OSM completeness at the time of the run.

## Data and license

Streets and stations: © OpenStreetMap contributors, [ODbL](https://opendatacommons.org/licenses/odbl/). Keep attribution on any published output.
