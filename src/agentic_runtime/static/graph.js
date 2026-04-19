import * as d3 from "https://cdn.jsdelivr.net/npm/d3@7.9.0/+esm";
import { dagStratify, sugiyama, layeringSimplex, decrossOpt, decrossTwoLayer, coordCenter }
    from "https://cdn.jsdelivr.net/npm/d3-dag@0.11.3/+esm";

var dataEl = document.getElementById("graph-data");
var wrap   = document.getElementById("cy");
if (!dataEl || !wrap) throw new Error("graph: no graph-data or cy element");

var graph;
try {
  graph = JSON.parse(dataEl.textContent || '{"nodes":[],"edges":[]}');
} catch (_) { throw new Error("graph: invalid JSON in graph-data"); }

if (!graph.nodes || !graph.nodes.length) {
  wrap.innerHTML = '<p class="graph-empty">No project tasks yet — submit a goal and let the planner build the DAG.</p>';
} else {
  renderGraph(graph);
}

function renderGraph(graph) {
  // ── Node dimensions & spacing ─────────────────────────────────────────────
  var NW   = 224;   // node card width
  var NH   = 76;    // node card height
  var HGAP = 100;   // horizontal gap between layers
  var VGAP = 52;    // vertical gap within a layer
  var PAD  = 44;    // canvas outer padding

  // ── Build parent map (edge: source → target means target depends on source)
  var validIds = {};
  graph.nodes.forEach(function (n) { validIds[n.task_id] = true; });

  var parentMap    = {};
  var edgeLabelMap = {};
  graph.nodes.forEach(function (n) { parentMap[n.task_id] = []; });
  graph.edges.forEach(function (e) {
    if (!validIds[e.source] || !validIds[e.target]) return;
    if (!parentMap[e.target]) parentMap[e.target] = [];
    if (parentMap[e.target].indexOf(e.source) === -1) {
      parentMap[e.target].push(e.source);
    }
    edgeLabelMap[e.source + "__" + e.target] =
      e.artifacts.map(function (a) { return a.artifact_key; }).join(", ");
  });

  var stratData = graph.nodes.map(function (n) {
    return {
      id:        n.task_id,
      parentIds: parentMap[n.task_id] || [],
      taskId:    n.task_id,
      title:     n.title     || "",
      state:     n.state     || "created",
      capName:   n.capability_name || "",
    };
  });

  // ── d3-dag: Sugiyama layout ───────────────────────────────────────────────
  var dag;
  try {
    dag = dagStratify()(stratData);
  } catch (err) {
    wrap.innerHTML = '<p class="graph-empty">DAG build error: ' + (err.message || err) + '</p>';
    return;
  }

  function runLayout(decross) {
    return sugiyama()
      .layering(layeringSimplex())
      .decross(decross)
      .coord(coordCenter())
      .nodeSize(() => [NW + HGAP, NH + VGAP])(dag);
  }

  var dims;
  try {
    dims = runLayout(decrossOpt());
  } catch (_) {
    try {
      dims = runLayout(decrossTwoLayer());
    } catch (err2) {
      wrap.innerHTML = '<p class="graph-empty">Layout failed: ' + (err2.message || err2) + '</p>';
      return;
    }
  }

  var lw = dims.width;
  var lh = dims.height;
  var totalW = lw + 2 * PAD;
  var totalH = lh + 2 * PAD;

  // ── SVG setup ─────────────────────────────────────────────────────────────
  var cW = wrap.clientWidth  || 900;
  var cH = wrap.clientHeight || 520;

  var svg = d3.select(wrap)
    .append("svg")
    .attr("width",  "100%")
    .attr("height", "100%")
    .attr("viewBox", "0 0 " + cW + " " + cH)
    .style("display", "block");

  // ── Defs: arrow marker + glow filter ─────────────────────────────────────
  var defs = svg.append("defs");

  defs.append("marker")
    .attr("id",           "dag-arrow")
    .attr("viewBox",      "0 0 8 8")
    .attr("refX",         7)
    .attr("refY",         4)
    .attr("markerWidth",  5)
    .attr("markerHeight", 5)
    .attr("orient",       "auto")
    .append("path")
      .attr("d",    "M 0 0 L 8 4 L 0 8 Z")
      .attr("fill", "rgba(155,176,209,0.55)");

  var glowF = defs.append("filter").attr("id", "dag-glow").attr("x", "-30%").attr("y", "-30%").attr("width", "160%").attr("height", "160%");
  glowF.append("feGaussianBlur").attr("in", "SourceAlpha").attr("stdDeviation", "5").attr("result", "blur");
  var fm = glowF.append("feMerge");
  fm.append("feMergeNode").attr("in", "blur");
  fm.append("feMergeNode").attr("in", "SourceGraphic");

  // ── Zoom behaviour ────────────────────────────────────────────────────────
  var rootG = svg.append("g").attr("class", "dag-root");

  var zoom = d3.zoom()
    .scaleExtent([0.1, 4])
    .on("zoom", function (ev) { rootG.attr("transform", ev.transform); });

  svg.call(zoom);

  // Initial: scale + center graph inside the container
  var initScale = Math.min((cW - 40) / totalW, (cH - 40) / totalH, 1);
  var initTx    = (cW - totalW * initScale) / 2;
  var initTy    = (cH - totalH * initScale) / 2;
  svg.call(zoom.transform, d3.zoomIdentity.translate(initTx, initTy).scale(initScale));

  // ── State colour palette ──────────────────────────────────────────────────
  var COLORS = {
    done:     { bg: "#071e18", stroke: "#45d0b8", text: "#9ff5e4", glow: false },
    running:  { bg: "#0c1a36", stroke: "#7c9cff", text: "#c9d6ff", glow: true  },
    claimed:  { bg: "#0a1530", stroke: "#5a7adf", text: "#b8c8f8", glow: false },
    queued:   { bg: "#0a1228", stroke: "#3d5496", text: "#9bb0d1", glow: false },
    created:  { bg: "#090f1e", stroke: "#2e4060", text: "#7a92b4", glow: false },
    failed:   { bg: "#260812", stroke: "#ff5c7b", text: "#ffc4cf", glow: false },
    cancelled:{ bg: "#1c0810", stroke: "#c03a5a", text: "#f09ab0", glow: false },
    blocked:  { bg: "#1e1500", stroke: "#ffc45c", text: "#ffe5b8", glow: false },
    paused_manual:                    { bg: "#191200", stroke: "#d4a040", text: "#f0d090", glow: false },
    paused_pending_change_review:     { bg: "#191200", stroke: "#d4a040", text: "#f0d090", glow: false },
  };
  var DC = { bg: "#090f1e", stroke: "#2e4060", text: "#7a92b4", glow: false };

  function col(state) { return COLORS[state] || DC; }

  // ── Helper: truncate ──────────────────────────────────────────────────────
  function trunc(s, max) {
    return s.length > max ? s.slice(0, max - 1) + "\u2026" : s;
  }

  // ── Draw edges ────────────────────────────────────────────────────────────
  var edgesG = rootG.append("g").attr("class", "dag-edges");

  var nodes = Array.from(dag.descendants ? dag.descendants() : dag.nodes());
  var links = Array.from(dag.links ? dag.links() : []);

  links.forEach(function (link) {
    var sx = link.source.x + PAD;
    var sy = link.source.y + PAD + NH / 2;   // exit from bottom of source
    var tx = link.target.x + PAD;
    var ty = link.target.y + PAD - NH / 2;   // enter at top of target

    // Cubic S-curve
    var mY = (sy + ty) / 2;
    var pathD =
      "M " + sx + " " + sy +
      " C " + sx + " " + mY +
      ", "  + tx + " " + mY +
      ", "  + tx + " " + ty;

    edgesG.append("path")
      .attr("d",           pathD)
      .attr("fill",        "none")
      .attr("stroke",      "rgba(155,176,209,0.30)")
      .attr("stroke-width", 1.6)
      .attr("marker-end",  "url(#dag-arrow)");

    // Artifact label
    var key = link.source.data.taskId + "__" + link.target.data.taskId;
    var lbl = edgeLabelMap[key] || "";
    if (lbl) {
      var ldisp  = trunc(lbl, 34);
      var lx     = (sx + tx) / 2;
      var ly     = mY;
      var lw2    = ldisp.length * 5.4 + 10;
      var lblGrp = edgesG.append("g");
      lblGrp.append("rect")
        .attr("x",      lx - lw2 / 2).attr("y",      ly - 11)
        .attr("width",  lw2)          .attr("height", 13)
        .attr("rx",     4)
        .attr("fill",   "rgba(9,17,31,0.80)")
        .attr("stroke", "rgba(155,176,209,0.15)")
        .attr("stroke-width", 0.5);
      lblGrp.append("text")
        .attr("x",                  lx).attr("y", ly - 4)
        .attr("text-anchor",        "middle")
        .attr("dominant-baseline",  "middle")
        .attr("fill",               "rgba(155,176,209,0.70)")
        .attr("font-size",          "9px")
        .attr("font-family",        "Inter, ui-sans-serif, sans-serif")
        .text(ldisp);
    }
  });

  // ── Draw nodes ────────────────────────────────────────────────────────────
  var nodesG = rootG.append("g").attr("class", "dag-nodes");

  nodes.forEach(function (node) {
    var cx = node.x + PAD;
    var cy = node.y + PAD;
    var rx = cx - NW / 2;
    var ry = cy - NH / 2;
    var c  = col(node.data.state);

    var ng = nodesG.append("a")
      .attr("href",  "/tasks/" + node.data.taskId)
      .attr("class", "dag-node")
      .style("cursor", "pointer");

    // Outer glow ring for running tasks
    if (c.glow) {
      ng.append("rect")
        .attr("x", rx - 5).attr("y", ry - 5)
        .attr("width", NW + 10).attr("height", NH + 10)
        .attr("rx", 20)
        .attr("fill",   "none")
        .attr("stroke", c.stroke)
        .attr("stroke-width", 2)
        .attr("opacity", 0.30)
        .attr("filter",  "url(#dag-glow)");
    }

    // Drop-shadow rect (offset slightly below/right)
    ng.append("rect")
      .attr("x", rx + 3).attr("y", ry + 5)
      .attr("width", NW).attr("height", NH)
      .attr("rx", 14)
      .attr("fill", "rgba(0,0,0,0.40)");

    // Card background
    ng.append("rect")
      .attr("x", rx).attr("y", ry)
      .attr("width", NW).attr("height", NH)
      .attr("rx", 14)
      .attr("fill",         c.bg)
      .attr("stroke",       c.stroke)
      .attr("stroke-width", 1.6);

    // Left accent bar
    ng.append("rect")
      .attr("x", rx + 1).attr("y", ry + 13)
      .attr("width",  3)
      .attr("height", NH - 26)
      .attr("rx",     1.5)
      .attr("fill",    c.stroke)
      .attr("opacity", 0.90);

    // Title
    ng.append("text")
      .attr("x",           rx + 16)
      .attr("y",           ry + 30)
      .attr("fill",        c.text)
      .attr("font-size",   "12.5px")
      .attr("font-weight", "600")
      .attr("font-family", "Inter, ui-sans-serif, sans-serif")
      .text(trunc(node.data.title, 26));

    // Subtitle: capability · state
    var cap = node.data.capName;
    var sub = cap ? (cap + "  \u00B7  " + node.data.state) : node.data.state;
    ng.append("text")
      .attr("x",           rx + 16)
      .attr("y",           ry + 52)
      .attr("fill",        "rgba(155,176,209,0.55)")
      .attr("font-size",   "10px")
      .attr("font-family", "Inter, ui-sans-serif, sans-serif")
      .text(trunc(sub, 30));

    // State indicator dot (top-right corner)
    ng.append("circle")
      .attr("cx",      rx + NW - 15)
      .attr("cy",      ry + 15)
      .attr("r",       4.5)
      .attr("fill",    c.stroke)
      .attr("opacity", 0.90);
  });
}
