"""
Streamlit front-end for ifc-typeviewer.

Drop an IFC → name · IFC class · material set · mesh-based QTO. Choose the geometry engine
(ifcfast / IfcOpenShell / both) and mesh detail (Simplified / Original). Everything renders
**in-app**; the interactive 3D and per-object timings are right there. Local only — the file
never leaves the machine.

Run:  streamlit run app.py
"""
import inspect
import json
import os
import tempfile

import streamlit as st
import streamlit.components.v1 as components

import ifc_typeviewer as ITV
from ifc_typeviewer import extract, build_html, ENGINE_LABEL

st.set_page_config(page_title="IFC Type Viewer · ifcfast", page_icon="🧱", layout="wide")

st.markdown("""
<style>
  .stApp { background: linear-gradient(135deg,#f5f5f0 0%,#e8e4dc 100%); }
  header[data-testid="stHeader"] { background: transparent; }
  /* width is set per-view: concentrated centred on landing, full-page once a model is loaded */
  .block-container { padding-top: 2.4rem; padding-bottom: 3.5rem; margin-left:auto; margin-right:auto; }
  [data-testid="stVerticalBlock"] { gap: 1.6rem; }
  [data-testid="stHorizontalBlock"] { gap: 1.1rem; }
  [data-baseweb="tab-list"] { gap: .4rem; }
  button[data-baseweb="tab"] { font-weight:600; }
  #MainMenu, footer, .stDeployButton { visibility: hidden; display:none; }
  [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"] { display:none; }
  .app-header { background: linear-gradient(135deg,#2d4a3e 0%,#3d5a4e 100%); color:#fff;
                padding:2rem 2.2rem; border-radius:14px; margin-bottom:1rem;
                box-shadow:0 6px 20px rgba(45,74,62,.18); }
  .app-header h1 { color:#fff; margin:0; font-size:1.6rem; font-weight:600; }
  .app-header p  { color:#b8c9bf; margin:.45rem 0 0 0; font-size:.95rem; line-height:1.5; max-width:760px; }
  .scard { background:#fff; border-radius:12px; padding:1.3rem 1.1rem; box-shadow:0 2px 10px rgba(0,0,0,.06);
           text-align:center; border-left:4px solid #2d4a3e; min-height:108px;
           display:flex; flex-direction:column; justify-content:center; gap:.3rem; }
  .scard h4 { font-size:.72rem; color:#64748b; margin:0; text-transform:uppercase; letter-spacing:.04em; }
  .scard .value { font-size:1.8rem; font-weight:700; color:#334155; line-height:1.2; }
  .scard .sm { font-size:.72rem; color:#94a3b8; }
  [data-testid="stFileUploader"] { background:#fff; padding:1.1rem; border-radius:12px; border:2px dashed #9bb0a6; }
  .stMetric { background:#fff; border-radius:10px; padding:.6rem .9rem; box-shadow:0 2px 8px rgba(0,0,0,.05); }
</style>
""", unsafe_allow_html=True)

HERO = """
<div class="app-header">
  <h1>🧱 IFC Type Viewer</h1>
  <p>Pulls the mesh, computes <b>QTO from the mesh</b>, reads every type (and flags the untyped),
  and material sets — with <b>ifcfast</b> or <b>IfcOpenShell</b>, head to head on speed.</p>
</div>
"""
WIDTH_CENTERED = "<style>.block-container{max-width:1060px;}</style>"
WIDTH_FULL = "<style>.block-container{max-width:1640px;}</style>"


@st.cache_data(show_spinner=False)
def process(data: bytes, name: str, engines: tuple, simplified: bool, cap: int):
    """Cached so toggles/reruns don't re-mesh unless inputs change."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".ifc")
    try:
        tmp.write(data); tmp.flush(); tmp.close()
        payload = extract(tmp.name, engines=engines, simplified=simplified, mesh3d_cap=cap)
    finally:
        os.unlink(tmp.name)
    return payload, build_html(payload, title=name)


ENGINE_OPTS = {"ifcfast": ("ifcfast",), "IfcOpenShell": ("ifcopenshell",),
               "Both": ("ifcfast", "ifcopenshell")}

tab_view, tab_code = st.tabs(["🧱  Viewer", "🐍  Extraction code"])

with tab_view:
 hero_slot = st.container()
 up = st.file_uploader("Drop an .ifc model", type=["ifc"], label_visibility="collapsed")

 c1, c2, c3 = st.columns([1.5, 1.1, 0.8], gap="large", vertical_alignment="bottom")
 with c1:
    eng_label = st.segmented_control("Engine", list(ENGINE_OPTS), default="ifcfast",
                                     help="IfcOpenShell is the reference engine; ifcfast is the fast one. "
                                          "‘Both’ runs each and lets you toggle per object.")
 with c2:
    detail = st.segmented_control("Mesh", ["Simplified", "Original"], default="Simplified",
                                  help="Simplified = light, face-budgeted geometry. Original = full detail.")
 with c3:
    with st.popover("⚙ Options", use_container_width=True):
        cap = st.slider("Max types with 3D mesh", 100, 1500, 600, 50,
                        help="Caps embedded 3D for very large models.")

 engines = ENGINE_OPTS.get(eng_label or "ifcfast")
 simplified = (detail or "Simplified") == "Simplified"

 # colour-code the segmented "tabs" (Streamlit can't style options by label, so paint via JS)
 _TAB = {"ifcfast": ["#e0822f", "#fff"], "IfcOpenShell": ["#2f9e6e", "#fff"], "Both": ["#caa12a", "#23190a"],
         "Original": ["#2f9e6e", "#fff"], "Simplified": ["#caa12a", "#23190a"]}
 _sel = json.dumps([eng_label or "ifcfast", detail or "Simplified"])
 components.html(f"""<script>
