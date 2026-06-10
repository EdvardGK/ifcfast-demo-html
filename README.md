# ifcfast-demo-html — IFC Type Viewer

Drop **any** IFC, get a **single self-contained HTML** that explores it by type — with an
interactive 3D popup. A small demo of what [ifcfast](https://github.com/EdvardGK/ifcfast) can pull
out of a model with almost no assumptions.

It reads only what **every** IFC carries:

- **name** (`IfcRoot.Name` / type name)
- **IFC class** + type (`IfcWall`, `IfcBeam`, …)
- **material / material set** (layer thicknesses where present)
- **QTO from the mesh** — volume, surface area and bounding box computed straight from the
  geometry, so it works even when the model has **no `BaseQuantities`**, classifications or vendor
  property sets.

No MMI, NS3451, de-dup or vendor-specific logic — those are project work, not universal.

## Use

### Streamlit (drop a file in the browser)
```bash
pip install -r requirements.txt
streamlit run app.py
```
Drag an `.ifc` in → see the type grid + metrics → **Download self-contained HTML**.

### CLI (no server)
```bash
python ifc_typeviewer.py model.ifc            # -> model_types.html
python ifc_typeviewer.py model.ifc -o out.html --cap3d 800
```

## The output file

One `.html`. Data, geometry **and the 3D engine (three.js) are inlined**, so it opens by
double-click (`file://`) — no folder, no server, no internet. Cards show name, class, count and
mesh-QTO (m³ / m² / per-unit); material sets render as a layer sandwich; click any card with
geometry for an orbitable 3D view. Search, filter by class/graphic, sort, and CSV export are built in.

### Why three.js is embedded (not just linked)
ES-module three.js can't be `import`ed over `file://` — Chrome blocks local module fetches as CORS
`origin null`. So the build embeds `three.module.js` + `OrbitControls.js` as text and loads them via
**blob URLs** at runtime, which sidesteps the restriction and keeps the file a true single artifact.
(Served over http you wouldn't need this — see `vendor/`.)

## Layout
```
ifcfast-demo-html/
├── app.py              # Streamlit front-end
├── ifc_typeviewer.py   # extract() + build_html() + CLI
├── vendor/             # three.module.js + OrbitControls.js (embedded at build time)
├── requirements.txt
└── README.md
```
