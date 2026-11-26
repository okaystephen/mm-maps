#!/usr/bin/env python3
"""
Metro Manila rail walkshed - interactive local web map.

Builds the 15-minute walkshed data from OpenStreetMap (LRT-1, LRT-2, MRT-3, PNR), writes it to
./mm_map_web_site/, then serves a Leaflet map at http://127.0.0.1:8000 with zoom/pan and station search.

    python mm_map_web.py              # build (first run) + serve + open browser
    python mm_map_web.py --rebuild    # recompute data even if cached
    python mm_map_web.py --port 9000 --no-open

Needs internet in the browser for Leaflet and the basemap tiles.
pip install osmnx geopandas networkx scipy scikit-learn shapely numpy pandas
"""
import argparse
import functools
import http.server
import json
import re
import socketserver
import threading
import time
import webbrowser
from datetime import date
from pathlib import Path

import geopandas as gpd
import networkx as nx
import numpy as np
import osmnx as ox
import pandas as pd
import shapely
from shapely.ops import unary_union

# ---------------- config ----------------
CRS = "EPSG:32651"                 # UTM 51N (metres)
SPEED_KMH = 4.8
MINUTES = 15
RADIUS_M = SPEED_KMH * 1000 / 60 * MINUTES          # 1200 m
CUTOFF_S = MINUTES * 60
REACH_BUFFER_M = 100               # area "within reach" = reached streets buffered by this
MERGE_M = 120                      # merge platforms/interchange nodes closer than this
LAND_KM2 = 619.54                  # PSA 2020 census land area of NCR; None = use OSM boundary area
EXCLUDE_NAMES = []                 # stations to drop (e.g. not in service)
MANUAL_STATIONS = {}               # {"Name": (lat, lon)} for stations missing from OSM (verified coords only)

SITE = Path(__file__).resolve().parent / "mm_map_web_site"

ox.settings.use_cache = True


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ---------------- data build ----------------
def get_boundary():
    for q in ["National Capital Region, Philippines", "Metro Manila, Philippines",
              "Metropolitan Manila, Philippines"]:
        try:
            g = ox.geocode_to_gdf(q)
            gt = g.geometry.iloc[0].geom_type
            a = g.to_crs(CRS).area.iloc[0] / 1e6
            log(f"geocode '{q}': {gt}, {a:.1f} km2")
            if gt in ("Polygon", "MultiPolygon") and 400 < a < 3000:
                return g
        except Exception as e:
            log(f"geocode failed: {q} {e}")
    log("Falling back to union of LGU boundaries")
    lgus = ["Manila", "Quezon City", "Caloocan", "Las Pi\u00f1as", "Makati", "Malabon", "Mandaluyong",
            "Marikina", "Muntinlupa", "Navotas", "Para\u00f1aque", "Pasay", "Pasig", "San Juan",
            "Taguig", "Valenzuela", "Pateros"]
    parts = []
    for n in lgus:
        try:
            g = ox.geocode_to_gdf(f"{n}, Metro Manila, Philippines")
            if g.geometry.iloc[0].geom_type in ("Polygon", "MultiPolygon"):
                parts.append(g.geometry.iloc[0])
        except Exception as e:
            log(f"  failed: {n} {e}")
    assert len(parts) >= 15, f"Only {len(parts)}/17 LGU polygons resolved"
    return gpd.GeoDataFrame(geometry=[unary_union(parts)], crs="EPSG:4326")


