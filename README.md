# Metro Manila Rail Walkshed

How much of Metro Manila is within a 15-minute walk of a rail station?

Compares the straight-line 1.2 km circle around each station with the area you can actually reach along walkable streets in 15 minutes. All figures are computed from OpenStreetMap at run time.

Covers LRT-1, LRT-2, MRT-3 and PNR stations inside the National Capital Region.

## How it works

1. Resolves the NCR boundary from OSM (Nominatim) and sanity-checks its area.
2. Pulls rail stations (`railway=station|halt|stop`, `public_transport=station`), drops under-construction/disused entries, and merges duplicate nodes within 120 m.
3. Downloads the pedestrian street network (`network_type="walk"`) and projects it to UTM 51N (EPSG:32651).
4. Runs multi-source Dijkstra from all stations at 4.8 km/h with a 900 s cutoff (15 min = 1.2 km).
5. Marks a street as reached if both endpoints are reachable.
6. Computes:
   - **Reach area:** reached streets buffered by 100 m, clipped to the boundary
   - **Circle area:** union of 1.2 km buffers around stations, clipped to the boundary
   - **Percentage:** reach area / land area
7. Renders the infographic to `mm_map.png`.

## Usage

Requires Python 3.10+.
 
```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install osmnx geopandas networkx matplotlib scipy scikit-learn shapely
python mm_map.py
```

The first run downloads the full NCR walk network, which is slow and memory-heavy. osmnx caches it afterwards.

## Configuration

Edit the constants at the top of the script.

| Variable | Purpose |
|---|---|
| `AUTHOR` | Name shown in the footer |
| `SPEED_KMH`, `MINUTES` | Walking speed and time budget (radius is derived) |
| `REACH_BUFFER_M` | Buffer around reached streets for the "within reach" area |
| `MERGE_M` | Distance under which station nodes are merged |
| `LAND_KM2` | Override denominator with the official land area (otherwise OSM boundary area) |
| `LABELS` | Station names to annotate on the map |
| `EXCLUDE_NAMES` | Stations to drop (e.g. not in service) |
| `MANUAL_STATIONS` | `{"Name": (lat, lon)}` for stations missing from OSM; use verified coordinates only |

## Validate before publishing

- **Station list:** the script prints all stations found and a completeness check for LRT-1, LRT-2 and MRT-3. Expected names are hand-typed; verify against official DOTr/LRTA/MRT-3 lists. Antipolo (LRT-2) is outside NCR and excluded by design.
- **PNR:** OSM may list stops that are not currently in service. Remove them via `EXCLUDE_NAMES`.
- **Land area:** the OSM boundary may include water. Set `LAND_KM2` to the official PSA figure.
- **Reach definition:** the buffered-street area is one reasonable definition; the footer states it. Change `REACH_BUFFER_M` if you need a different one.

## Limitations

- Stations are snapped to the nearest street node; entrances and platform access are not modeled.
- Walk time ignores slopes, crossings, flooding and informal paths absent from OSM.
- Results depend on OSM completeness at the time of the run.

## Data and license

Streets and stations: © OpenStreetMap contributors, [ODbL](https://opendatacommons.org/licenses/odbl/). Keep attribution on any published output.
