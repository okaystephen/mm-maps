"""
Metro Manila: share of land within a 15-minute walk of a rail station.
Stack: osmnx + networkx + geopandas + matplotlib.

pip install osmnx geopandas networkx matplotlib scipy scikit-learn shapely
python manila_rail_walkshed.py

All figures are computed from OpenStreetMap at run time. Nothing is hard-coded
except the walking speed (4.8 km/h -> 15 min = 1.2 km, as in the original).
"""
from datetime import date

import geopandas as gpd
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import networkx as nx
import numpy as np
import osmnx as ox
from matplotlib.lines import Line2D
from shapely.ops import unary_union

# ---------------- config ----------------
AUTHOR = "Steph"
CRS = "EPSG:32651"                 # UTM 51N (metres)
SPEED_KMH = 4.8
MINUTES = 15
RADIUS_M = SPEED_KMH * 1000 / 60 * MINUTES          # 1200 m
CUTOFF_S = MINUTES * 60
REACH_BUFFER_M = 100               # area "within reach" = reached streets buffered by this
MERGE_M = 120                      # merge platforms/interchange nodes closer than this
LAND_KM2 = 619.54                  # PSA 2020 census land area of NCR; None = use OSM boundary area
LABELS = ["Baclaran", "Monumento", "Cubao", "Recto", "Taft Avenue", "North Avenue", "Santolan"]
# Stations to drop if OSM still lists them but they are not in service (verify against DOTr/PNR).
EXCLUDE_NAMES = []
OUT = "manila_rail_walkshed.png"

BG, BLUE, LIGHT, RED, INK = "#EFEBE2", "#2B74B8", "#A9C3DA", "#C0453F", "#161616"
plt.rcParams["font.family"] = ["Plus Jakarta Sans", "Inter", "DejaVu Sans"]

ox.settings.use_cache = True
ox.settings.log_console = True

# ---------------- boundary ----------------
# Accept the first polygon result. OSM admin boundaries can include Manila Bay waters, so the
# area range is wide and the percentage uses LAND_KM2 (land only) as its denominator.
boundary = None
for q in ["National Capital Region, Philippines", "Metro Manila, Philippines",
          "Metropolitan Manila, Philippines"]:
    try:
        g = ox.geocode_to_gdf(q)
        gt = g.geometry.iloc[0].geom_type
        a_km2 = g.to_crs(CRS).area.iloc[0] / 1e6
        print(f"geocode '{q}': {gt}, {a_km2:.1f} km2")
        if gt in ("Polygon", "MultiPolygon") and 400 < a_km2 < 3000:
            boundary = g
            break
    except Exception as e:
        print("geocode failed:", q, e)

if boundary is None:   # fallback: union of the 16 cities + Pateros
    print("Falling back to union of LGU boundaries")
    lgus = ["Manila", "Quezon City", "Caloocan", "Las Pi\u00f1as", "Makati", "Malabon", "Mandaluyong",
            "Marikina", "Muntinlupa", "Navotas", "Para\u00f1aque", "Pasay", "Pasig", "San Juan",
            "Taguig", "Valenzuela", "Pateros"]
    parts = []
    for n in lgus:
        try:
            g = ox.geocode_to_gdf(f"{n}, Metro Manila, Philippines")
            if g.geometry.iloc[0].geom_type in ("Polygon", "MultiPolygon"):
                parts.append(g.geometry.iloc[0])
            else:
                print("  not a polygon:", n)
        except Exception as e:
            print("  failed:", n, e)
    assert len(parts) >= 15, f"Only {len(parts)}/17 LGU polygons resolved"
    boundary = gpd.GeoDataFrame(geometry=[unary_union(parts)], crs="EPSG:4326")

poly = boundary.geometry.iloc[0]
boundary_p = boundary.to_crs(CRS)
bnd = boundary_p.geometry.iloc[0]
bnd_km2 = bnd.area / 1e6
land_km2 = LAND_KM2 or bnd_km2
print(f"OSM boundary area: {bnd_km2:.1f} km2 | land area used: {land_km2:.1f} km2")
if bnd_km2 > 1.05 * land_km2:
    print("WARNING: boundary likely includes water; 'inside the circles' km2 may include bay area.")

