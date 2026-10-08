#!/usr/bin/env python3
"""
BEAST2 package dependency network.

Reads the CBAN package registry (which mirrors each package's version.xml
<depends> entries), takes the latest listed version of every package and
writes an interactive HTML network of who depends on whom.

Usage:
    python3 beast2_dependency_graph.py                        # -> beast2_dependencies.html
    python3 beast2_dependency_graph.py --output docs/dependencies.html \
        --snapshots-dir beast2_snapshots                      # size nodes by downloads too
"""

import argparse
import json
import os
import re
from datetime import datetime
from urllib.request import urlopen
from xml.etree import ElementTree

from beast2_site import NAV_CSS, nav_html

CBAN_URLS = [
    "https://raw.githubusercontent.com/CompEvol/CBAN/master/packages2.7.xml",
    "https://raw.githubusercontent.com/CompEvol/CBAN/master/packages-extra-2.7.xml",
]
CORE = ["BEAST.base", "BEAST.app"]


def version_key(v):
    return [(0, int(p)) if p.isdigit() else (1, p) for p in re.findall(r"\d+|[a-z]+", v.lower())]


def load_xml(source):
    if os.path.exists(source):
        with open(source, encoding="utf-8") as f:
            return f.read()
    print(f"Fetching {source}")
    with urlopen(source) as resp:
        return resp.read().decode()


def parse_packages(sources):
    """Return {name: {version, description, url, depends: [{on, atleast, atmost}]}} for latest versions."""
    latest = {}
    for src in sources:
        root = ElementTree.fromstring(load_xml(src))
        for p in root.findall("package"):
            name = p.get("name")
            version = p.get("version", "0")
            if name in latest and version_key(version) <= version_key(latest[name]["version"]):
                continue
            latest[name] = {
                "version": version,
                "description": (p.get("description") or "").strip(),
                "url": p.get("projectURL") or "",
                "depends": [
                    {k: d.get(k) for k in ("on", "atleast", "atmost") if d.get(k)}
                    for d in p.findall("depends")
                ],
            }
    return latest


def latest_downloads(snapshots_dir):
    """All-time .zip downloads per package from the newest snapshot, if available."""
    if not snapshots_dir or not os.path.isdir(snapshots_dir):
        return {}
    files = sorted(f for f in os.listdir(snapshots_dir)
                   if f.startswith("snapshot_") and f.endswith(".json"))
    if not files:
        return {}
    with open(os.path.join(snapshots_dir, files[-1])) as f:
        data = json.load(f)["data"]
    return {pkg: sum(sum(a.values()) for a in info["releases"].values()) for pkg, info in data.items()}


def build_graph(packages, downloads):
    names = set(packages)
    links = []
    for name, info in packages.items():
        for d in info["depends"]:
            if d["on"] in names:
                links.append({"source": name, "target": d["on"],
                              "atleast": d.get("atleast", ""), "atmost": d.get("atmost", "")})
    dependents = {n: 0 for n in names}
    for l in links:
        dependents[l["target"]] += 1
    nodes = []
    for name, info in sorted(packages.items()):
        kind = "core" if name in CORE else ("library" if dependents[name] else "leaf")
        nodes.append({
            "id": name, "kind": kind, "version": info["version"],
            "description": info["description"], "url": info["url"],
            "dependents": dependents[name], "downloads": downloads.get(name),
        })
    return {"nodes": nodes, "links": links}