const MAP={json.dumps(_TAB)}, SEL=new Set({_sel});
function tint(h,a){{const n=parseInt(h.slice(1),16);return 'rgba('+(n>>16)+','+((n>>8)&255)+','+(n&255)+','+a+')';}}
function paint(){{
  window.parent.document.querySelectorAll('[data-testid="stButtonGroup"] button').forEach(b=>{{
    const t=(b.innerText||'').trim(), m=MAP[t]; if(!m) return; const on=SEL.has(t);
    b.style.border='1px solid '+m[0]; b.style.background=on?m[0]:tint(m[0],.12);
    b.style.color=on?m[1]:m[0]; b.style.fontWeight='700';
  }});
}}
if(window.parent.__tabPaint) clearInterval(window.parent.__tabPaint);
window.parent.__tabPaint=setInterval(paint,200); paint();
</script>""", height=0)

 if up:
    # ---- ran: switch to the full-page, content-dense report ----------------
    st.markdown(WIDTH_FULL, unsafe_allow_html=True)
    slow = "ifcopenshell" in engines
    with st.spinner(f"Reading {up.name} and meshing…" + ("  (IfcOpenShell is slower — hang tight)" if slow else "")):
        payload, html = process(up.getvalue(), up.name, engines, simplified, cap)

    r1 = st.columns(3, gap="medium")
    r1[0].markdown(f'<div class="scard"><h4>Types</h4><div class="value">{payload["n_types"]:,}</div></div>', unsafe_allow_html=True)
    r1[1].markdown(f'<div class="scard"><h4>Elements</h4><div class="value">{payload["n_elements"]:,}</div></div>', unsafe_allow_html=True)
    r1[2].markdown(f'<div class="scard"><h4>Mesh volume</h4><div class="value">{payload["tot_vol"]:,.0f}<span class="sm"> m³</span></div></div>', unsafe_allow_html=True)

    r2 = st.columns(2 + len(payload["engines"]), gap="medium")
    nu_el, nu_t = payload["n_untyped_el"], payload["n_untyped_types"]
    acc_u = "#d97706" if nu_el else "#2d4a3e"
    r2[0].markdown(f'<div class="scard" style="border-left-color:{acc_u}"><h4>Untyped</h4>'
                   f'<div class="value" style="color:{acc_u}">{nu_el:,}</div>'
                   f'<div class="sm">{nu_t:,} buckets</div></div>', unsafe_allow_html=True)
    ndup = payload["n_dup_guids"]
    acc_d = "#d97706" if ndup else "#2d4a3e"
    r2[1].markdown(f'<div class="scard" style="border-left-color:{acc_d}"><h4>Duplicate GUIDs</h4>'
                   f'<div class="value" style="color:{acc_d}">{ndup:,}</div>'
                   f'<div class="sm">deduped</div></div>', unsafe_allow_html=True)
    accent = {"ifcfast": "#e0822f", "ifcopenshell": "#2f9e6e"}
    for i, e in enumerate(payload["engines"]):
        t = payload["timing"][e]
        r2[2 + i].markdown(
            f'<div class="scard" style="border-left-color:{accent[e]}"><h4>{ENGINE_LABEL[e]} time</h4>'
            f'<div class="value" style="color:{accent[e]}">{t["wall_s"]:.2f}<span class="sm"> s</span></div>'
            f'<div class="sm">{t["ms_per_obj"]:.1f} ms/obj · {t["n_meshed"]:,} meshes</div></div>',
            unsafe_allow_html=True)

    if len(payload["engines"]) == 2:
        a, b = (payload["timing"][e]["wall_s"] for e in payload["engines"])
        fast_e = payload["engines"][0] if a <= b else payload["engines"][1]
        if min(a, b) > 0:
            st.success(f"⚡ **{ENGINE_LABEL[fast_e]}** was **{max(a, b)/min(a, b):.1f}× faster** "
                       f"on the same geometry. Toggle any card between engines to compare.")

    st.markdown("<div style='height:.6rem'></div>", unsafe_allow_html=True)
    components.html(html, height=1040, scrolling=True)

    with st.expander("Export the standalone HTML (same view, shareable single file)"):
        st.download_button("⬇  Download self-contained HTML", html,
                           file_name=os.path.splitext(up.name)[0] + "_types.html", mime="text/html")
 else:
    # ---- landing: concentrated, centred ------------------------------------
    st.markdown(WIDTH_CENTERED, unsafe_allow_html=True)
    with hero_slot:
        st.markdown(HERO, unsafe_allow_html=True)
    st.info("Drop an IFC — we pull the mesh, compute quantities from it, read every type "
            "(flagging the untyped), and material sets.")

with tab_code:
    st.markdown(WIDTH_CENTERED if not up else WIDTH_FULL, unsafe_allow_html=True)
    st.markdown("#### Extraction logic")
    st.caption("The exact code that reads each model — mesh, type, material set, and QTO from the "
               "geometry. Each engine pass times every object and dedups GUIDs. (HTML/3D build omitted.)")
    for _title, _fn in [
        ("Orchestrate — open, read metadata, run engine(s), assemble", ITV.extract),
        ("ifcfast pass — batch meshes, time each, dedup GUIDs", ITV._pass_ifcfast),
        ("IfcOpenShell pass — geometry iterator, time each, dedup GUIDs", ITV._pass_ifcopenshell),
        ("Accumulate per type — count, Σvolume, Σarea, keep a rep mesh", ITV._acc),
        ("QTO from the mesh — |signed volume| + surface area", ITV.mesh_metrics),
    ]:
        st.markdown(f"**{_title}**  ·  `{_fn.__name__}()`")
        st.code(inspect.getsource(_fn), language="python")