# ---------------- stations ----------------
# Every operating rail stop in NCR: LRT-1, LRT-2, MRT-3, PNR. No station-type filtering,
# so nothing is dropped because of how OSM tags it; under-construction/disused are removed.
raw = ox.features_from_polygon(poly, tags={
    "railway": ["station", "halt", "stop"],
    "public_transport": ["station"],
}).reset_index()
for col in ("disused", "abandoned", "construction", "proposed", "razed"):
    if col in raw.columns:
        raw = raw[raw[col].isna()]
for col in ("disused:railway", "abandoned:railway", "construction:railway", "railway:proposed"):
    if col in raw.columns:
        raw = raw[raw[col].isna()]
if "railway" in raw.columns:   # drop railway=construction/proposed/disused rows
    raw = raw[raw["railway"].isna() | raw["railway"].isin(["station", "halt", "stop"])]
if "name" not in raw.columns:
    raw["name"] = None
raw["name"] = raw["name"].fillna(raw.get("name:en")).fillna("(unnamed)")
raw = raw[~raw["name"].isin(EXCLUDE_NAMES)]
raw = raw.to_crs(CRS)
raw["geometry"] = raw.geometry.centroid
raw = raw[raw.within(bnd)]

# merge near-duplicate nodes (platforms, entrances, same-name interchange nodes)
u = unary_union(raw.geometry.buffer(MERGE_M / 2))
blobs = list(u.geoms) if u.geom_type == "MultiPolygon" else [u]
rows = []
for bl in blobs:
    m = raw[raw.within(bl)]
    rows.append({"name": m["name"].iloc[0], "geometry": m.geometry.unary_union.centroid})
stations = gpd.GeoDataFrame(rows, crs=CRS)

# manual additions for anything OSM lacks: {"Station name": (lat, lon)} -- use verified coordinates only
MANUAL_STATIONS = {}
if MANUAL_STATIONS:
    extra = gpd.GeoDataFrame(
        [{"name": n, "geometry": gpd.points_from_xy([lon], [lat])[0]} for n, (lat, lon) in MANUAL_STATIONS.items()],
        crs="EPSG:4326").to_crs(CRS)
    stations = gpd.GeoDataFrame(__import__("pandas").concat([stations, extra], ignore_index=True), crs=CRS)

# completeness check for LRT-1 / LRT-2 / MRT-3 (names normalised; edit if OSM spells differently)
EXPECTED = {
    "LRT-1": ["roosevelt|fernando poe", "balintawak", "monumento", "5th avenue", "r. papa|r papa", "abad santos",
              "blumentritt", "tayuman", "bambang", "doroteo jose", "carriedo", "central terminal", "united nations",
              "pedro gil", "quirino", "vito cruz", "gil puyat", "libertad", "edsa", "baclaran",
              "redemptorist", "mia road", "asia world", "ninoy aquino", "dr. santos|dr santos"],
    "LRT-2": ["recto", "legarda", "pureza", "v. mapa|v mapa", "j. ruiz|j ruiz", "gilmore", "betty go|belmonte",
              "araneta center-cubao|araneta center cubao|cubao", "anonas", "katipunan", "santolan", "marikina"],
    "MRT-3": ["north avenue", "quezon avenue", "kamuning", "cubao", "santolan", "ortigas", "shaw", "boni",
              "guadalupe", "buendia", "ayala", "magallanes", "taft"],
}
import re
names = [n.lower() for n in stations["name"]]
print(f"\n{len(stations)} stations after merging:")
print(stations["name"].sort_values().to_string(index=False))
print("\nCompleteness check (LRT/MRT):")
for line, keys in EXPECTED.items():
    miss = [k for k in keys if not any(re.search(k, n) for n in names)]
    print(f"  {line}: {len(keys)-len(miss)}/{len(keys)} found", "| MISSING: " + ", ".join(miss) if miss else "")
print("MISSING may be outside NCR (e.g. Antipolo), not yet open, or spelled differently in OSM.")
print("Add true gaps to MANUAL_STATIONS; drop non-operating entries via EXCLUDE_NAMES; re-run.\n")

# ---------------- walk network ----------------
G = ox.graph_from_polygon(poly, network_type="walk", simplify=True, retain_all=True)
Gp = ox.project_graph(G, to_crs=CRS)
v = SPEED_KMH * 1000 / 3600
for _, _, _, d in Gp.edges(keys=True, data=True):
    d["time"] = d["length"] / v

snap = ox.distance.nearest_nodes(Gp, stations.geometry.x.values, stations.geometry.y.values)
reached = nx.multi_source_dijkstra_path_length(Gp, set(snap), cutoff=CUTOFF_S, weight="time")

