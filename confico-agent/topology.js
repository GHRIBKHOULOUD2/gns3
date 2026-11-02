/*
 * topology.js - draws the lab schema inside the empty square of any page.
 *
 *   <div id="schema" style="width:100%;height:100%"></div>
 *   <script src="topology.js"></script>
 *   drawTopology(document.getElementById("schema"), topology, options);
 *
 * topology = the "topology" object returned by /api/topology/import or /live
 *            { nodes: [{name, type}], links: ["A:eth0 <-> B:eth1", ...] }
 *
 * options (all optional):
 *   hostnames : { "frrouting-frr-1": "R1" }                 shown under each icon (Identity page)
 *   labels    : { "frrouting-frr-1|eth0": "192.168.10.1/24" } shown at that cable end (IP page)
 *   onSelect  : function (deviceName) {...}                 called when an icon is clicked
 *
 * Call it again after every change (new hostname, new IP) to refresh the drawing.
 */
(function () {
  const NS = "http://www.w3.org/2000/svg";
  const COLORS = { router: "#2b8a8a", switch: "#5b7fa6", pc: "#8a98a8" };
  const LETTER = { router: "R", switch: "S", pc: "PC" };

  const kind = n =>
    n.type === "vpcs" || /^pc/i.test(n.name) ? "pc" : /ovs|switch/i.test(n.name) ? "switch" : "router";

  function add(parent, tag, attrs, text) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text !== undefined) e.textContent = text;     // textContent: device names can never inject HTML
    parent.appendChild(e);
    return e;
  }

  const split = end => {
    const i = end.indexOf(":");
    return [end.slice(0, i), end.slice(i + 1)];
  };

  window.drawTopology = function (container, topo, opts = {}) {
    const W = 400, H = 350, cx = W / 2, cy = H / 2, R = 125;
    container.innerHTML = "";
    const svg = add(container, "svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: "100%" });
    const nodes = topo.nodes || [];

    // Layout: one switch in the middle (star) with everything else on a circle; otherwise all on a circle.
    const switches = nodes.filter(n => kind(n) === "switch");
    const hub = switches.length === 1 && nodes.length > 2 ? switches[0] : null;
    const ring = nodes.filter(n => n !== hub);
    const pos = {};
    if (hub) pos[hub.name] = [cx, cy];
    ring.forEach((n, i) => {
      const a = (2 * Math.PI * i) / ring.length - Math.PI / 2;
      pos[n.name] = [cx + R * Math.cos(a), cy + R * Math.sin(a)];
    });

    // Cables + interface labels
    (topo.links || []).forEach(link => {
      const ends = link.split(" <-> ").map(split);
      if (ends.length !== 2 || !pos[ends[0][0]] || !pos[ends[1][0]]) return;
      const [[ax, ay], [bx, by]] = [pos[ends[0][0]], pos[ends[1][0]]];
      add(svg, "line", { x1: ax, y1: ay, x2: bx, y2: by, stroke: "#6b97c2", "stroke-width": 2 });
      const len = Math.hypot(bx - ax, by - ay) || 1;
      const nx = -(by - ay) / len * 9, ny = (bx - ax) / len * 9;    // push the label off the line
      [[ends[0], ax, ay, bx, by], [ends[1], bx, by, ax, ay]].forEach(([[dev, iface], x, y, ox, oy]) => {
        const t = 0.32;
        const t_ = add(svg, "text", { x: x + (ox - x) * t + nx, y: y + (oy - y) * t + ny, "font-size": 9, "text-anchor": "middle", fill: "#555" });
        add(t_, "tspan", { x: x + (ox - x) * t + nx }, iface);
        const extra = (opts.labels || {})[dev + "|" + iface];
        if (extra) add(t_, "tspan", { x: x + (ox - x) * t + nx, dy: 10, fill: "#2b6ca3", "font-weight": "bold" }, extra);
      });
    });

    // Device icons
    nodes.forEach(n => {
      const [x, y] = pos[n.name], k = kind(n);
      const g = add(svg, "g", { style: opts.onSelect ? "cursor:pointer" : "" });
      add(g, "circle", { cx: x, cy: y, r: 19, fill: COLORS[k], stroke: "#fff", "stroke-width": 2 });
      add(g, "text", { x, y: y + 4, "text-anchor": "middle", "font-size": 11, fill: "#fff", "font-weight": "bold" }, LETTER[k]);
      add(g, "text", { x, y: y + 34, "text-anchor": "middle", "font-size": 11, fill: "#222" }, (opts.hostnames || {})[n.name] || n.name);
      if (opts.onSelect) g.addEventListener("click", () => opts.onSelect(n.name));
    });
  };
})();