def get_stations(poly, bnd):
    raw = ox.features_from_polygon(poly, tags={
        "railway": ["station", "halt", "stop"],
        "public_transport": ["station"],
    }).reset_index()
    for col in ("disused", "abandoned", "construction", "proposed", "razed",
                "disused:railway", "abandoned:railway", "construction:railway", "railway:proposed"):
        if col in raw.columns:
            raw = raw[raw[col].isna()]
    if "railway" in raw.columns:
        raw = raw[raw["railway"].isna() | raw["railway"].isin(["station", "halt", "stop"])]
    if "name" not in raw.columns:
        raw["name"] = None
    raw["name"] = raw["name"].fillna(raw.get("name:en")).fillna("(unnamed)")
    raw = raw[~raw["name"].isin(EXCLUDE_NAMES)].to_crs(CRS)
    raw["geometry"] = raw.geometry.centroid
    raw = raw[raw.within(bnd)]

    u = unary_union(raw.geometry.buffer(MERGE_M / 2))
    blobs = list(u.geoms) if u.geom_type == "MultiPolygon" else [u]
    rows = []
    for bl in blobs:
        m = raw[raw.within(bl)]
        rows.append({"name": m["name"].iloc[0], "geometry": shapely.union_all(m.geometry.values).centroid})
    st = gpd.GeoDataFrame(rows, crs=CRS)

    if MANUAL_STATIONS:
        extra = gpd.GeoDataFrame(
            [{"name": n, "geometry": gpd.points_from_xy([lon], [lat])[0]}
             for n, (lat, lon) in MANUAL_STATIONS.items()], crs="EPSG:4326").to_crs(CRS)
        st = gpd.GeoDataFrame(pd.concat([st, extra], ignore_index=True), crs=CRS)

    expected = {
        "LRT-1": ["roosevelt|fernando poe", "balintawak", "monumento", "5th avenue", "r. papa|r papa",
                  "abad santos", "blumentritt", "tayuman", "bambang", "doroteo jose", "carriedo",
                  "central terminal", "united nations", "pedro gil", "quirino", "vito cruz", "gil puyat",
                  "libertad", "edsa", "baclaran", "redemptorist", "mia road", "asia world",
                  "ninoy aquino", "dr. santos|dr santos"],
        "LRT-2": ["recto", "legarda", "pureza", "v. mapa|v mapa", "j. ruiz|j ruiz", "gilmore",
                  "betty go|belmonte", "araneta center-cubao|araneta center cubao|cubao", "anonas",
                  "katipunan", "santolan", "marikina"],
        "MRT-3": ["north avenue", "quezon avenue", "kamuning", "cubao", "santolan", "ortigas", "shaw",
                  "boni", "guadalupe", "buendia", "ayala", "magallanes", "taft"],
    }
    names = [n.lower() for n in st["name"]]
    print(f"\n{len(st)} stations after merging:")
    print(st["name"].sort_values().to_string(index=False))
    print("\nCompleteness check (LRT/MRT):")
    for line, keys in expected.items():
        miss = [k for k in keys if not any(re.search(k, n) for n in names)]
        print(f"  {line}: {len(keys) - len(miss)}/{len(keys)} found",
              "| MISSING: " + ", ".join(miss) if miss else "")
    print("MISSING may be outside NCR, not yet open, or spelled differently in OSM.")
    print("Fix via MANUAL_STATIONS / EXCLUDE_NAMES and --rebuild.\n")
    return st


