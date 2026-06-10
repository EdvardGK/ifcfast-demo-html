"""
ifc-typeviewer — drop ANY IFC, get a self-contained HTML type explorer.

Reads only what every IFC carries: name · IFC class/type · material(set) · QTO from the MESH
(volume, area, bbox from geometry — no BaseQuantities needed). Two geometry engines so you can
compare/contrast: **ifcfast** (fast batch) and **IfcOpenShell**. Run either or both; in "both"
mode every object can be toggled between engines and you see how long each took (aggregate + per
object). Output is ONE standalone .html (three.js embedded → opens via file://, double-click).

CLI:  python ifc_typeviewer.py model.ifc [-o out.html] [--engine ifcfast|ifcopenshell|both] [--original]
Lib:  from ifc_typeviewer import extract, build_html
      html = build_html(extract("model.ifc", engines=("ifcfast","ifcopenshell")), title="model.ifc")
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import defaultdict
from pathlib import Path

VENDOR = Path(__file__).resolve().parent / "vendor"

SKIP = {"IfcOpeningElement", "IfcAnnotation", "IfcSpace", "IfcGrid", "IfcGridAxis",
        "IfcVirtualElement", "IfcVoidingFeature"}
ENGINE_LABEL = {"ifcfast": "ifcfast", "ifcopenshell": "IfcOpenShell"}


# ----------------------------------------------------------- mesh math
def _farea2(V, f):
    if f[0] >= len(V) or f[1] >= len(V) or f[2] >= len(V):
        return -1.0
    a, b, c = V[f[0]], V[f[1]], V[f[2]]
    ux, uy, uz = b[0]-a[0], b[1]-a[1], b[2]-a[2]
    vx, vy, vz = c[0]-a[0], c[1]-a[1], c[2]-a[2]
    cx, cy, cz = uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx
    return cx*cx + cy*cy + cz*cz


def mesh_metrics(V, F):
    import numpy as np
    tri = V[F]
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    area = float(0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1).sum())
    vol = float(abs(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0))
    return vol, area


def simplify3d(V, F, max_faces=200):
    """Largest faces kept (None = keep all), used verts re-indexed, centred → {v,f} flat."""
    if not V or not F:
        return None
    F = [f for f in F if max(f) < len(V)]
    if not F:
        return None
    if max_faces and len(F) > max_faces:
        F = sorted(F, key=lambda f: _farea2(V, f), reverse=True)[:max_faces]
    remap, verts, faces = {}, [], []
    for f in F:
        for idx in f:
            j = remap.get(idx)
            if j is None:
                j = remap[idx] = len(verts); verts.append(V[idx])
            faces.append(j)
    if not verts:
        return None
    n = len(verts)
    cx = sum(v[0] for v in verts)/n; cy = sum(v[1] for v in verts)/n; cz = sum(v[2] for v in verts)/n
    flatv = []
    for v in verts:
        flatv += [round(v[0]-cx, 3), round(v[1]-cy, 3), round(v[2]-cz, 3)]
    return {"v": flatv, "f": faces}


COS30, SIN30 = math.cos(math.radians(30)), math.sin(math.radians(30))


def project_mesh(V, F, max_faces=160):
    if not V or not F:
        return None
    F = [f for f in F if max(f) < len(V)]
    if len(F) > max_faces:
        F = sorted(F, key=lambda f: _farea2(V, f), reverse=True)[:max_faces]
    tris = []
    minx = miny = 1e18; maxx = maxy = -1e18
    for a, b, c in F:
        p = [V[a], V[b], V[c]]
        ux, uy, uz = (p[1][0]-p[0][0], p[1][1]-p[0][1], p[1][2]-p[0][2])
        vx, vy, vz = (p[2][0]-p[0][0], p[2][1]-p[0][1], p[2][2]-p[0][2])
        nx, ny, nz = (uy*vz-uz*vy, uz*vx-ux*vz, ux*vy-uy*vx)
        nl = math.sqrt(nx*nx+ny*ny+nz*nz) or 1
        shade = 0.4 + 0.6*abs(nz/nl)
        depth = sum(v[0]+v[1]+v[2] for v in p) / 3.0
        scr = []
        for (x, y, z) in p:
            sx = (x - y) * COS30; sy = (x + y) * SIN30 - z
            scr.append((sx, sy)); minx = min(minx, sx); maxx = max(maxx, sx); miny = min(miny, sy); maxy = max(maxy, sy)
        tris.append((scr, round(shade, 2), depth))
    if not tris:
        return None
    tris.sort(key=lambda t: t[2])
    w = (maxx-minx) or 1; h = (maxy-miny) or 1; sc = 96/max(w, h)
    ox = (100-w*sc)/2; oy = (100-h*sc)/2
    out = []
    for scr, shade, _ in tris:
        flat = []
        for (sx, sy) in scr:
            flat += [round((sx-minx)*sc+ox, 1), round((sy-miny)*sc+oy, 1)]
        out.append(flat + [shade])
    return out


def _s(v):
    return "" if v is None else str(v).strip()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------- per-type accumulator
def _acc(types, key, g, V, F, dt_ms, name_of, lay, role):
    vol, area = mesh_metrics(V, F)
    t = types.get(key)
    if not t:
        t = types[key] = {"n": 0, "vol": 0.0, "area": 0.0, "t_ms": 0.0, "_rf": -1, "_V": None, "_F": None,
                          "name": name_of.get(g, ""), "layers": lay.get(g, []), "role": role.get(g, "direct")}
    t["n"] += 1; t["vol"] += vol; t["area"] += area; t["t_ms"] += dt_ms
    nf = len(F)
    if nf > t["_rf"]:
        t["_rf"] = nf; t["_V"] = V.tolist(); t["_F"] = F.tolist()
        if not t["layers"] and lay.get(g):
            t["layers"] = lay.get(g)


# ----------------------------------------------------------- engine passes
def _pass_ifcfast(m, key_of, name_of, lay, role):
    import numpy as np
    types = {}; seen = set(); dup = 0
    t0 = time.perf_counter(); tprev = t0
    for mesh in m.iter_meshes(unit="m"):
        now = time.perf_counter(); dt = (now - tprev) * 1000.0; tprev = now
        g = mesh.guid
        key = key_of.get(g)
        if key is None:
            continue
        if g in seen:                                  # GUID dedup — count each element once
            dup += 1; continue
        seen.add(g)
        V = np.asarray(mesh.vertices, float).reshape(-1, 3)
        if V.size == 0:
            continue
        F = np.asarray(mesh.faces, int).reshape(-1, 3)
        _acc(types, key, g, V, F, dt, name_of, lay, role)
    wall = time.perf_counter() - t0
    return types, {"wall_s": round(wall, 3), "n_meshed": sum(t["n"] for t in types.values()), "n_dup": dup}


def _pass_ifcopenshell(path, key_of, name_of, lay, role):
    import ifcopenshell
    import ifcopenshell.geom
    import numpy as np
    f = ifcopenshell.open(str(path))
    settings = ifcopenshell.geom.settings()           # default = SI metres, triangulated
    it = ifcopenshell.geom.iterator(settings, f, max(1, (os.cpu_count() or 2) - 1))
    types = {}; seen = set(); dup = 0
    t0 = time.perf_counter()
    if it.initialize():
        tprev = time.perf_counter()
        while True:
            sh = it.get()
            now = time.perf_counter(); dt = (now - tprev) * 1000.0; tprev = now
            g = sh.guid
            key = key_of.get(g)
            if key is not None:
                if g in seen:                          # GUID dedup — count each element once
                    dup += 1
                else:
                    seen.add(g)
                    geo = sh.geometry
                    V = np.asarray(geo.verts, float).reshape(-1, 3)
                    if V.size:
                        F = np.asarray(geo.faces, int).reshape(-1, 3)
                        _acc(types, key, g, V, F, dt, name_of, lay, role)
            if not it.next():
                break
    wall = time.perf_counter() - t0
    return types, {"wall_s": round(wall, 3), "n_meshed": sum(t["n"] for t in types.values()), "n_dup": dup}


# ----------------------------------------------------------- extract
def extract(ifc_path, engines=("ifcfast",), simplified=True, mesh3d_cap=600):
    """engines: any of ('ifcfast','ifcopenshell'). Returns a payload with per-engine mesh+QTO+timing."""
    import ifcfast
    engines = tuple(e for e in ("ifcfast", "ifcopenshell") if e in engines) or ("ifcfast",)
    faces3d = 200 if simplified else None
    faces2d = 160

    m = ifcfast.open(str(ifc_path))                    # ifcfast = shared metadata source (name/type/material)
    pdf = m.products_df
    cols = set(pdf.columns)
    tn_col = pdf["type_name"] if "type_name" in cols else [None]*len(pdf)
    nm_col = pdf["name"] if "name" in cols else [None]*len(pdf)
    key_of, name_of = {}, {}
    for g, e, tn, nm in zip(pdf["guid"], pdf["entity"], tn_col, nm_col):
        if e in SKIP:
            continue
        cls = e.replace("Ifc", "")
        key_of[g] = (cls, _s(tn) if isinstance(tn, str) else "")
        name_of[g] = _s(nm) if isinstance(nm, str) else ""

    lay, role = defaultdict(list), {}
    try:
        for r in m.materials.sort_values("layer_index").itertuples(index=False):
            lay[r.guid].append({"m": _s(r.material_name) or "(uten navn)", "t": _num(getattr(r, "layer_thickness_mm", None))})
            role.setdefault(r.guid, getattr(r, "role", "direct"))
    except Exception:
        pass

    eng_types, timing = {}, {}
    if "ifcfast" in engines:
        eng_types["ifcfast"], timing["ifcfast"] = _pass_ifcfast(m, key_of, name_of, lay, role)
    if "ifcopenshell" in engines:
        eng_types["ifcopenshell"], timing["ifcopenshell"] = _pass_ifcopenshell(ifc_path, key_of, name_of, lay, role)

    for e in eng_types:
        t = timing[e]
        t["ms_per_obj"] = round(t["wall_s"]*1000.0/t["n_meshed"], 2) if t["n_meshed"] else 0.0
        t["label"] = ENGINE_LABEL[e]

    allkeys = set()
    for et in eng_types.values():
        allkeys |= set(et.keys())

    def cnt(k):
        return max((eng_types[e].get(k, {}).get("n", 0) for e in eng_types), default=0)

    primary = engines[0]
    n3d = {e: 0 for e in eng_types}
    rows = []
    for key in sorted(allkeys, key=lambda k: -cnt(k)):
        cls, tn = key
        meta = next((eng_types[e][key] for e in eng_types if key in eng_types[e]), {})
        has_layers = meta.get("role") == "layer" and any(l["t"] for l in meta.get("layers", []))
        rec = {"cls": cls, "type": tn, "untyped": tn == "", "name": meta.get("name", ""), "n": cnt(key),
               "layers": meta.get("layers", []), "graphic": "layer" if has_layers else "mesh", "e": {}}
        for e, et in eng_types.items():
            t = et.get(key)
            if not t:
                continue
            d = {"vol": round(t["vol"], 3), "area": round(t["area"], 2),
                 "vpu": round(t["vol"]/t["n"], 4) if t["n"] else 0.0,
                 "t_ms": round(t["t_ms"], 1), "n": t["n"]}
            V, F = t.get("_V"), t.get("_F")
            if V and F:
                d["mesh"] = project_mesh(V, F, faces2d)
                if n3d[e] < mesh3d_cap:
                    m3 = simplify3d(V, F, faces3d)
                    if m3:
                        d["mesh3d"] = m3; n3d[e] += 1
            rec["e"][e] = d
        if rec["graphic"] == "mesh" and not any(d.get("mesh") for d in rec["e"].values()):
            rec["graphic"] = "solid"
        rows.append(rec)

    def eng_tot(field):
        return round(sum((r["e"].get(primary) or next(iter(r["e"].values()), {})).get(field, 0) for r in rows), 2)

    return {"title": "", "engines": list(engines), "simplified": simplified,
            "timing": timing, "primary": primary,
            "n_types": len(rows),
            "n_elements": max((sum(t["n"] for t in et.values()) for et in eng_types.values()), default=0),
            "n_untyped_types": sum(1 for r in rows if r["untyped"]),
            "n_untyped_el": sum(r["n"] for r in rows if r["untyped"]),
            "n_dup_guids": timing.get(primary, {}).get("n_dup", 0),
            "tot_vol": eng_tot("vol"), "tot_area": eng_tot("area"),
            "n_3d": n3d, "rows": rows}


# ----------------------------------------------------------- html
def build_html(payload, title="IFC"):
    payload = {**payload, "title": str(title)}
    three = (VENDOR / "three.module.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    orbit = (VENDOR / "OrbitControls.js").read_text(encoding="utf-8").replace("</script", "<\\/script")
    html = TEMPLATE.replace("/*__DATA__*/", json.dumps(payload, ensure_ascii=False))
    html = html.replace("/*__THREE__*/", three).replace("/*__ORBIT__*/", orbit)
    return html


TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>IFC Type Viewer</title>
<style>
:root{--bg:#0f1318;--panel:#171c22;--panel2:#1e242c;--line:#2a313a;--txt:#e7e3da;--txt2:#9aa1a8;--txt3:#6b7480;--accent:#5cb8ff;--fast:#6fd3a3;--ops:#e0a36f}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:12px/1.45 system-ui,Segoe UI,sans-serif}
.wrap{max-width:1850px;margin:0 auto;padding:14px 18px}
h1{font-size:17px;margin:0;display:inline-block}.sub{color:var(--txt3);font-size:11px;margin-left:10px}
.bar{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin:8px 0;padding:8px 12px;background:var(--panel);border:1px solid var(--line);border-radius:8px}
.tcmp{display:flex;gap:8px;align-items:center;font-size:12px}
.tdot{width:9px;height:9px;border-radius:50%}
.tval{font-variant-numeric:tabular-nums}.tval b{font-size:13px}
.speed{font-weight:700;color:var(--fast);border:1px solid #2c5a44;background:#10301f;border-radius:10px;padding:1px 9px}
.toolbar{display:flex;gap:8px;align-items:center;margin:10px 0;flex-wrap:wrap}
.toolbar input{flex:1;min-width:200px;background:var(--panel);border:1px solid var(--line);border-radius:7px;color:var(--txt);padding:7px 10px;font:inherit}
.toolbar select{background:var(--panel);border:1px solid var(--line);border-radius:7px;color:var(--txt);padding:7px 8px;font:inherit}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{background:var(--panel);border:0;border-right:1px solid var(--line);color:var(--txt2);padding:7px 11px;font:inherit;cursor:pointer}
.seg button:last-child{border-right:0}.seg button.on{background:var(--panel2);color:var(--txt);font-weight:700}
.btn{background:var(--panel2);border:1px solid var(--line);border-radius:7px;color:var(--txt);padding:7px 11px;font:inherit;cursor:pointer}
.cnt{color:var(--txt3);font-size:11px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:11px 12px;display:flex;flex-direction:column;gap:8px}
.card.has3d{cursor:pointer}.card.has3d:hover{border-color:#3a536f}.card.has3d:hover .d3{opacity:1}
.chead{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
.tname{font-weight:600;font-size:12px;line-height:1.3;word-break:break-word}
.ctag{display:inline-block;padding:0 6px;border-radius:4px;font-size:10px;font-weight:700;white-space:nowrap}
.utag{display:inline-block;padding:0 6px;border-radius:4px;font-size:10px;font-weight:700;background:#3a2c0f;color:#e9be72;border:1px solid #6a4f1c;white-space:nowrap}
.thead{display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end}
.chk{display:inline-flex;align-items:center;gap:5px;color:var(--txt2);font-size:11px;cursor:pointer;user-select:none;padding:0 4px}
.qto{display:flex;gap:9px;flex-wrap:wrap;font-size:11px;color:var(--txt2);font-variant-numeric:tabular-nums}
.qto b{color:var(--txt);font-weight:700}
.ms{color:var(--accent)}
.engtog{display:inline-flex;border:1px solid var(--line);border-radius:7px;overflow:hidden;font-size:10px}
.engtog span{padding:1px 7px;cursor:pointer;color:var(--txt3);font-weight:700}
.engtog span.fast.on{background:#10301f;color:var(--fast)}.engtog span.ops.on{background:#33240f;color:var(--ops)}
.sand{display:flex;height:42px;border-radius:6px;overflow:hidden;border:1px solid #000}
.sl{position:relative;min-width:3px;display:flex;align-items:center;justify-content:center;border-right:1px solid rgba(0,0,0,.35)}
.sl span{font-size:9px;color:rgba(0,0,0,.7);font-weight:700}
.gfx{height:96px;background:#0c1014;border-radius:6px;border:1px solid #000;display:block;position:relative}
.d3{position:absolute;top:6px;right:6px;font-size:9px;font-weight:800;color:#cfe6ff;background:#16263acc;border:1px solid #29456b;border-radius:6px;padding:1px 6px;opacity:.85;pointer-events:none;z-index:2}
.leg{display:flex;flex-direction:column;gap:2px}
.lr{display:flex;align-items:center;gap:6px;font-size:11px}
.sw{width:10px;height:10px;border-radius:2px;flex:0 0 auto}
.lr .nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.lr .v{color:var(--txt3);font-variant-numeric:tabular-nums}
.empty{color:var(--txt3);font-size:11px}
.ov{position:fixed;inset:0;background:rgba(6,9,12,.78);display:none;align-items:center;justify-content:center;z-index:50}
.ov.on{display:flex}
.ovbox{background:var(--panel);border:1px solid var(--line);border-radius:12px;width:min(760px,94vw);height:min(600px,90vh);display:flex;flex-direction:column;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.55)}
.ovhead{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 12px;border-bottom:1px solid var(--line);font-weight:600;font-size:12px}
.ovhead .btn{padding:4px 10px}
#ovc{flex:1;display:block;width:100%;min-height:0;background:#0a0d11;cursor:grab}#ovc:active{cursor:grabbing}
.ovfoot{padding:8px 12px;border-top:1px solid var(--line);color:var(--txt2);font-size:11px;display:flex;gap:14px;flex-wrap:wrap}
</style></head><body><div class="wrap">
<h1>IFC Type Viewer</h1><span class="sub" id="sub"></span>
<div class="bar" id="tbar"></div>
<div class="toolbar">
 <input id="q" placeholder="Search name / type / class / material…">
 <select id="cf"><option value="">All classes</option></select>
 <select id="gf"><option value="">All graphics</option><option value="layer">Material set</option><option value="mesh">Mesh</option><option value="solid">Solid</option></select>
 <select id="sf"><option value="n">Sort: count</option><option value="vol">Sort: volume</option><option value="area">Sort: area</option><option value="t_ms">Sort: time</option><option value="name">Sort: name</option></select>
 <label class="chk"><input type="checkbox" id="uf"> ⚠ untyped only</label>
 <span id="engsel"></span>
 <button class="btn" id="csv">Export CSV</button><span class="cnt" id="cnt"></span>
</div>
<div class="grid" id="grid"></div>
<div style="color:var(--txt3);font-size:10px;margin-top:10px">ifcfast / IfcOpenShell · mesh-based QTO (volume &amp; area from geometry) · total across all instances + per-unit · time per object · material set = layer thickness · click a card for 3D.</div>
</div>
<script>
const D = /*__DATA__*/;
let shown=[], curEng=D.engines[0], cardEng={};
const $=s=>document.querySelector(s);
const fmt=n=>(n==null?'':typeof n==="number"?n.toLocaleString("en-US",{maximumFractionDigits:2}):n);
const esc=s=>String(s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
function hcol(s,sat,lig){let h=0;s=String(s||"");for(let i=0;i<s.length;i++)h=(h*31+s.charCodeAt(i))>>>0;return `hsl(${h%360},${sat}%,${lig}%)`;}
const matColor=s=>hcol(s,42,52);
const EC={ifcfast:'fast',ifcopenshell:'ops'};
const ECOL={ifcfast:'#6fd3a3',ifcopenshell:'#e0a36f'};
const key=r=>r.cls+'|'+r.type;
const dataOf=r=>{const e=cardEng[key(r)]||curEng;return r.e[e]||Object.values(r.e)[0]||{};};
const engOf=r=>{const e=cardEng[key(r)]||curEng;return r.e[e]?e:Object.keys(r.e)[0];};

$("#sub").textContent=`${D.title} · ${fmt(D.n_types)} types · ${fmt(D.n_elements)} elements · ${fmt(D.tot_vol)} m³`;
[...new Set(D.rows.map(r=>r.cls))].sort().forEach(c=>{const o=document.createElement("option");o.value=o.textContent=c;$("#cf").appendChild(o);});

// timing bar + engine selector
(function(){
 const T=D.timing, es=D.engines;
 let h='';
 es.forEach(e=>{const t=T[e];h+=`<div class="tcmp"><span class="tdot" style="background:${ECOL[e]}"></span><span class="tval"><b>${t.label}</b> · ${fmt(t.wall_s)} s · ${fmt(t.ms_per_obj)} ms/obj · ${fmt(t.n_meshed)} meshes</span></div>`;});
 if(es.length===2){const a=T[es[0]].wall_s,b=T[es[1]].wall_s;const slow=Math.max(a,b),fastv=Math.min(a,b);const who=a<b?T[es[0]].label:T[es[1]].label;if(fastv>0)h+=`<span class="speed">${who} ${fmt(slow/fastv)}× faster</span>`;}
 $("#tbar").innerHTML=h;
 if(es.length>1){let s='<span class="cnt" style="margin-right:4px">Engine</span><span class="seg" id="eseg">';
  es.forEach(e=>s+=`<button data-e="${e}" class="${e===curEng?'on':''}">${T[e].label}</button>`);
  s+='</span>'; $("#engsel").innerHTML=s;
  $("#eseg").addEventListener("click",ev=>{const b=ev.target.closest("button");if(!b)return;curEng=b.dataset.e;cardEng={};[...$("#eseg").children].forEach(x=>x.classList.toggle("on",x.dataset.e===curEng));render();});
 }
})();

function sandwich(ls){const tot=ls.reduce((s,l)=>s+(l.t||0),0)||ls.length;
 return `<div class="sand">`+ls.map(l=>{const g=(l.t||tot/ls.length)/tot*100;
  return `<div class="sl" title="${esc(l.m)} ${l.t?l.t+' mm':''}" style="flex:${g.toFixed(3)} 1 0;background:${matColor(l.m)}">${l.t&&g>7?`<span>${fmt(l.t)}</span>`:''}</div>`;}).join("")+`</div>`;}
function meshSVG(tris,color){return `<svg class="gfx" viewBox="0 0 100 100" preserveAspectRatio="xMidYMid meet">`+tris.map(t=>{
  const L=t[6],c=shade(color,L);return `<polygon points="${t[0]},${t[1]} ${t[2]},${t[3]} ${t[4]},${t[5]}" fill="${c}" stroke="${c}" stroke-width="0.3"/>`;}).join("")+`</svg>`;}
function shade(hsl,l){const m=hsl.match(/hsl\((\d+),(\d+)%,(\d+)%\)/);if(!m)return hsl;return `hsl(${m[1]},${m[2]}%,${Math.round(+m[3]*(0.45+0.55*l))}%)`;}
function legend(ls){const seen=new Set(),u=[];ls.forEach(l=>{if(!seen.has(l.m)){seen.add(l.m);u.push(l);}});
 return `<div class="leg">`+u.slice(0,5).map(l=>`<div class="lr"><span class="sw" style="background:${matColor(l.m)}"></span><span class="nm" title="${esc(l.m)}">${esc(l.m)}</span><span class="v">${l.t?fmt(l.t)+' mm':''}</span></div>`).join("")+`</div>`;}
function engTog(r){if(D.engines.length<2)return"";const cur=engOf(r);
 return `<span class="engtog" data-k="${esc(key(r))}">`+D.engines.map(e=>`<span class="${EC[e]} ${e===cur?'on':''}" data-e="${e}" title="${D.timing[e].label}">${r.e[e]?D.timing[e].label:'–'}</span>`).join("")+`</span>`;}

function render(){
 const q=$("#q").value.trim().toLowerCase(),cf=$("#cf").value,gf=$("#gf").value,sf=$("#sf").value,uf=$("#uf").checked;
 let rows=D.rows.filter(r=>(!cf||r.cls===cf)&&(!gf||r.graphic===gf)&&(!uf||r.untyped)&&
  (!q||((r.name||"")+" "+r.type+" "+r.cls+" "+(r.layers||[]).map(l=>l.m).join(" ")).toLowerCase().includes(q)));
 const val=(r,f)=>f==="name"?(r.type||r.name||""):f==="n"?r.n:(dataOf(r)[f]||0);
 rows.sort(sf==="name"?(a,b)=>val(a,sf).localeCompare(val(b,sf)):(a,b)=>val(b,sf)-val(a,sf));
 $("#cnt").textContent=`${fmt(rows.length)} types · ${fmt(rows.reduce((s,r)=>s+r.n,0))} el`;
 shown=rows.slice(0,400);
 $("#grid").innerHTML=shown.map((r,i)=>{
  const d=dataOf(r);
  const cc=hcol(r.cls,38,24),ct=hcol(r.cls,55,72);
  const nm=r.untyped?'(untyped)':(r.type||r.name||'<no type>');
  const d3=d.mesh3d?`<span class="d3">⤢ 3D</span>`:"";
  let gfx="",leg="";
  if(r.graphic==="layer"){gfx=sandwich(r.layers);leg=legend(r.layers);}
  else if(d.mesh){gfx=meshSVG(d.mesh,hcol(r.cls,45,60));if(r.layers&&r.layers.length)leg=legend(r.layers);}
  else{gfx=`<div class="gfx" style="display:flex;align-items:center;justify-content:center;color:#39424d">no mesh</div>`;if(r.layers&&r.layers.length)leg=legend(r.layers);}
  const vol=d.vol>0?`<span><b>${fmt(d.vol)}</b> m³</span>`:"";
  const area=d.area>0?`<span><b>${fmt(d.area)}</b> m²</span>`:"";
  const vpu=d.vol>0?`<span>${fmt(d.vpu)} m³/stk</span>`:"";
  const ms=d.t_ms!=null?`<span class="ms">${fmt(d.t_ms)} ms</span>`:"";
  return `<div class="card${d.mesh3d?' has3d':''}" data-i="${i}"${d.mesh3d?' title="Click for 3D"':''}>
   <div class="chead"><div class="tname">${esc(nm)}</div><div class="thead">${r.untyped?'<span class="utag">⚠ untyped</span>':''}<span class="ctag" style="background:${cc};color:${ct}">${esc(r.cls)}</span></div></div>
   <div class="qto"><span><b>${fmt(r.n)}</b> stk</span>${vol}${area}${vpu}${ms}</div>
   ${engTog(r)}
   <div style="position:relative">${d3}${gfx}</div>${leg}</div>`;}).join("")||`<div class="empty">No matches.</div>`;
 if(rows.length>400)$("#cnt").textContent+=" (showing 400)";
}
$("#q").oninput=render;$("#cf").onchange=render;$("#gf").onchange=render;$("#sf").onchange=render;$("#uf").onchange=render;
$("#grid").addEventListener("click",e=>{
 const tg=e.target.closest(".engtog span");
 if(tg){const k=tg.closest(".engtog").dataset.k,en=tg.dataset.e;cardEng[k]=en;render();return;}
 const c=e.target.closest(".card");if(!c)return;const r=shown[+c.dataset.i];const d=dataOf(r);
 if(d&&d.mesh3d&&window.openMesh3D)window.openMesh3D(r,d,engOf(r));});
$("#csv").onclick=()=>{const eng=curEng;const L=[`name;type;class;count;engine;volume_m3;area_m2;vol_per_unit_m3;time_ms;materials`];
 D.rows.forEach(r=>{const d=r.e[eng]||Object.values(r.e)[0]||{};L.push([`"${(r.name||'').replace(/"/g,'""')}"`,`"${(r.type||'').replace(/"/g,'""')}"`,r.cls,r.n,eng,d.vol||0,d.area||0,d.vpu||0,d.t_ms||0,`"${(r.layers||[]).map(l=>l.m).join(' | ').replace(/"/g,'""')}"`].join(";"));});
 const b=new Blob(["﻿"+L.join("\n")],{type:"text/csv;charset=utf-8"});const u=document.createElement("a");u.href=URL.createObjectURL(b);u.download="ifc_types.csv";u.click();};
render();
</script>
<div class="ov" id="ov"><div class="ovbox">
 <div class="ovhead"><span id="ovt"></span><button class="btn" id="ovx">✕ Close (Esc)</button></div>
 <canvas id="ovc"></canvas>
 <div class="ovfoot" id="ovf"></div>
</div></div>
<script type="text/plain" id="three-src">/*__THREE__*/</script>
<script type="text/plain" id="orbit-src">/*__ORBIT__*/</script>
<script type="module">
const _tURL=URL.createObjectURL(new Blob([document.getElementById('three-src').textContent],{type:'text/javascript'}));
const _oTxt=document.getElementById('orbit-src').textContent.replace(/from\s*['"](?:three|\.\/three\.module\.js)['"]/g,`from '${_tURL}'`);
const _oURL=URL.createObjectURL(new Blob([_oTxt],{type:'text/javascript'}));
const THREE=await import(_tURL);
const {OrbitControls}=await import(_oURL);
let rnd,scene,cam,ctr,curMesh,grid,raf;
const ov=document.getElementById('ov'),ovc=document.getElementById('ovc'),ovt=document.getElementById('ovt'),ovf=document.getElementById('ovf');
function clsColor(s){let h=0;s=String(s||"");for(let i=0;i<s.length;i++)h=(h*31+s.charCodeAt(i))>>>0;const c=new THREE.Color();c.setHSL((h%360)/360,0.45,0.62);return c;}
function init(){
 rnd=new THREE.WebGLRenderer({canvas:ovc,antialias:true}); rnd.setPixelRatio(Math.min(2,window.devicePixelRatio||1));
 scene=new THREE.Scene(); scene.background=new THREE.Color(0x0a0d11);
 cam=new THREE.PerspectiveCamera(45,1,1e-3,1e5);
 ctr=new OrbitControls(cam,ovc); ctr.enableDamping=true; ctr.dampingFactor=0.08;
 scene.add(new THREE.HemisphereLight(0xcfe0ff,0x202833,1.0));
 const d=new THREE.DirectionalLight(0xffffff,1.5); d.position.set(1,1.6,1.2); scene.add(d);
 const d2=new THREE.DirectionalLight(0x8da9d8,0.6); d2.position.set(-1.2,-0.4,-1); scene.add(d2);
}
function resize(){const w=ovc.clientWidth||720,h=ovc.clientHeight||440; rnd.setSize(w,h,false); cam.aspect=w/h; cam.updateProjectionMatrix();}
function loop(){ if(!ov.classList.contains('on'))return; ctr.update(); rnd.render(scene,cam); raf=requestAnimationFrame(loop);}
function clr(){ if(curMesh){scene.remove(curMesh);curMesh.geometry.dispose();curMesh.material.dispose();curMesh=null;} if(grid){scene.remove(grid);grid.geometry.dispose();grid.material.dispose();grid=null;} }
window.openMesh3D=function(r,d,eng){
 if(!rnd) init();
 clr();
 ovt.textContent=`${r.type||r.name||'<no type>'} · ${r.cls}${eng?'  ·  '+eng:''}`;
 const m3=d.mesh3d;
 const g=new THREE.BufferGeometry();
 g.setAttribute('position',new THREE.Float32BufferAttribute(m3.v,3));
 g.setIndex(m3.f); g.computeVertexNormals(); g.computeBoundingSphere();
 const mat=new THREE.MeshStandardMaterial({color:clsColor(r.cls),roughness:0.62,metalness:0.12,side:THREE.DoubleSide,flatShading:true});
 curMesh=new THREE.Mesh(g,mat); curMesh.rotation.x=-Math.PI/2;
 scene.add(curMesh);
 const rad=(g.boundingSphere&&g.boundingSphere.radius)||1;
 const dist=rad/Math.sin(Math.PI*45/360)*1.25;
 cam.near=Math.max(1e-3,rad/200); cam.far=rad*200; cam.updateProjectionMatrix();
 cam.position.set(dist*0.85,dist*0.6,dist*0.95); ctr.target.set(0,0,0); ctr.update();
 grid=new THREE.GridHelper(rad*4,16,0x32404f,0x1c232b); grid.position.y=-rad; scene.add(grid);
 ovf.textContent=`${m3.f.length/3} faces · ${m3.v.length/3} pts · ${r.n} instances · ${d.vol>0?d.vol.toLocaleString('en-US',{maximumFractionDigits:2})+' m³':''} · ${d.t_ms!=null?d.t_ms+' ms':''} · drag=rotate · scroll=zoom · right-drag=pan`;
 ov.classList.add('on'); resize(); loop();
};
function close(){ ov.classList.remove('on'); if(raf)cancelAnimationFrame(raf); clr(); }
document.getElementById('ovx').onclick=close;
ov.addEventListener('click',e=>{ if(e.target===ov) close(); });
window.addEventListener('keydown',e=>{ if(e.key==='Escape') close(); });
window.addEventListener('resize',()=>{ if(ov.classList.contains('on')) resize(); });
</script></body></html>"""


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Drop any IFC -> self-contained HTML type viewer.")
    ap.add_argument("ifc")
    ap.add_argument("-o", "--out")
    ap.add_argument("--engine", choices=["ifcfast", "ifcopenshell", "both"], default="ifcfast")
    ap.add_argument("--original", action="store_true", help="keep full mesh detail (no simplify)")
    ap.add_argument("--cap3d", type=int, default=600)
    args = ap.parse_args()
    engines = ("ifcfast", "ifcopenshell") if args.engine == "both" else (args.engine,)
    src = Path(args.ifc)
    print(f"reading {src.name}  engines={engines}  {'original' if args.original else 'simplified'} …")
    payload = extract(src, engines=engines, simplified=not args.original, mesh3d_cap=args.cap3d)
    html = build_html(payload, title=src.name)
    out = Path(args.out) if args.out else src.with_name(src.stem + "_types.html")
    out.write_text(html, encoding="utf-8")
    t = " · ".join(f"{payload['timing'][e]['label']} {payload['timing'][e]['wall_s']}s" for e in payload["engines"])
    print(f"wrote {out}  ({out.stat().st_size/1e6:.1f} MB) · {payload['n_types']} types · "
          f"{payload['n_elements']} elements · {payload['tot_vol']} m³ · [{t}]")


if __name__ == "__main__":
    main()