nodes, edges = ox.graph_to_gdfs(Gp)
edges = edges.reset_index()
edges["hit"] = edges["u"].isin(reached) & edges["v"].isin(reached)

# ---------------- areas ----------------
reach_area = unary_union(edges.loc[edges.hit, "geometry"]).buffer(REACH_BUFFER_M).intersection(bnd)
circles = unary_union(stations.geometry.buffer(RADIUS_M)).intersection(bnd)
reach_km2 = reach_area.area / 1e6
circle_km2 = circles.area / 1e6
pct = 100 * reach_km2 / land_km2
print(f"Reach {reach_km2:.1f} km2 / {land_km2:.1f} km2 = {pct:.1f}% | circles {circle_km2:.1f} km2")

# ---------------- plot ----------------
fig = plt.figure(figsize=(8, 10), dpi=200, facecolor=BG)
ax = fig.add_axes([0.04, 0.13, 0.92, 0.62], facecolor=BG)
ax.set_aspect("equal"); ax.axis("off")

edges[~edges.hit].plot(ax=ax, color=LIGHT, linewidth=0.25, alpha=0.7)
boundary_p.boundary.plot(ax=ax, color=LIGHT, linewidth=0.6)
edges[edges.hit].plot(ax=ax, color=BLUE, linewidth=0.8)
gpd.GeoSeries(circles.boundary, crs=CRS).plot(ax=ax, color=RED, linewidth=0.7, linestyle=(0, (3, 2)))
stations.plot(ax=ax, color=RED, markersize=9, zorder=5)

halo = [pe.withStroke(linewidth=2.5, foreground=BG)]
used = set()
for lab in LABELS:
    m = stations[stations["name"].str.contains(lab, case=False, regex=False)]
    if len(m) and lab not in used:
        p = m.geometry.iloc[0]
        ax.annotate(lab, (p.x, p.y), xytext=(6, 6), textcoords="offset points",
                    fontsize=7, fontweight="bold", color=INK, path_effects=halo, zorder=6)
        used.add(lab)

ax.legend(handles=[
    Line2D([0], [0], color=BLUE, lw=2, label=f"street you reach in {MINUTES} min"),
    Line2D([0], [0], color=LIGHT, lw=1, label="street you don't"),
    Line2D([0], [0], color=RED, lw=1, ls="--", label=f"{RADIUS_M/1000:.1f} km as the crow flies"),
    Line2D([0], [0], marker="o", color="none", markerfacecolor=RED, markersize=5, label="rail station"),
], loc="lower left", frameon=False, fontsize=7)

fig.text(0.05, 0.965, "NETWORK · METRO MANILA", fontsize=8, color=INK, family="monospace")
fig.text(0.05, 0.915, f"Only {pct:.0f}% of Metro Manila is a", fontsize=24, fontweight="bold", color=INK)
fig.text(0.05, 0.868, f"{MINUTES} minute walk from a rail station", fontsize=24, fontweight="bold", color=BLUE)
fig.text(0.05, 0.815, f"Dashed circles: {RADIUS_M/1000:.1f} km from each station in a straight line.\n"
                      f"Blue streets: how far you really get on foot in {MINUTES} minutes.",
         fontsize=10, color=INK, linespacing=1.4, va="top")
fig.text(0.07, 0.115, f"{reach_km2:.0f} of {land_km2:.0f} km² of land is within reach",
         fontsize=8, color=BLUE, fontweight="bold")
fig.text(0.07, 0.100, f"{circle_km2:.0f} km² inside the circles", fontsize=8, color=RED, fontweight="bold")
fig.add_artist(plt.Line2D([0.05, 0.95], [0.075, 0.075], color="#999", lw=0.6))
fig.text(0.05, 0.055, AUTHOR, fontsize=9, fontweight="bold", color=INK)
fig.text(0.05, 0.038,
         f"Streets and stations: © OpenStreetMap contributors (ODbL), {date.today():%b %Y} · "
         f"Walk {SPEED_KMH} km/h, {MINUTES} min = {RADIUS_M/1000:.1f} km · reach = {REACH_BUFFER_M} m around reachable streets",
         fontsize=5.5, color="#444")
fig.text(0.05, 0.022, "Made with Python · osmnx · networkx · geopandas · matplotlib", fontsize=5.5, color="#666")

fig.savefig(OUT, facecolor=BG)
print("saved", OUT)