def generate_html(graph, filename, sources):
    generated = datetime.now().strftime("%Y-%m-%d")
    has_downloads = any(n["downloads"] for n in graph["nodes"])
    n_nonCore = sum(1 for l in graph["links"] if l["target"] not in CORE)
    html = HTML_TEMPLATE
    for key, val in {
        "__DATA__": json.dumps(graph),
        "__CORE__": json.dumps(CORE),
        "__HAS_DOWNLOADS__": "true" if has_downloads else "false",
        "__GENERATED__": generated,
        "__N_PACKAGES__": str(len(graph["nodes"])),
        "__N_LINKS__": str(len(graph["links"])),
        "__N_NONCORE__": str(n_nonCore),
        "__NAV_CSS__": NAV_CSS,
        "__NAV__": nav_html("dependencies.html"),
    }.items():
        html = html.replace(key, val)
    os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
    with open(filename, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Dependency network written to {filename}")


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BEAST2 Package Dependencies</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js"></script>
<style>
  :root {
    color-scheme: light;
    --bg: #f5f5f4; --surface: #fcfcfb; --border: #e4e3df;
    --text: #0b0b0b; --text-2: #52514e; --muted: #8a8984;
    --link: #b9b8b2; --link-hi: #0b0b0b;
    --core: #52514e; --library: #2a78d6; --leaf: #eb6834;
    --up: #4a3aa7; --down: #008300;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --bg: #121211; --surface: #1a1a19; --border: #2e2e2c;
      --text: #ffffff; --text-2: #c3c2b7; --muted: #8a8984;
      --link: #4a4a47; --link-hi: #ffffff;
      --core: #c3c2b7; --library: #3987e5; --leaf: #d95926;
      --up: #9085e9; --down: #1baf7a;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --bg: #121211; --surface: #1a1a19; --border: #2e2e2c;
    --text: #ffffff; --text-2: #c3c2b7; --muted: #8a8984;
    --link: #4a4a47; --link-hi: #ffffff;
    --core: #c3c2b7; --library: #3987e5; --leaf: #d95926;
    --up: #9085e9; --down: #1baf7a;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: var(--bg); color: var(--text); padding: 20px; }
  h1 { font-size: 24px; margin-bottom: 6px; }
  .info { color: var(--text-2); font-size: 13px; margin-bottom: 14px; max-width: 900px; line-height: 1.5; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; margin-bottom: 12px; font-size: 13px; }
  .toolbar input[type=text] { padding: 7px 10px; width: 220px; border: 1px solid var(--border);
         border-radius: 4px; background: var(--surface); color: var(--text); font-size: 13px; }
  .toolbar label { display: flex; align-items: center; gap: 5px; color: var(--text-2); cursor: pointer; }
  .toolbar select, .toolbar button { padding: 6px 10px; border: 1px solid var(--border); border-radius: 4px;
         background: var(--surface); color: var(--text); font-size: 13px; cursor: pointer; }
  .layout { display: grid; grid-template-columns: 1fr 320px; gap: 16px; }
  @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
  .canvas { background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
         position: relative; height: 78vh; min-height: 480px; overflow: hidden; }
  svg { width: 100%; height: 100%; display: block; cursor: grab; }
  .legend { position: absolute; left: 12px; bottom: 10px; font-size: 12px; color: var(--text-2);
         display: flex; gap: 14px; background: var(--surface); padding: 4px 8px; border-radius: 4px; }
  .legend span { display: inline-flex; align-items: center; gap: 5px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
  .hint { position: absolute; right: 12px; bottom: 10px; font-size: 12px; color: var(--muted); }
  .panel { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 16px;
         font-size: 13px; height: 78vh; min-height: 480px; overflow-y: auto; }
  .panel h2 { font-size: 18px; margin-bottom: 2px; }
  .panel .ver { color: var(--muted); font-size: 12px; margin-bottom: 8px; }
  .panel p { color: var(--text-2); line-height: 1.45; margin-bottom: 10px; }
  .panel h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted);
         margin: 14px 0 6px; }
  .panel ul { list-style: none; }
  .panel li { padding: 3px 0; display: flex; justify-content: space-between; gap: 8px; }
  .panel li a { color: var(--text); text-decoration: none; cursor: pointer; border-bottom: 1px dotted var(--muted); }
  .panel .req { color: var(--muted); font-size: 12px; white-space: nowrap; }
  .panel .stat { display: flex; flex-wrap: wrap; gap: 10px 18px; margin: 6px 0 4px; }
  .panel .stat b { display: block; font-size: 18px; }
  .panel .stat span { color: var(--muted); font-size: 12px; }
  .panel a.ext { color: var(--library); }
  .empty { color: var(--muted); }
  .node circle { stroke: var(--surface); stroke-width: 2px; cursor: pointer; }
  .node text { font-size: 11px; fill: var(--text-2); pointer-events: none;
         paint-order: stroke; stroke: var(--surface); stroke-width: 3px; stroke-linejoin: round; }
  .node.core text { font-weight: 600; fill: var(--text); }
  .link { stroke: var(--link); stroke-width: 1.2px; fill: none; }
  .dim { opacity: 0.12; }
  .link.up { stroke: var(--up); stroke-width: 2px; opacity: 1; }
  .link.down { stroke: var(--down); stroke-width: 2px; opacity: 1; }
  .node.sel circle { stroke: var(--text); stroke-width: 3px; }
  .node.match circle { stroke: var(--text); stroke-width: 2.5px; }
  .tooltip { position: absolute; pointer-events: none; background: var(--text); color: var(--surface);
         font-size: 12px; padding: 5px 8px; border-radius: 4px; opacity: 0; white-space: nowrap; }
  details { margin-top: 16px; background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; }
  summary { cursor: pointer; font-size: 14px; }
  table { border-collapse: collapse; font-size: 12px; margin-top: 10px; width: 100%; }
  th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--border); vertical-align: top; }
  th { color: var(--text-2); }