def build_data():
    boundary = get_boundary()
    poly = boundary.geometry.iloc[0]
    bnd = boundary.to_crs(CRS).geometry.iloc[0]
    bnd_km2 = bnd.area / 1e6
    land_km2 = LAND_KM2 or bnd_km2
    log(f"OSM boundary {bnd_km2:.1f} km2 | land area used {land_km2:.1f} km2")

    stations = get_stations(poly, bnd)

    log("downloading/loading walk network (slow on first run) ...")
    G = ox.graph_from_polygon(poly, network_type="walk", simplify=True, retain_all=True)
    Gp = ox.project_graph(G, to_crs=CRS)
    v = SPEED_KMH * 1000 / 3600
    for _, _, _, d in Gp.edges(keys=True, data=True):
        d["time"] = d["length"] / v

    edges = ox.graph_to_gdfs(Gp, nodes=False).reset_index()[["u", "v", "length", "geometry"]]
    edges["a"] = np.minimum(edges.u, edges.v)
    edges["b"] = np.maximum(edges.u, edges.v)
    edges["l"] = edges.length.round(1)
    edges = edges.drop_duplicates(["a", "b", "l"]).reset_index(drop=True)   # walk graphs are bidirectional
    eu, ev, elen = edges.u.values, edges.v.values, edges.length.values

    log("per-station walksheds ...")
    snap = ox.distance.nearest_nodes(Gp, stations.geometry.x.values, stations.geometry.y.values)
    masks, union = [], np.zeros(len(edges), dtype=bool)
    for node in snap:
        d = nx.single_source_dijkstra_path_length(Gp, node, cutoff=CUTOFF_S, weight="time")
        arr = np.fromiter(d.keys(), dtype=np.int64)
        m = np.isin(eu, arr) & np.isin(ev, arr)
        masks.append(m)
        union |= m
    hit_ids = np.flatnonzero(union)
    pos = np.full(len(edges), -1, dtype=np.int64)
    pos[hit_ids] = np.arange(len(hit_ids))
    log(f"{len(hit_ids)} reachable street segments")

    # areas
    log("computing areas ...")
    bnd_s = bnd.simplify(25)
    buf = shapely.buffer(edges.geometry.values[hit_ids], REACH_BUFFER_M, quad_segs=2)
    c = shapely.centroid(buf)
    tile = (np.floor(shapely.get_x(c) / 3000).astype(np.int64) * 100000
            + np.floor(shapely.get_y(c) / 3000).astype(np.int64))
    tiles = [shapely.union_all(buf[tile == t]) for t in np.unique(tile)]
    reach_km2 = shapely.union_all(tiles).intersection(bnd_s).area / 1e6
    circle_km2 = unary_union(stations.geometry.buffer(RADIUS_M)).intersection(bnd_s).area / 1e6
    pct = 100 * reach_km2 / land_km2
    log(f"Reach {reach_km2:.1f} km2 / {land_km2:.1f} km2 = {pct:.1f}% | circles {circle_km2:.1f} km2")

    # geometry out (WGS84, [lat, lon])
    simp = shapely.simplify(edges.geometry.values[hit_ids], 3)
    ll = gpd.GeoSeries(simp, crs=CRS).to_crs(4326)
    lines = [[[round(y, 5), round(x, 5)] for x, y in g.coords] for g in ll]

    st_ll = stations.to_crs(4326)
    st_out = []
    for i, row in enumerate(st_ll.itertuples()):
        ids = np.flatnonzero(masks[i])
        st_out.append({
            "name": row.name,
            "lat": round(row.geometry.y, 6),
            "lon": round(row.geometry.x, 6),
            "street_km": round(float(elen[ids].sum()) / 1000, 1),
            "edges": pos[ids].tolist(),
        })
    st_out.sort(key=lambda s: s["name"].lower())

    b_ll = gpd.GeoSeries([bnd.simplify(150)], crs=CRS).to_crs(4326).iloc[0]
    rings = [[[round(y, 5), round(x, 5)] for x, y in g.exterior.coords]
             for g in getattr(b_ll, "geoms", [b_ll])]

    return {
        "meta": {
            "pct": round(pct, 1), "reach_km2": round(reach_km2, 1), "land_km2": round(land_km2, 1),
            "circle_km2": round(circle_km2, 1), "speed_kmh": SPEED_KMH, "minutes": MINUTES,
            "radius_m": RADIUS_M, "reach_buffer_m": REACH_BUFFER_M, "built": date.today().isoformat(),
        },
        "stations": st_out, "lines": lines, "boundary": rings,
    }


# ---------------- web page ----------------
HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Metro Manila Rail Walkshed</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
:root{--bg:#efebe2;--ink:#161616;--blue:#2b74b8;--red:#c0453f;--hl:#f28e2b}
html,body{height:100%;margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink)}
#map{position:absolute;inset:0}
#panel{position:absolute;z-index:1000;top:12px;left:12px;width:320px;max-width:calc(100vw - 24px);
  background:rgba(239,235,226,.96);border-radius:10px;padding:14px;box-shadow:0 2px 12px rgba(0,0,0,.25);
  max-height:calc(100vh - 24px);overflow:auto;box-sizing:border-box}
