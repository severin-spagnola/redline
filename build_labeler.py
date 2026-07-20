#!/usr/bin/env python3
"""
build_labeler.py — generate a standalone HTML labeling tool from enriched_arch.json.

The labeler is the human-facing classification surface (see DESIGN.md "Labeling
UX"): you look at your architecture as a graph and click components to mark the
thesis-critical minority. Everything defaults GREEN (editable); you only mark the
YELLOW (conditional) and RED (never) / dark-red (frozen) exceptions.

Output: a single self-contained HTML file. In the browser you:
  * click a node to cycle its level  green → yellow → red → frozen → green
  * expand a node into intra-file "clause" markers and mark those
  * click "Export policy"  → get arch.policy.json + per-component editability
  * click "Export markers"  → get the `# arch:begin/end` snippets to paste in code

The LLM is never in the trust path: a human does the labeling, the exported
policy feeds the deterministic gate (arch_gate.py). This tool only writes policy;
it enforces nothing.

Usage:
    python build_labeler.py --in ../enriched_arch.json --out labeler.html
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any, Dict, List


LEVELS = ["editable", "conditional", "never", "frozen"]
LEVEL_COLOR = {
    "editable": "#16a34a",     # green
    "conditional": "#e0a000",  # yellow/amber
    "never": "#e5484d",        # red
    "frozen": "#8b1a1a",       # dark red
}
LEVEL_LABEL = {
    "editable": "Editable (green)",
    "conditional": "Conditional (yellow)",
    "never": "Never (red)",
    "frozen": "Frozen (dark red)",
}


def _esc(s: Any) -> str:
    return html.escape(str(s), quote=True)


def build(data: Dict[str, Any]) -> str:
    comps = []
    for c in data.get("components", []):
        comps.append({
            "slug": c["component"],
            "name": c["display_name"],
            "layer": c.get("layer", ""),
            "zone": c.get("zone", ""),
            "level": c.get("editability", "editable"),
            "editRule": c.get("edit_rule", ""),
            "desc": c.get("description", ""),
            "invariants": c.get("sacred_invariants", []),
            "paths": c.get("paths", []),
            "metaPath": c.get("meta_path", ""),
            "subs": c.get("subcomponents", []),
        })
    payload = json.dumps({
        "components": comps,
        "levels": LEVELS,
        "levelColor": LEVEL_COLOR,
        "levelLabel": LEVEL_LABEL,
    }, separators=(",", ":"))
    return _PAGE.replace("__PAYLOAD__", payload)


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Editability Labeler</title>
<style>
  :root { --bg:#fff; --ink:#16202c; --muted:#6b7684; --hair:#e7ebf0; --panel:#f7f9fc; }
  * { box-sizing:border-box; }
  html,body { margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }
  header { position:sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--hair);
    padding:12px 18px; display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
  header h1 { font-size:16px; margin:0; font-weight:650; }
  header .hint { font-size:12px; color:var(--muted); }
  .legend { display:flex; gap:12px; margin-left:auto; flex-wrap:wrap; }
  .legend .item { display:inline-flex; align-items:center; gap:6px; font-size:12px; }
  .legend .sw { width:12px; height:12px; border-radius:3px; }
  .bar { display:flex; gap:8px; padding:10px 18px; border-bottom:1px solid var(--hair); flex-wrap:wrap; align-items:center; }
  .bar button { border:1px solid var(--hair); background:#fff; border-radius:8px; padding:6px 12px;
    font-size:13px; cursor:pointer; }
  .bar button:hover { background:var(--panel); }
  .bar .count { font-size:12px; color:var(--muted); }
  main { padding:16px 18px 80px; }
  .zone { margin-bottom:22px; }
  .zone h2 { font-size:13px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted);
    margin:0 0 10px; border-left:3px solid #cbd5e1; padding-left:10px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:12px; }
  .card { border:1px solid var(--hair); border-left-width:6px; border-radius:10px; padding:11px 13px;
    cursor:pointer; transition:box-shadow .12s; user-select:none; background:#fff; }
  .card:hover { box-shadow:0 4px 14px rgba(20,32,44,.10); }
  .card .top { display:flex; justify-content:space-between; align-items:flex-start; gap:8px; }
  .card .name { font-weight:600; font-size:14px; }
  .card .slug { font-family:ui-monospace,Menlo,monospace; font-size:10.5px; color:var(--muted); }
  .card .lvl { font-size:10px; font-weight:700; text-transform:uppercase; letter-spacing:.04em;
    padding:2px 8px; border-radius:20px; color:#fff; white-space:nowrap; }
  .card .desc { font-size:12px; color:var(--muted); margin:8px 0 6px; }
  .card .rule { font-size:11px; color:var(--muted); background:var(--panel); border-radius:6px; padding:6px 8px; }
  .card .expand { font-size:11px; color:#2f6bff; margin-top:8px; }
  .subs { margin-top:8px; border-top:1px dashed var(--hair); padding-top:8px; display:none; }
  .subs.open { display:block; }
  .sub { display:flex; align-items:center; gap:8px; font-size:12px; padding:3px 0; }
  .sub .sname { font-family:ui-monospace,Menlo,monospace; font-size:11px; }
  .sub .note { color:var(--muted); font-size:10.5px; }
  .sub .slvl { margin-left:auto; font-size:9.5px; font-weight:700; text-transform:uppercase;
    padding:1px 6px; border-radius:10px; color:#fff; cursor:pointer; }
  dialog { border:none; border-radius:12px; box-shadow:0 20px 60px rgba(0,0,0,.3); padding:0; max-width:720px; width:92%; }
  dialog .dh { padding:14px 18px; border-bottom:1px solid var(--hair); display:flex; justify-content:space-between; }
  dialog .db { padding:14px 18px; }
  dialog textarea { width:100%; min-height:320px; font-family:ui-monospace,Menlo,monospace; font-size:12px;
    border:1px solid var(--hair); border-radius:8px; padding:10px; }
  dialog .df { padding:12px 18px; border-top:1px solid var(--hair); display:flex; gap:8px; justify-content:flex-end; }
  dialog button { border:1px solid var(--hair); background:#fff; border-radius:8px; padding:7px 14px; cursor:pointer; }
  .prim { background:#2f6bff !important; color:#fff; border-color:#2f6bff !important; }
</style>
</head>
<body>
<div id="app"></div>
<dialog id="out">
  <div class="dh"><b id="outTitle">Export</b><button onclick="document.getElementById('out').close()">✕</button></div>
  <div class="db"><textarea id="outText" readonly></textarea></div>
  <div class="df">
    <button id="copyBtn">Copy</button>
    <button id="dlBtn" class="prim">Download</button>
  </div>
</dialog>
<script>
const DATA = __PAYLOAD__;
(function(){
  "use strict";
  const LC = DATA.levelColor, LL = DATA.levelLabel, LEVELS = DATA.levels;
  // state: slug -> level ; sub state: "slug::subname" -> level
  const state = {}; const substate = {};
  DATA.components.forEach(c => state[c.slug] = c.level || "editable");

  function next(lvl){ return LEVELS[(LEVELS.indexOf(lvl)+1) % LEVELS.length]; }

  function render(){
    const app = document.getElementById("app");
    // group by zone then layer
    const zones = {};
    DATA.components.forEach(c => { (zones[c.zone||"(unzoned)"] ||= []).push(c); });
    let counts = {editable:0,conditional:0,never:0,frozen:0};
    DATA.components.forEach(c => counts[state[c.slug]]++);
    const legend = LEVELS.map(l =>
      `<span class="item"><span class="sw" style="background:${LC[l]}"></span>${LL[l]}</span>`).join("");
    let html = `
      <header>
        <h1>Editability Labeler</h1>
        <span class="hint">Click a card to cycle its level. Mark the exceptions — everything stays green by default.</span>
        <div class="legend">${legend}</div>
      </header>
      <div class="bar">
        <button id="allGreen">Reset all → green</button>
        <button id="expPolicy" class="prim">Export policy JSON</button>
        <button id="expMarkers">Export marker snippets</button>
        <span class="count">🟢 ${counts.editable} · 🟡 ${counts.conditional} · 🔴 ${counts.never} · 🟥 ${counts.frozen}</span>
      </div>
      <main>`;
    Object.keys(zones).sort().forEach(z => {
      html += `<section class="zone"><h2>${esc(z)}</h2><div class="grid">`;
      zones[z].forEach(c => { html += card(c); });
      html += `</div></section>`;
    });
    html += `</main>`;
    app.innerHTML = html;
    wire();
  }

  function card(c){
    const lvl = state[c.slug];
    const color = LC[lvl];
    const subsHtml = (c.subs && c.subs.length) ? `
      <div class="expand" data-expand="${esc(c.slug)}">▸ ${c.subs.length} subcomponent(s) — mark intra-file clauses</div>
      <div class="subs" id="subs-${esc(c.slug)}">
        ${c.subs.map(s => subRow(c.slug, s)).join("")}
      </div>` : "";
    return `
      <div class="card" data-slug="${esc(c.slug)}" style="border-left-color:${color}">
        <div class="top">
          <div><div class="name">${esc(c.name)}</div><div class="slug">${esc(c.slug)}</div></div>
          <span class="lvl" style="background:${color}">${lvl}</span>
        </div>
        <div class="desc">${esc(c.desc)}</div>
        ${c.editRule ? `<div class="rule">${esc(c.editRule)}</div>` : ""}
        ${subsHtml}
      </div>`;
  }

  function subRow(slug, s){
    const key = slug+"::"+s.name;
    const lvl = substate[key] || "editable";
    return `<div class="sub">
      <span class="sname">${esc(s.name)}</span>
      ${s.note ? `<span class="note">${esc(s.note)}</span>` : ""}
      <span class="slvl" style="background:${LC[lvl]}" data-sub="${esc(key)}">${lvl}</span>
    </div>`;
  }

  function wire(){
    document.querySelectorAll(".card").forEach(el => {
      el.addEventListener("click", e => {
        if (e.target.closest("[data-expand]") || e.target.closest("[data-sub]")) return;
        const slug = el.dataset.slug;
        state[slug] = next(state[slug]);
        render();
      });
    });
    document.querySelectorAll("[data-expand]").forEach(el => {
      el.addEventListener("click", e => {
        e.stopPropagation();
        document.getElementById("subs-"+el.dataset.expand).classList.toggle("open");
      });
    });
    document.querySelectorAll("[data-sub]").forEach(el => {
      el.addEventListener("click", e => {
        e.stopPropagation();
        const key = el.dataset.sub;
        substate[key] = next(substate[key] || "editable");
        render();
        // keep the subs panel open after re-render
        const slug = key.split("::")[0];
        const panel = document.getElementById("subs-"+slug);
        if (panel) panel.classList.add("open");
      });
    });
    document.getElementById("allGreen").addEventListener("click", () => {
      Object.keys(state).forEach(k => state[k]="editable");
      Object.keys(substate).forEach(k => delete substate[k]);
      render();
    });
    document.getElementById("expPolicy").addEventListener("click", exportPolicy);
    document.getElementById("expMarkers").addEventListener("click", exportMarkers);
  }

  function exportPolicy(){
    // Only non-green components go in the policy's per-component overrides; the
    // gate config below carries the default level behaviors + override signals.
    const comps = DATA.components.map(c => {
      const o = { component:c.slug, editability:state[c.slug] };
      if (c.paths && c.paths.length) o.paths = c.paths;
      if (c.editRule) o.edit_rule = c.editRule;
      return o;
    });
    const config = {
      spec_version:"0.1",
      annotation_glob:"**/redline.meta.json",
      protected_branches:["main"],
      unannotated_policy:"pass",
      override_mode:"any",
      levels:{
        editable:{on_change:"pass"},
        conditional:{on_change:"require", override:["justification","code_owner"]},
        never:{on_change:"block", override:["justification","code_owner"]},
        frozen:{on_change:"block", override:["justification","code_owner","strong_label"], override_mode:"all"}
      },
      overrides:{
        justification:{type:"pr_body_block", heading:"Arch-Override"},
        code_owner:{type:"codeowners_review"},
        strong_label:{type:"label", name:"arch-frozen-approved"}
      }
    };
    const bundle =
      "// ===== arch.policy.json (gate config) =====\n" +
      JSON.stringify(config, null, 2) +
      "\n\n// ===== per-component editability (merge into your redline.meta.json files) =====\n" +
      JSON.stringify({components:comps.filter(c=>c.editability!=="editable")}, null, 2) +
      "\n\n// (components not listed default to 'editable' / green)\n";
    showOut("Export policy", bundle, "arch.policy.bundle.txt");
  }

  function exportMarkers(){
    // Emit `# arch:begin/end` snippets for every sub-clause marked non-green.
    const lines = ["# Paste each block around the corresponding code region.\n"];
    let any = false;
    DATA.components.forEach(c => {
      (c.subs||[]).forEach(s => {
        const key = c.slug+"::"+s.name;
        const lvl = substate[key];
        if (lvl && lvl !== "editable") {
          any = true;
          const clause = (c.slug+"-"+s.name).replace(/[^a-z0-9-]+/gi,"-").toLowerCase();
          const reason = (s.note || c.editRule || "").replace(/"/g,"'");
          lines.push(
            `# in the file for component '${c.slug}':`,
            `# arch:begin ${clause} ${lvl} reason="${reason}"`,
            `#   ... the ${s.name} region ...`,
            `# arch:end ${clause}`, "");
        }
      });
    });
    if (!any) lines.push("# (no intra-file clauses marked yet — expand a component and mark a subcomponent)");
    showOut("Export marker snippets", lines.join("\n"), "arch.markers.txt");
  }

  function showOut(title, text, filename){
    document.getElementById("outTitle").textContent = title;
    document.getElementById("outText").value = text;
    const dl = document.getElementById("dlBtn");
    dl.onclick = () => {
      const blob = new Blob([text], {type:"text/plain"});
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = filename; a.click();
    };
    document.getElementById("copyBtn").onclick = () => {
      navigator.clipboard && navigator.clipboard.writeText(text);
    };
    document.getElementById("out").showModal();
  }

  function esc(s){ return String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
  render();
})();
</script>
</body>
</html>"""


def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Generate the HTML editability labeler.")
    ap.add_argument("--in", dest="inp", default=str(here.parent / "enriched_arch.json"))
    ap.add_argument("--out", default=str(here / "labeler.html"))
    args = ap.parse_args(argv)
    data = json.loads(Path(args.inp).read_text())
    Path(args.out).write_text(build(data))
    print(f"[build_labeler] Wrote {args.out} ({Path(args.out).stat().st_size} bytes) "
          f"from {len(data.get('components', []))} components")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
