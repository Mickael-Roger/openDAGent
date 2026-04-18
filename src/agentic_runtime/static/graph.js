(function () {
  const dataElement = document.getElementById("graph-data");
  const svg = document.getElementById("task-graph");
  if (!dataElement || !svg) {
    return;
  }

  const graph = JSON.parse(dataElement.textContent || "{\"nodes\":[],\"edges\":[]}");
  const width = Math.max(svg.clientWidth || 900, 900);
  const nodeWidth = 220;
  const nodeHeight = 74;
  const horizontalGap = 110;
  const verticalGap = 34;

  const levels = computeLevels(graph.nodes, graph.edges);
  const columns = new Map();
  graph.nodes.forEach((node) => {
    const level = levels.get(node.task_id) || 0;
    if (!columns.has(level)) {
      columns.set(level, []);
    }
    columns.get(level).push(node);
  });

  const positions = new Map();
  const sortedLevels = [...columns.keys()].sort((a, b) => a - b);
  let totalHeight = 0;

  sortedLevels.forEach((level) => {
    const column = columns.get(level) || [];
    column.forEach((node, index) => {
      const x = 40 + level * (nodeWidth + horizontalGap);
      const y = 40 + index * (nodeHeight + verticalGap);
      positions.set(node.task_id, { x, y });
      totalHeight = Math.max(totalHeight, y + nodeHeight + 40);
    });
  });

  svg.setAttribute("viewBox", `0 0 ${width} ${Math.max(totalHeight, 420)}`);
  svg.innerHTML = "";

  graph.edges.forEach((edge) => {
    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    if (!source || !target) {
      return;
    }

    const startX = source.x + nodeWidth;
    const startY = source.y + nodeHeight / 2;
    const endX = target.x;
    const endY = target.y + nodeHeight / 2;
    const curve = `M ${startX} ${startY} C ${startX + 46} ${startY}, ${endX - 46} ${endY}, ${endX} ${endY}`;

    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("d", curve);
    path.setAttribute("class", "graph-edge");
    svg.appendChild(path);

    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("class", "graph-label");
    label.setAttribute("x", String((startX + endX) / 2));
    label.setAttribute("y", String((startY + endY) / 2 - 8));
    label.textContent = edge.artifacts.map((artifact) => artifact.artifact_key).join(", ");
    svg.appendChild(label);
  });

  graph.nodes.forEach((node) => {
    const position = positions.get(node.task_id);
    if (!position) {
      return;
    }

    const group = document.createElementNS("http://www.w3.org/2000/svg", "a");
    group.setAttribute("href", `/tasks/${node.task_id}`);
    group.setAttribute("class", "graph-node");

    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(position.x));
    rect.setAttribute("y", String(position.y));
    rect.setAttribute("width", String(nodeWidth));
    rect.setAttribute("height", String(nodeHeight));
    rect.setAttribute("rx", "18");
    group.appendChild(rect);

    const title = document.createElementNS("http://www.w3.org/2000/svg", "text");
    title.setAttribute("x", String(position.x + 16));
    title.setAttribute("y", String(position.y + 28));
    title.textContent = truncate(node.title, 26);
    group.appendChild(title);

    const meta = document.createElementNS("http://www.w3.org/2000/svg", "text");
    meta.setAttribute("x", String(position.x + 16));
    meta.setAttribute("y", String(position.y + 48));
    meta.setAttribute("class", "graph-label");
    meta.textContent = `${node.state} · ${node.goal_title}`;
    group.appendChild(meta);

    svg.appendChild(group);
  });

  function computeLevels(nodes, edges) {
    const incoming = new Map();
    const adjacency = new Map();
    const levels = new Map();

    nodes.forEach((node) => {
      incoming.set(node.task_id, 0);
      adjacency.set(node.task_id, []);
    });

    edges.forEach((edge) => {
      adjacency.get(edge.source)?.push(edge.target);
      incoming.set(edge.target, (incoming.get(edge.target) || 0) + 1);
    });

    const queue = nodes.filter((node) => (incoming.get(node.task_id) || 0) === 0).map((node) => node.task_id);
    while (queue.length > 0) {
      const nodeId = queue.shift();
      const nextLevel = levels.get(nodeId) || 0;
      (adjacency.get(nodeId) || []).forEach((neighbor) => {
        levels.set(neighbor, Math.max(levels.get(neighbor) || 0, nextLevel + 1));
        incoming.set(neighbor, (incoming.get(neighbor) || 0) - 1);
        if ((incoming.get(neighbor) || 0) === 0) {
          queue.push(neighbor);
        }
      });
    }

    nodes.forEach((node) => {
      if (!levels.has(node.task_id)) {
        levels.set(node.task_id, 0);
      }
    });

    return levels;
  }

  function truncate(value, maxLength) {
    if (value.length <= maxLength) {
      return value;
    }
    return `${value.slice(0, maxLength - 1)}…`;
  }
})();