__NAV_CSS__
</style>
</head>
<body>
__NAV__
<h1>BEAST2 Package Dependencies</h1>
<p class="info">
  __N_PACKAGES__ packages in CBAN (latest listed version of each), __N_LINKS__ dependencies, of which
  __N_NONCORE__ are on packages other than BEAST.base / BEAST.app. Arrows point from a package to what it depends on.
  Generated __GENERATED__ from the CBAN registry.
</p>

<div class="toolbar">
  <input type="text" id="search" placeholder="Find package...">
  <label><input type="checkbox" id="showCore"> Show links to BEAST.base / BEAST.app</label>
  <label><input type="checkbox" id="hideIsolated" checked> Hide packages with only core dependencies</label>
  <label>Node size
    <select id="sizeBy">
      <option value="dependents">Number of dependents</option>
      <option value="downloads">All-time downloads</option>
    </select>
  </label>
  <button id="downloadSvg">Download SVG</button>
</div>

<div class="layout">
  <div class="canvas" id="canvas">
    <svg id="graph"></svg>
    <div class="legend">
      <span><i class="dot" style="background:var(--core)"></i>Core</span>
      <span><i class="dot" style="background:var(--library)"></i>Depended on by others</span>
      <span><i class="dot" style="background:var(--leaf)"></i>No dependents</span>
    </div>
    <div class="hint">Click a package · drag to move · scroll to zoom</div>
    <div class="tooltip" id="tooltip"></div>
  </div>
  <div class="panel" id="panel"><p class="empty">Click a package to see what it depends on (violet) and what depends on it (green), including indirect dependencies.</p></div>
</div>

<details>
  <summary>Table view</summary>
  <table id="table"><thead><tr><th>Package</th><th>Version</th><th>Depends on</th><th>Required by</th></tr></thead><tbody></tbody></table>
</details>

<script>
const DATA = __DATA__;
const CORE = new Set(__CORE__);
const HAS_DOWNLOADS = __HAS_DOWNLOADS__;

if (!HAS_DOWNLOADS) document.querySelector('#sizeBy option[value=downloads]').disabled = true;

const byId = new Map(DATA.nodes.map(n => [n.id, n]));
const out = new Map(DATA.nodes.map(n => [n.id, []]));   // id -> links it depends on
const inc = new Map(DATA.nodes.map(n => [n.id, []]));   // id -> links depending on it
DATA.links.forEach(l => { out.get(l.source).push(l); inc.get(l.target).push(l); });

function closure(start, adj, key) {
  const seen = new Set(), stack = [start];
  while (stack.length) {
    const id = stack.pop();
    adj.get(id).forEach(l => { const nx = l[key]; if (!seen.has(nx)) { seen.add(nx); stack.push(nx); } });
  }
  return seen;
}

const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = n => n == null ? '–' : n.toLocaleString('en-US');
const req = l => [l.atleast && '≥ ' + l.atleast, l.atmost && '≤ ' + l.atmost].filter(Boolean).join(', ');

