(function () {
  "use strict";

  const dataEl = document.getElementById("graph-data");
  const container = document.getElementById("cy");
  if (!dataEl || !container) return;

  const graph = JSON.parse(dataEl.textContent || '{"nodes":[],"edges":[]}');

  if (!graph.nodes.length) {
    container.innerHTML =
      '<p class="graph-empty">No project tasks yet — submit a goal and let the planner build the DAG.</p>';
    return;
  }

  // ── Elements ──────────────────────────────────────────────────────────────

  const elements = [];

  graph.nodes.forEach(function (n) {
    var title = n.title && n.title.length > 30 ? n.title.slice(0, 29) + "\u2026" : (n.title || "");
    var cap   = n.capability_name || "";
    elements.push({
      data: {
        id:         n.task_id,
        label:      title,
        sublabel:   cap ? cap + " \u00B7 " + n.state : n.state,
        state:      n.state,
        href:       "/tasks/" + n.task_id,
      },
    });
  });

  var seenEdges = {};
  graph.edges.forEach(function (e) {
    var key = e.source + "__" + e.target;
    if (seenEdges[key]) return;
    seenEdges[key] = true;
    var label = e.artifacts.map(function (a) { return a.artifact_key; }).join("\n");
    elements.push({
      data: { id: key, source: e.source, target: e.target, label: label },
    });
  });

  // ── Cytoscape ─────────────────────────────────────────────────────────────

  var cy = cytoscape({
    container: container,
    elements:  elements,

    layout: {
      name:            "breadthfirst",
      directed:        true,
      padding:         44,
      spacingFactor:   1.55,
      avoidOverlap:    true,
      nodeDimensionsIncludeLabels: true,
      // lay left to right
      transform: function (node, pos) { return pos; },
    },

    style: [
      /* ── Default node ───────────────────────────────────────────────── */
      {
        selector: "node",
        style: {
          "shape":             "round-rectangle",
          "width":             210,
          "height":            72,
          "background-color":  "rgba(13,27,46,0.92)",
          "border-color":      "rgba(148,163,184,0.35)",
          "border-width":      1.5,
          "label":             function (ele) {
            return ele.data("label") + "\n" + ele.data("sublabel");
          },
          "text-wrap":         "wrap",
          "text-max-width":    190,
          "text-valign":       "center",
          "text-halign":       "center",
          "font-family":       "Inter, ui-sans-serif, system-ui, sans-serif",
          "font-size":         12,
          "line-height":       1.55,
          "color":             "#9bb0d1",
        },
      },

      /* ── State overrides ─────────────────────────────────────────────── */
      {
        selector: "node[state = 'done']",
        style: {
          "background-color": "rgba(69,208,184,0.13)",
          "border-color":     "rgba(69,208,184,0.75)",
          "border-width":     2,
          "color":            "#9ff5e4",
        },
      },
      {
        selector: "node[state = 'running']",
        style: {
          "background-color": "rgba(124,156,255,0.17)",
          "border-color":     "rgba(124,156,255,0.85)",
          "border-width":     2,
          "color":            "#c9d6ff",
        },
      },
      {
        selector: "node[state = 'claimed'], node[state = 'queued']",
        style: {
          "background-color": "rgba(124,156,255,0.10)",
          "border-color":     "rgba(124,156,255,0.50)",
          "border-width":     1.5,
          "color":            "#c9d6ff",
        },
      },
      {
        selector: "node[state = 'failed'], node[state = 'cancelled']",
        style: {
          "background-color": "rgba(255,92,123,0.14)",
          "border-color":     "rgba(255,92,123,0.7)",
          "border-width":     2,
          "color":            "#ffc4cf",
        },
      },
      {
        selector: "node[state = 'blocked'], node[state = 'paused_manual'], node[state = 'paused_pending_change_review']",
        style: {
          "background-color": "rgba(255,196,92,0.12)",
          "border-color":     "rgba(255,196,92,0.65)",
          "border-width":     1.5,
          "color":            "#ffe5b8",
        },
      },

      /* ── Selected / hover ────────────────────────────────────────────── */
      {
        selector: "node:selected",
        style: {
          "border-width":     2.5,
          "border-opacity":   1,
          "shadow-blur":      14,
          "shadow-color":     "#7c9cff",
          "shadow-opacity":   0.45,
          "shadow-offset-x":  0,
          "shadow-offset-y":  0,
        },
      },
      {
        selector: "node.hovered",
        style: {
          "border-width":     2,
          "border-opacity":   0.95,
        },
      },

      /* ── Edges ───────────────────────────────────────────────────────── */
      {
        selector: "edge",
        style: {
          "width":                  1.5,
          "line-color":             "rgba(155,176,209,0.30)",
          "target-arrow-color":     "rgba(155,176,209,0.55)",
          "target-arrow-shape":     "triangle",
          "curve-style":            "bezier",
          "arrow-scale":            1.15,
          "label":                  "data(label)",
          "font-size":              9,
          "text-wrap":              "wrap",
          "color":                  "rgba(155,176,209,0.65)",
          "font-family":            "Inter, ui-sans-serif, system-ui, sans-serif",
          "text-rotation":          "autorotate",
          "text-background-color":  "#09111f",
          "text-background-opacity": 0.72,
          "text-background-padding": 2,
          "text-background-shape":  "round-rectangle",
        },
      },
      {
        selector: "edge:selected",
        style: {
          "line-color":         "rgba(124,156,255,0.65)",
          "target-arrow-color": "rgba(124,156,255,0.8)",
          "width":              2.5,
        },
      },
    ],

    userZoomingEnabled:  true,
    userPanningEnabled:  true,
    boxSelectionEnabled: false,
    minZoom: 0.2,
    maxZoom: 3,
  });

  // ── Interactions ──────────────────────────────────────────────────────────

  cy.on("tap", "node", function (evt) {
    window.location.href = evt.target.data("href");
  });

  cy.on("mouseover", "node", function (evt) {
    container.style.cursor = "pointer";
    evt.target.addClass("hovered");
  });
  cy.on("mouseout", "node", function (evt) {
    container.style.cursor = "default";
    evt.target.removeClass("hovered");
  });

  // Fit graph on first render once layout settles
  cy.one("layoutstop", function () {
    cy.fit(undefined, 32);
  });
})();
