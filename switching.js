/*
 * switching.js - "switching prot" pages of Configo:
 *   1) the cards (VLANs / Trunking / STP / Port Security)
 *   2) the VLANs page: Section 1 (create VLANs) and Section 2 (assign ports),
 *      with the topology square on the left
 *
 * Load it AFTER the routing script:  <script src="switching.js"></script>
 * It wraps showPage() and validate(); Identity, IP and Routing keep working exactly as before.
 */
(function () {
  const prevShow = window.showPage, prevValidate = window.validate;
  const TITLE = "Switching Configuration";
  const MINE = ["switching", "vlans", "trunking", "stp", "portsec"];

  const CARDS = [
    { id: "vlans",    title: "VLANs",         text: "Create VLANs and assign ports." },
    { id: "trunking", title: "Trunking",      text: "Enable trunk links and allow VLANs." },
    { id: "stp",      title: "STP",           text: "Prevent loops using STP." },
    { id: "portsec",  title: "Port Security", text: "Control MAC addresses per port." }
  ];
  const SOON = {                                                    // pages that are not built yet
    trunking: ["Trunking", "Not built yet. For now, set a port to Mode = trunk on the VLANs page."],
    stp:      ["STP", "Not built yet."],
    portsec:  ["Port Security", "Not built yet."]
  };

  const switches = () => topology.nodes.filter(n => kind(n) === "switch").map(n => n.name);
  const portsOf = dev => interfaces().filter(i => i.device === dev).map(i => i.interface);

  /* data[switch] = {vlans: [{id, name}], ports: [{interface, vlan_id, mode}], loaded, touched} */
  const data = {};
  let cur = null;                                                   // the switch shown on the VLANs page

  /* ---------- extra elements, created once ---------- */
  const css = document.createElement("style");
  css.textContent = `
    #swcards { position: relative; margin-top: 6px; }
    #swcards .track { display: flex; gap: 48px; overflow-x: auto; padding: 0 70px 12px 56px; scroll-snap-type: x mandatory; scrollbar-width: none; }
    #swcards .track::-webkit-scrollbar { display: none; }
    #swcards .card { flex: 0 0 210px; height: 296px; background: #a9cbee; color: #fff; padding: 26px 18px 18px; display: flex; flex-direction: column; text-align: center; cursor: pointer; scroll-snap-align: start; border: 0; font: inherit; }
    #swcards .card:nth-child(even) { background: #dbeafb; color: #2b6ca3; }
    #swcards .card:hover { outline: 2px solid #2b6ca3; }
    #swcards .card h3 { margin: 0 0 28px; font-size: 2.1rem; font-weight: 400; letter-spacing: 1px; }
    #swcards .card p { margin: 0 auto; font-weight: 600; color: #0d2f5a; font-size: .95rem; line-height: 1.35; }
    #swcards .card svg { margin-top: auto; width: 52px; height: 52px; }
    #swcards .next { position: absolute; right: 0; top: 50%; transform: translateY(-50%); width: 56px; background: none; border: 0; cursor: pointer; color: #2b6ca3; padding: 0; }
    #vlanbox h4 { margin: 20px 0 0 8px; font-weight: 500; color: #2b6ca3; }
    #vlanbox h4:first-child, #vlanbox label + h4 { margin-top: 10px; }
    #vlanbox label { margin-left: 8px; }
    #vlanbox .addbar { display: block; width: calc(100% - 16px); margin: 0 8px; padding: 14px; background: #deecfb; color: #2b6ca3; border: 0; font: inherit; cursor: pointer; }
    #vlanbox .addbar:hover { background: #cfe3f8; }
    #app td select { width: 100%; border: 0; background: transparent; padding: 14px 8px; font: inherit; color: inherit; }
    #app td select:focus { outline: 2px solid #2b6ca3; }
    #title .back { cursor: pointer; }
    #title .crumb { color: #6a4fb6; }
  `;
  document.head.appendChild(css);

  const arrow = '<svg viewBox="0 0 52 52" fill="none" stroke="currentColor" stroke-width="2"><circle cx="26" cy="26" r="24"/><path d="M12 26h27M30 17l9 9-9 9"/></svg>';
  const chevron = '<svg viewBox="0 0 60 70" fill="none" stroke-width="5" stroke-linecap="round"><path d="M5 5l25 30-25 30" stroke="#6b97c2"/><path d="M28 5l25 30-25 30" stroke="#2b6ca3"/></svg>';

  const swcards = document.createElement("div");
  swcards.id = "swcards";
  swcards.hidden = true;
  swcards.innerHTML =
    '<div class="track" id="swtrack">' +
    CARDS.map(c => `<button class="card" onclick="showPage('${c.id}')"><h3>${c.title}</h3><p>${c.text}</p>${arrow}</button>`).join("") +
    '</div><button class="next" id="swnext" aria-label="More" onclick="document.getElementById(\'swtrack\').scrollBy({left: 260, behavior: \'smooth\'})">' + chevron + '</button>';
  const cols = document.querySelector("#app .cols");
  cols.before(swcards);

  const defTable = cols.querySelector("table");                     // the normal table of the other pages
  const vbox = document.createElement("div");                       // the VLANs page: Section 1 + Section 2
  vbox.id = "vlanbox";
  vbox.hidden = true;
  defTable.before(vbox);

  /* ---------- the VLANs page ---------- */
  function render() {
    const d = data[cur], ports = portsOf(cur), sws = switches();
    const opt = (list, v) => list.map(x => `<option${String(x) === String(v) ? " selected" : ""}>${esc(x)}</option>`).join("");
    const t1 = d.vlans.map((v, i) =>
      `<tr><td><input data-t="v" data-i="${i}" data-f="id" value="${esc(v.id)}"></td>` +
      `<td><input data-t="v" data-i="${i}" data-f="name" value="${esc(v.name)}"></td></tr>`).join("");
    const t2 = d.ports.map((p, i) =>
      `<tr><td><select data-t="p" data-i="${i}" data-f="interface">${opt(ports, p.interface)}</select></td>` +
      `<td><input data-t="p" data-i="${i}" data-f="vlan_id" value="${esc(p.vlan_id)}"></td>` +
      `<td><select data-t="p" data-i="${i}" data-f="mode">${opt(["access", "trunk"], p.mode)}</select></td></tr>`).join("");
    vbox.innerHTML =
      (sws.length > 1 ? `<label>Switch <select id="swsel">${opt(sws, cur)}</select></label>` : "") +
      `<h4>Section 1 &ndash; Create VLANs</h4>
       <table><thead><tr><th>VLAN ID</th><th>VLAN Name</th></tr></thead><tbody>${t1}</tbody></table>
       <button class="addbar" data-add="v">+ ADD</button>
       <h4>Section 2 &ndash; Assign Ports to VLANs</h4>
       <table><thead><tr><th>Interface</th><th>VLAN ID</th><th>Mode</th></tr></thead><tbody>${t2}</tbody></table>
       <button class="addbar" data-add="p">ADD</button>`;
  }

  vbox.addEventListener("input", e => {                             // keep what is typed
    const t = e.target;
    if (!t.dataset.t) return;
    const d = data[cur];
    d[t.dataset.t === "v" ? "vlans" : "ports"][+t.dataset.i][t.dataset.f] = t.value;
    d.touched = true;
  });
  vbox.addEventListener("change", e => { if (e.target.id === "swsel") open(e.target.value); });
  vbox.addEventListener("click", e => {
    const what = e.target.dataset.add;
    if (!what) return;
    const d = data[cur];
    d.touched = true;
    if (what === "v") d.vlans.push({ id: "", name: "" });
    else {                                                          // a new port row starts on a port that has no row yet
      const used = d.ports.map(p => p.interface), all = portsOf(cur);
      d.ports.push({ interface: all.find(x => !used.includes(x)) || all[0] || "", vlan_id: "", mode: "access" });
    }
    render();
  });

  /* show the VLANs of one switch; the first time, read what the switch really has */
  function open(dev) {
    cur = dev;
    if (!data[dev]) data[dev] = { vlans: [{ id: "", name: "" }], ports: [{ interface: portsOf(dev)[0] || "", vlan_id: "", mode: "access" }], loaded: false, touched: false };
    render();
    if (!data[dev].loaded) loadReal(dev);
  }

  async function loadReal(dev) {
    data[dev].loaded = true;
    msg("Reading the VLANs from the switch in GNS3, please wait...");
    try {
      const r = await api("/api/current/vlans?device=" + encodeURIComponent(dev));
      if (!data[dev].touched && (r.vlans.length || r.ports.length)) {   // never overwrite what the user already typed
        data[dev].vlans = r.vlans.map(v => ({ id: String(v.id), name: v.name || "" }));
        data[dev].ports = r.ports;
        if (cur === dev) render();
      }
      msg(r.ports.length ? "Current VLANs loaded from the switch." : "The switch has no VLAN configured yet.", "ok");
    } catch (e) { msg("Could not read the switch (" + esc(e.message) + ").", "bad"); }
  }

  /* ---------- pages ---------- */
  window.showPage = function (p) {
    const foot = document.querySelector("#app .foot");
    swcards.hidden = true; vbox.hidden = true; defTable.hidden = false;
    if (!MINE.includes(p)) return prevShow(p);                      // Identity / IP / Routing: the old code

    const rc = document.getElementById("cards"); if (rc) rc.hidden = true;                    // routing cards
    const add = document.querySelector("#app .foot .btn.alt"); if (add) add.hidden = true;    // routing "+ ADD ROW"
    page = p;
    document.getElementById("menu").classList.remove("open");
    document.querySelectorAll("#menu button[data-p]").forEach(b => b.classList.toggle("active", b.dataset.p === "switching"));
    document.querySelectorAll(".onlyips").forEach(e => e.hidden = true);
    const title = document.getElementById("title"), sub = document.getElementById("sub");
    const crumb = name => `<span class="back" onclick="showPage('switching')">${TITLE}</span> <span class="crumb">&gt; ${name}</span>`;

    if (p === "switching") {                                        // the cards
      title.textContent = TITLE;
      sub.textContent = "Configure VLANs, assign interfaces, set trunk links, and apply switching protocols such as STP or Port Security.";
      swcards.hidden = false; cols.hidden = true; foot.hidden = true;
      document.getElementById("swnext").hidden = document.getElementById("swtrack").scrollWidth <= document.getElementById("swtrack").clientWidth;
      return;
    }
    if (SOON[p]) {                                                  // pages not built yet
      title.innerHTML = crumb(SOON[p][0]);
      sub.textContent = SOON[p][1];
      cols.hidden = true; foot.hidden = true;
      return;
    }

    title.innerHTML = crumb("VLANs");                               // p === "vlans"
    sub.textContent = "Create VLANs, then assign each port to a VLAN as access or trunk.";
    cols.hidden = false; foot.hidden = false; defTable.hidden = true; vbox.hidden = false;
    draw();
    const sws = switches();
    if (!sws.length) { vbox.innerHTML = ""; msg("This topology has no switch.", "bad"); return; }
    msg("");
    open(sws.includes(cur) ? cur : sws[0]);
  };

  /* ---------- VALIDATE ---------- */
  window.validate = async function () {
    if (page !== "vlans") return prevValidate();
    const d = data[cur];
    const body = {
      device: cur,
      vlans: d.vlans.filter(v => String(v.id).trim()).map(v => ({ id: String(v.id).trim(), name: String(v.name).trim() })),
      ports: d.ports.filter(p => String(p.vlan_id).trim()).map(p => ({ interface: p.interface, vlan_id: String(p.vlan_id).trim(), mode: p.mode }))
    };
    msg("Applying to the switch in GNS3, please wait...");
    try {
      const r = await api("/api/apply/vlans", body);
      msg(r.ok ? (r.applied.length ? "Done. Applied on: " + r.applied.map(esc).join(", ") : "Nothing to apply: add at least one port in Section 2.")
               : "Please fix:<ul>" + r.errors.map(e => "<li>" + esc(e) + "</li>").join("") + "</ul>", r.ok ? "ok" : "bad");
    } catch (e) { msg(esc(e.message), "bad"); }
  };
})();