// ---------- table view ----------
document.querySelector('#table tbody').innerHTML = DATA.nodes.map(n =>
  `<tr><td>${esc(n.id)}</td><td>${esc(n.version)}</td>
   <td>${out.get(n.id).map(l => esc(l.target)).join(', ')}</td>
   <td>${inc.get(n.id).map(l => esc(l.source)).join(', ')}</td></tr>`).join('');

// ---------- graph ----------
const svg = d3.select('#graph');
const root = svg.append('g');
const defs = svg.append('defs');
[['arrow', 'var(--link)'], ['arrow-up', 'var(--up)'], ['arrow-down', 'var(--down)']].forEach(([id, col]) =>
  defs.append('marker').attr('id', id).attr('viewBox', '0 -4 8 8').attr('refX', 8).attr('refY', 0)
    .attr('markerWidth', 7).attr('markerHeight', 7).attr('orient', 'auto')
    .append('path').attr('d', 'M0,-4L8,0L0,4').style('fill', col));

const zoom = d3.zoom().scaleExtent([0.2, 5]).on('zoom', e => root.attr('transform', e.transform));
svg.call(zoom);

function fitView() {
  const ns = nodeSel.data();
  if (!ns.length) return;
  const { width, height } = document.getElementById('canvas').getBoundingClientRect();
  const x0 = d3.min(ns, d => d.x) - 30, x1 = d3.max(ns, d => d.x) + 120;   // room for labels
  const y0 = d3.min(ns, d => d.y) - 30, y1 = d3.max(ns, d => d.y) + 40;   // and the legend
  const k = Math.min(1.6, 0.95 * Math.min(width / (x1 - x0), height / (y1 - y0)));
  svg.transition().duration(500).call(zoom.transform,
    d3.zoomIdentity.translate(width / 2, height / 2).scale(k).translate(-(x0 + x1) / 2, -(y0 + y1) / 2));
}

const linkG = root.append('g'), nodeG = root.append('g');
let sim, nodeSel, linkSel, visLinks = [], selected = null;

function radius(n) {
  const mode = document.getElementById('sizeBy').value;
  if (mode === 'downloads' && HAS_DOWNLOADS) return 4 + Math.sqrt((n.downloads || 0) / 40);
  return CORE.has(n.id) ? 16 : 5 + 2.2 * Math.sqrt(n.dependents);
}

