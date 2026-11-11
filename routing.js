/*
 * routing.js - "routing prot" pages of Configo:
 *   1) the protocol cards (OSPF / RIP / STATIC)
 *   2) one table per protocol, with the topology square on the left
 *
 * Loaded AFTER the main script of configo.html. It wraps showPage() and validate():
 * the Identity and IP pages keep working exactly as before.
 */
(function () {
  const origShow = window.showPage, origValidate = window.validate;
  const TITLE = "Routing Protocol Configuration";

  const CARDS = [
    { id: "ospf",   title: "OSPF",   text: "Fast convergence, hierarchical routing." },
    { id: "rip",    title: "RIP",    text: "Simple, hop-based distance vector." },
    { id: "static", title: "STATIC", text: "Full control over route paths." }
  ];

  const PROTO = {
    ospf: {
      name: "OSPF", api: "/api/apply/ospf",
      sub: "Choose a process ID, assign areas, and select interfaces or networks to include.",
      heads: ["Router", "Process ID", "Area ID", "Networks to include"],
      fields: ["process_id", "area", "networks"],
      hint: { process_id: "1", area: "0", networks: "192.168.10.0/24, 10.0.0.0/30" },
      fresh: dev => ({ device: dev, process_id: "1", area: "0", networks: "", auto: true }),
      blank: r => !r.networks.trim()
    },
    rip: {
      name: "RIP", api: "/api/apply/rip",
      sub: "Choose the RIP version and the connected networks to advertise.",
      heads: ["Router", "Version", "Networks to include"],
      fields: ["version", "networks"],
      hint: { version: "2", networks: "192.168.10.0/24" },
      fresh: dev => ({ device: dev, version: "2", networks: "", auto: true }),
      blank: r => !r.networks.trim()
    },
    static: {
      name: "STATIC", api: "/api/apply/static",
      sub: "Add routes by hand: a destination network and the next hop that reaches it.",
      heads: ["Router", "Destination network", "Next hop"],
      fields: ["prefix", "next_hop"],
      hint: { prefix: "10.0.0.0/8", next_hop: "192.168.10.2" },
      fresh: dev => ({ device: dev, prefix: "", next_hop: "" }),
      blank: r => !r.prefix.trim() && !r.next_hop.trim()
    }
  };

  const rows = {};                                              // rows[protocol] = [{device, ...fields}]
  const routers = () => topology.nodes.filter(n => kind(n) === "router");

  /* "192.168.10.1/24" or "192.168.10.1 255.255.255.0" -> "192.168.10.0/24" */
  function netOf(v) {
    const [a, p] = short(v).split("/"), n = Number(p), o = (a || "").split(".").map(Number);
    if (o.length !== 4 || o.some(x => isNaN(x) || x < 0 || x > 255) || isNaN(n) || n < 0 || n > 32) return "";
    const mask = n === 0 ? 0 : (~0 << (32 - n)) >>> 0;
    const ip = ((o[0] << 24) | (o[1] << 16) | (o[2] << 8) | o[3]) >>> 0;
    const net = (ip & mask) >>> 0;
    return [net >>> 24, (net >>> 16) & 255, (net >>> 8) & 255, net & 255].join(".") + "/" + n;
  }

  /* the networks a router is directly connected to, from the IPs typed on the IP page */
  function connected(dev) {
    const out = [];
    interfaces().filter(i => i.device === dev).forEach(i => {
      const v = get("ip|" + dev + "|" + i.interface).trim();
      const n = v && netOf(v);
      if (n && !out.includes(n)) out.push(n);
    });
    return out;
  }

  /* ---------- extra elements, created once ---------- */
  const css = document.createElement("style");
  css.textContent = `
    #cards { position: relative; margin-top: 6px; }
    #cards .track { display: flex; gap: 48px; overflow-x: auto; padding: 0 70px 12px 56px; scroll-snap-type: x mandatory; scrollbar-width: none; }
    #cards .track::-webkit-scrollbar { display: none; }
    #cards .card { flex: 0 0 210px; height: 296px; background: #a9cbee; color: #fff; padding: 26px 18px 18px; display: flex; flex-direction: column; text-align: center; cursor: pointer; scroll-snap-align: start; border: 0; font: inherit; }
    #cards .card:nth-child(even) { background: #dbeafb; color: #2b6ca3; }
    #cards .card:hover { outline: 2px solid #2b6ca3; }
    #cards .card h3 { margin: 0 0 28px; font-size: 2.1rem; font-weight: 400; letter-spacing: 1px; }
    #cards .card p { margin: 0 auto; font-weight: 600; color: #0d2f5a; font-size: .95rem; line-height: 1.35; }
    #cards .card svg { margin-top: auto; width: 52px; height: 52px; }
    #cards .next { position: absolute; right: 0; top: 50%; transform: translateY(-50%); width: 56px; background: none; border: 0; cursor: pointer; color: #2b6ca3; padding: 0; }
    #app td select { width: 100%; border: 0; background: transparent; padding: 14px 8px; font: inherit; color: inherit; }
    #app td select:focus { outline: 2px solid #2b6ca3; }
    #app .btn.alt { background: #fff; color: #2b6ca3; border: 2px solid #2b6ca3; margin-right: 10px; }
    #title .back { cursor: pointer; }
    #title .crumb { color: #6a4fb6; }
  `;
  document.head.appendChild(css);

  const arrow = '<svg viewBox="0 0 52 52" fill="none" stroke="currentColor" stroke-width="2"><circle cx="26" cy="26" r="24"/><path d="M12 26h27M30 17l9 9-9 9"/></svg>';
  const chevron = '<svg viewBox="0 0 60 70" fill="none" stroke-width="5" stroke-linecap="round"><path d="M5 5l25 30-25 30" stroke="#6b97c2"/><path d="M28 5l25 30-25 30" stroke="#2b6ca3"/></svg>';

  const box = document.createElement("div");
  box.id = "cards";
  box.hidden = true;
  box.innerHTML =
    '<div class="track" id="track">' +
    CARDS.map(c => `<button class="card" onclick="showPage('${c.id}')"><h3>${c.title}</h3><p>${c.text}</p>${arrow}</button>`).join("") +
    '</div><button class="next" id="next" aria-label="More" onclick="document.getElementById(\'track\').scrollBy({left: 260, behavior: \'smooth\'})">' + chevron + '</button>';
  document.querySelector("#app .cols").before(box);

  const foot = document.querySelector("#app .foot");
  const addBtn = document.createElement("button");
  addBtn.className = "btn alt";
  addBtn.textContent = "+ ADD ROW";
  addBtn.hidden = true;
  addBtn.onclick = () => window.addRow();
  foot.prepend(addBtn);

  /* ---------- the table of one protocol ---------- */
  function table(p) {
    const P = PROTO[p], rs = routers();
    document.getElementById("thead").innerHTML = "<tr>" + P.heads.map(h => `<th>${h}</th>`).join("") + "</tr>";
    document.getElementById("rows").innerHTML = rows[p].map((r, i) => {
      const sel = `<select data-r="${i}" data-f="device">` +
        rs.map(n => `<option value="${esc(n.name)}"${n.name === r.device ? " selected" : ""}>${esc(n.name)}</option>`).join("") + "</select>";
      const cells = P.fields.map(f =>
        `<td><input data-r="${i}" data-f="${f}" value="${esc(r[f])}" placeholder="${esc(P.hint[f] || "")}"></td>`).join("");
      return `<tr><td>${sel}</td>${cells}</tr>`;
    }).join("");
  }

  document.getElementById("rows").addEventListener("input", e => {
    const t = e.target, P = PROTO[page];
    if (!P || t.dataset.r === undefined) return;
    const r = rows[page][+t.dataset.r];
    r[t.dataset.f] = t.value;
    if (t.dataset.f === "networks") r.auto = false;             // the user took control of this cell
  });
  document.getElementById("rows").addEventListener("change", e => {
    const t = e.target;
    if (t.tagName !== "SELECT" || !PROTO[page]) return;
    const r = rows[page][+t.dataset.r];
    r.device = t.value;
    if (r.auto) r.networks = connected(r.device).join(", ");
    table(page);
  });

  /* ---------- pages ---------- */
  window.showPage = function (p) {
    const cols = document.querySelector("#app .cols");
    if (p !== "routing" && !PROTO[p]) {                         // identity / ips: the old code
      box.hidden = true; cols.hidden = false; foot.hidden = false; addBtn.hidden = true;
      document.getElementById("title").textContent = "";
      return origShow(p);
    }
    page = p;
    const title = document.getElementById("title");
    document.getElementById("menu").classList.remove("open");
    document.querySelectorAll("#menu button[data-p]").forEach(b => b.classList.toggle("active", b.dataset.p === "routing"));
    document.querySelectorAll(".onlyips").forEach(e => e.hidden = true);

    if (p === "routing") {                                      // the cards
      title.textContent = TITLE;
      document.getElementById("sub").textContent = "Set up the routing logic for your network. Choose between dynamic protocols like OSPF and RIP, or go for a static routing approach.";
      box.hidden = false; cols.hidden = true; foot.hidden = true;
      const track = document.getElementById("track");
      document.getElementById("next").hidden = track.scrollWidth <= track.clientWidth;
      return;
    }

    const P = PROTO[p];                                         // a protocol table
    if (!rows[p]) rows[p] = routers().map(n => P.fresh(n.name));
    rows[p].forEach(r => { if (r.auto) r.networks = connected(r.device).join(", "); });
    title.innerHTML = `<span class="back" onclick="showPage('routing')">${TITLE}</span> <span class="crumb">&gt; ${P.name}</span>`;
    document.getElementById("sub").textContent = P.sub;
    box.hidden = true; cols.hidden = false; foot.hidden = false; addBtn.hidden = false;
    table(p);
    draw();
    msg(routers().length ? "" : "This topology has no router.", routers().length ? "" : "bad");
  };

  window.addRow = function () {
    const p = page, P = PROTO[p];
    if (!P || !routers().length) return;
    const last = rows[p][rows[p].length - 1];
    const r = P.fresh(last ? last.device : routers()[0].name);
    r.auto = false;                                             // an extra row is filled by hand
    rows[p].push(r);
    table(p);
  };

  window.validate = async function () {
    const P = PROTO[page];
    if (!P) return origValidate();
    const data = rows[page].filter(r => !P.blank(r)).map(r => {
      const o = {};
      Object.keys(r).filter(k => k !== "auto").forEach(k => o[k] = r[k].trim());
      return o;
    });
    if (!data.length) { msg("Nothing to apply: fill at least one row.", "bad"); return; }
    msg("Applying to the routers in GNS3, please wait (the first OSPF/RIP setup can take about a minute)...");
    try {
      const r = await api(P.api, data);
      msg(r.ok ? "Done. Applied on: " + r.applied.map(esc).join(", ")
               : "Please fix:<ul>" + r.errors.map(e => "<li>" + esc(e) + "</li>").join("") + "</ul>", r.ok ? "ok" : "bad");
    } catch (e) { msg(esc(e.message), "bad"); }
  };
})();