h1{font-size:17px;line-height:1.25;margin:0 0 6px}h1 b{color:var(--blue)}
.sub{font-size:12px;color:#444;margin:0 0 10px}
#q{width:100%;box-sizing:border-box;padding:8px 10px;font-size:14px;border:1px solid #b9b3a6;border-radius:6px;background:#fff}
#results{list-style:none;margin:4px 0 0;padding:0;background:#fff;border-radius:6px;border:1px solid #d8d3c6}
#results:empty{display:none}
#results li{padding:7px 10px;font-size:13px;cursor:pointer}
#results li:hover,#results li.on{background:#e4edf6}
#info{margin-top:10px;font-size:13px;line-height:1.45}
#info .n{font-weight:700;font-size:15px}
.row{display:flex;align-items:center;gap:8px;font-size:12px;margin-top:6px}
.sw{display:inline-block;width:22px;height:0;border-top:3px solid var(--blue)}
.sw.d{border-top:2px dashed var(--red)}.sw.h{border-top-color:var(--hl)}
.sw.dot{width:9px;height:9px;border:0;border-radius:50%;background:var(--red)}
label{cursor:pointer}
.foot{margin-top:10px;font-size:10.5px;color:#555;line-height:1.4}
</style></head><body>
<div id="map"></div>
<div id="panel">
  <h1 id="h"></h1>
  <p class="sub" id="sub"></p>
  <input id="q" type="search" placeholder="Search station..." autocomplete="off" spellcheck="false">
  <ul id="results"></ul>
  <div id="info"></div>
  <div class="row"><input type="checkbox" id="tReach" checked><span class="sw"></span><label for="tReach">streets reached in <span class="mn"></span> min</label></div>
  <div class="row"><input type="checkbox" id="tCircle" checked><span class="sw d"></span><label for="tCircle"><span class="rk"></span> km as the crow flies</label></div>
  <div class="row"><input type="checkbox" id="tBnd" checked><span class="sw" style="border-top:1px solid #7a8fa6"></span><label for="tBnd">NCR boundary</label></div>
  <div class="row"><span class="sw dot"></span> rail station</div>
  <div class="foot" id="foot"></div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
fetch('data.json').then(r=>r.json()).then(init).catch(e=>{document.getElementById('h').textContent='Failed to load data.json: '+e;});
function init(D){
  const M=D.meta, BLUE='#2b74b8', RED='#c0453f', HL='#f28e2b';
  const $=id=>document.getElementById(id);
  $('h').innerHTML='Only <b>'+M.pct.toFixed(0)+'%</b> of Metro Manila is a '+M.minutes+' minute walk from a rail station';
  $('sub').textContent=M.reach_km2+' of '+M.land_km2+' km\u00b2 of land is within reach \u00b7 '+M.circle_km2+' km\u00b2 inside the circles';
  document.querySelectorAll('.mn').forEach(e=>e.textContent=M.minutes);
  document.querySelectorAll('.rk').forEach(e=>e.textContent=(M.radius_m/1000).toFixed(1));
  $('foot').textContent='Streets and stations: \u00a9 OpenStreetMap contributors (ODbL). Walk '+M.speed_kmh+' km/h, '+M.minutes+' min = '+(M.radius_m/1000).toFixed(1)+' km. Reach = '+M.reach_buffer_m+' m around reachable streets. Built '+M.built+'.';

  const map=L.map('map',{preferCanvas:true,zoomControl:false}).setView([14.6,121.03],11);
  L.control.zoom({position:'bottomright'}).addTo(map);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',{
    maxZoom:19,subdomains:'abcd',attribution:'\u00a9 OpenStreetMap contributors \u00a9 CARTO'}).addTo(map);
  const canvas=L.canvas({padding:0.5});

  const bndLayer=L.layerGroup(D.boundary.map(r=>L.polygon(r,{color:'#7a8fa6',weight:1,fill:false,interactive:false}))).addTo(map);
  const reachLayer=L.polyline(D.lines,{renderer:canvas,color:BLUE,weight:2,opacity:.85,interactive:false}).addTo(map);
  const circleLayer=L.layerGroup(D.stations.map(s=>L.circle([s.lat,s.lon],{radius:M.radius_m,color:RED,weight:1,dashArray:'4 4',fill:false,interactive:false}))).addTo(map);
  const sel=L.layerGroup().addTo(map);

  const markers=D.stations.map((s,i)=>{
    const m=L.circleMarker([s.lat,s.lon],{radius:5,color:'#fff',weight:1,fillColor:RED,fillOpacity:1});
    m.bindTooltip(s.name);
    m.on('click',()=>select(i,false));
    return m;
  });
  const markerLayer=L.layerGroup(markers).addTo(map);

  function select(i,fly){
    const s=D.stations[i]; sel.clearLayers();
    const c=L.circle([s.lat,s.lon],{radius:M.radius_m,color:HL,weight:2,fillColor:HL,fillOpacity:.06,interactive:false}).addTo(sel);
    L.polyline(s.edges.map(k=>D.lines[k]),{renderer:canvas,color:HL,weight:3.5,opacity:.95,interactive:false}).addTo(sel);
    L.circleMarker([s.lat,s.lon],{radius:8,color:'#fff',weight:2,fillColor:HL,fillOpacity:1,interactive:false}).addTo(sel);
    if(fly) map.flyToBounds(c.getBounds(),{padding:[30,30],duration:.8});
    const info=$('info'); info.textContent='';
    const n=document.createElement('div'); n.className='n'; n.textContent=s.name; info.appendChild(n);
    const d=document.createElement('div');
    d.textContent=s.street_km.toFixed(1)+' km of streets reachable within '+M.minutes+' min (circle area '+(Math.PI*Math.pow(M.radius_m/1000,2)).toFixed(1)+' km\u00b2).';
    info.appendChild(d);
  }

  const norm=t=>t.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
  const q=$('q'), ul=$('results'); let cur=[], idx=-1;
  function render(){
    ul.textContent='';
    cur.forEach((i,k)=>{const li=document.createElement('li'); li.textContent=D.stations[i].name;
      if(k===idx) li.className='on';
      li.onclick=()=>{pick(i)}; ul.appendChild(li);});
  }
  function pick(i){q.value=D.stations[i].name; cur=[]; idx=-1; render(); select(i,true);}
  q.addEventListener('input',()=>{
    const t=norm(q.value.trim()); idx=-1;
    cur=t?D.stations.map((s,i)=>[s,i]).filter(([s])=>norm(s.name).includes(t)).slice(0,8).map(([,i])=>i):[];
    render();
  });
  q.addEventListener('keydown',e=>{
    if(e.key==='ArrowDown'&&cur.length){idx=(idx+1)%cur.length;render();e.preventDefault();}
    else if(e.key==='ArrowUp'&&cur.length){idx=(idx-1+cur.length)%cur.length;render();e.preventDefault();}
    else if(e.key==='Enter'&&cur.length){pick(cur[idx>=0?idx:0]);}
    else if(e.key==='Escape'){cur=[];idx=-1;render();}
  });

  const toggle=(id,layer)=>$(id).addEventListener('change',e=>e.target.checked?layer.addTo(map):map.removeLayer(layer));
  toggle('tReach',reachLayer); toggle('tCircle',circleLayer); toggle('tBnd',bndLayer);
}
</script></body></html>
"""


def write_site(data):
    SITE.mkdir(exist_ok=True)
    (SITE / "data.json").write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    (SITE / "index.html").write_text(HTML, encoding="utf-8")
    log(f"wrote {SITE}/ (data.json {(SITE / 'data.json').stat().st_size / 1e6:.1f} MB)")


# ---------------- server ----------------
class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(port, open_browser):
    handler = functools.partial(Quiet, directory=str(SITE))
    httpd = None
    for p in range(port, port + 20):
        try:
            httpd = Server(("127.0.0.1", p), handler)
            break
        except OSError:
            continue
    assert httpd, f"No free port in {port}-{port + 19}"
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    log(f"serving {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.8, webbrowser.open, [url]).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("stopped")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rebuild", action="store_true", help="recompute data even if cached")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = ap.parse_args()

    if args.rebuild or not (SITE / "data.json").exists():
        write_site(build_data())
    else:
        (SITE / "index.html").write_text(HTML, encoding="utf-8")   # refresh page, keep cached data
        log("using cached data.json (--rebuild to recompute)")
    serve(args.port, not args.no_open)