function render() {
  const showCore = document.getElementById('showCore').checked;
  const hideIso = document.getElementById('hideIsolated').checked;

  visLinks = DATA.links.filter(l => showCore || !CORE.has(l.target));
  const linked = new Set();
  visLinks.forEach(l => { linked.add(l.source); linked.add(l.target); });
  const visNodes = DATA.nodes.filter(n =>
    (showCore || !CORE.has(n.id) || linked.has(n.id)) && (!hideIso || linked.has(n.id) || (showCore && CORE.has(n.id))));
  const visIds = new Set(visNodes.map(n => n.id));
  visLinks = visLinks.filter(l => visIds.has(l.source) && visIds.has(l.target))
                     .map(l => ({...l, s: l.source, t: l.target}));

  const { width, height } = document.getElementById('canvas').getBoundingClientRect();
  if (sim) sim.stop();
  sim = d3.forceSimulation(visNodes)
    .force('link', d3.forceLink(visLinks).id(d => d.id)
           .distance(l => CORE.has(l.t) ? 160 : 90).strength(l => CORE.has(l.t) ? 0.05 : 0.4))
    .force('charge', d3.forceManyBody().strength(-380))
    .force('collide', d3.forceCollide(d => radius(d) + 22))
    .force('x', d3.forceX(width / 2).strength(0.04))
    .force('y', d3.forceY(height / 2).strength(0.06));

  linkSel = linkG.selectAll('path').data(visLinks, l => l.s + '>' + l.t)
    .join('path').attr('class', 'link').attr('marker-end', 'url(#arrow)');

  nodeSel = nodeG.selectAll('g.node').data(visNodes, d => d.id)
    .join(enter => {
      const g = enter.append('g');
      g.append('circle');
      g.append('text').attr('dy', '0.35em');
      return g;
    })
    .attr('class', d => 'node ' + d.kind)
    .on('click', (e, d) => { e.stopPropagation(); select(d.id); })
    .on('mousemove', (e, d) => {
      const r = document.getElementById('canvas').getBoundingClientRect();
      const tip = document.getElementById('tooltip');
      tip.innerHTML = `<b>${esc(d.id)}</b> ${esc(d.version)} · ${d.dependents} dependents` +
        (d.downloads != null ? ` · ${fmt(d.downloads)} downloads` : '');
      tip.style.left = (e.clientX - r.left + 12) + 'px';
      tip.style.top = (e.clientY - r.top + 12) + 'px';
      tip.style.opacity = 1;
    })
    .on('mouseleave', () => document.getElementById('tooltip').style.opacity = 0)
    .call(d3.drag()
      .on('start', (e, d) => { if (!e.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
      .on('drag', (e, d) => { d.fx = e.x; d.fy = e.y; })
      .on('end', (e, d) => { if (!e.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }));

  nodeSel.select('circle').attr('r', radius).style('fill', d => `var(--${d.kind})`);
  nodeSel.select('text').text(d => d.id).attr('x', d => radius(d) + 4);

  sim.on('end', fitView);
  sim.on('tick', () => {
    linkSel.attr('d', l => {
      const s = l.source, t = l.target, dx = t.x - s.x, dy = t.y - s.y, dist = Math.hypot(dx, dy) || 1;
      const rt = radius(t) + 3;
      return `M${s.x},${s.y}L${t.x - dx / dist * rt},${t.y - dy / dist * rt}`;
    });
    nodeSel.attr('transform', d => `translate(${d.x},${d.y})`);
  });

  if (selected && !visIds.has(selected)) selected = null;
  highlight();
}

function highlight() {
  if (!selected) {
    nodeSel.classed('dim', false).classed('sel', false);
    linkSel.classed('dim', false).classed('up', false).classed('down', false).attr('marker-end', 'url(#arrow)');
    return;
  }
  const up = closure(selected, out, 'target');      // everything it needs
  const down = closure(selected, inc, 'source');    // everything that needs it
  const keep = new Set([selected, ...up, ...down]);
  nodeSel.classed('dim', d => !keep.has(d.id)).classed('sel', d => d.id === selected);
  linkSel
    .classed('up', l => (l.s === selected || up.has(l.s)) && up.has(l.t))
    .classed('down', l => (l.t === selected || down.has(l.t)) && down.has(l.s))
    .classed('dim', l => !(((l.s === selected || up.has(l.s)) && up.has(l.t)) ||
                           ((l.t === selected || down.has(l.t)) && down.has(l.s))))
    .attr('marker-end', function () {
      return this.classList.contains('up') ? 'url(#arrow-up)'
           : this.classList.contains('down') ? 'url(#arrow-down)' : 'url(#arrow)';
    });
}

function select(id) {
  selected = id;
  highlight();
  const n = byId.get(id);
  const up = closure(id, out, 'target'), down = closure(id, inc, 'source');
  const direct = out.get(id), directIn = inc.get(id);
  const indirectUp = [...up].filter(x => !direct.some(l => l.target === x)).sort();
  const indirectDown = [...down].filter(x => !directIn.some(l => l.source === x)).sort();
  const item = (name, extra) => `<li><a data-id="${esc(name)}">${esc(name)}</a><span class="req">${esc(extra || '')}</span></li>`;
  const list = (arr) => arr.length ? `<ul>${arr.join('')}</ul>` : '<p class="empty">None</p>';
  document.getElementById('panel').innerHTML = `
    <h2>${esc(n.id)}</h2><div class="ver">version ${esc(n.version)}</div>
    ${n.description ? `<p>${esc(n.description)}</p>` : ''}
    ${n.url ? `<p><a class="ext" href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.url.replace(/^https?:\/\//, ''))}</a></p>` : ''}
    <div class="stat">
      <div><b>${direct.length}</b><span>depends on</span></div>
      <div><b>${directIn.length}</b><span>direct dependents</span></div>
      <div><b>${down.size}</b><span>all dependents</span></div>
      ${n.downloads != null ? `<div><b>${fmt(n.downloads)}</b><span>downloads</span></div>` : ''}
    </div>
    <h3>Depends on</h3>${list(direct.map(l => item(l.target, req(l))))}
    ${indirectUp.length ? `<h3>Indirectly depends on</h3>${list(indirectUp.map(x => item(x)))}` : ''}
    <h3>Required by</h3>${list(directIn.slice().sort((a, b) => a.source.localeCompare(b.source)).map(l => item(l.source, req(l))))}
    ${indirectDown.length ? `<h3>Indirectly required by</h3>${list(indirectDown.map(x => item(x)))}` : ''}`;
  document.querySelectorAll('#panel a[data-id]').forEach(a => a.onclick = () => {
    const target = a.dataset.id;
    if (!nodeSel.data().some(d => d.id === target)) {   // make hidden nodes visible
      document.getElementById('hideIsolated').checked = false;
      if (CORE.has(target)) document.getElementById('showCore').checked = true;
      render();
    }
    select(target);
  });
}

svg.on('click', () => {
  selected = null; highlight();
  document.getElementById('panel').innerHTML = '<p class="empty">Click a package to see what it depends on (violet) and what depends on it (green), including indirect dependencies.</p>';
});

document.getElementById('showCore').onchange = render;
document.getElementById('hideIsolated').onchange = render;
document.getElementById('sizeBy').onchange = () => {
  nodeSel.select('circle').attr('r', radius);
  nodeSel.select('text').attr('x', d => radius(d) + 4);
  sim.force('collide', d3.forceCollide(d => radius(d) + 22)).alpha(0.3).restart();
};
document.getElementById('search').oninput = e => {
  const q = e.target.value.trim().toLowerCase();
  nodeSel.classed('match', d => q && d.id.toLowerCase().includes(q));
  const exact = nodeSel.data().find(d => d.id.toLowerCase() === q);
  if (exact) select(exact.id);
};

document.getElementById('downloadSvg').onclick = () => {
  const node = document.getElementById('graph');
  const clone = node.cloneNode(true);
  const { width, height } = node.getBoundingClientRect();
  clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  clone.setAttribute('width', width); clone.setAttribute('height', height);
  // inline resolved colours so the file stands alone
  const cs = getComputedStyle(document.documentElement);
  const vars = ['--surface', '--text', '--text-2', '--link', '--core', '--library', '--leaf', '--up', '--down'];
  let css = '', vals = vars.map(v => `${v}:${cs.getPropertyValue(v)}`).join(';');
  for (const sheet of document.styleSheets) for (const r of sheet.cssRules)
    if (/^\.(node|link|dim)/.test(r.selectorText || '')) css += r.cssText + '\n';
  const style = document.createElementNS('http://www.w3.org/2000/svg', 'style');
  style.textContent = `svg{${vals};font-family:-apple-system,Segoe UI,Roboto,sans-serif;background:${cs.getPropertyValue('--surface')}}\n${css}`;
  clone.insertBefore(style, clone.firstChild);
  const blob = new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'beast2_dependencies.svg'; a.click();
};

render();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Build an interactive BEAST2 package dependency network")
    parser.add_argument("--xml", nargs="+", default=CBAN_URLS,
                        help="CBAN package XML files or URLs (default: the 2.7 registry)")
    parser.add_argument("--snapshots-dir", default="beast2_snapshots",
                        help="Download-stats snapshots, used to size nodes by downloads (optional)")
    parser.add_argument("--output", default="beast2_dependencies.html", help="Output HTML file")
    args = parser.parse_args()

    packages = parse_packages(args.xml)
    graph = build_graph(packages, latest_downloads(args.snapshots_dir))
    print(f"{len(graph['nodes'])} packages, {len(graph['links'])} dependencies")
    generate_html(graph, args.output, args.xml)


if __name__ == "__main__":
    main()
