"""
confico_core.py - the logic behind every Configo wizard page (no web code).

state = {
  "topology":  {"project", "nodes": [...], "links": ["A:eth0 <-> B:eth1", ...]},
  "identity":  {device: {"hostname", "password"}},
  "ips":       {"device|interface": {"ip", "gateway"}},
  "routing":   {router: {"ospf":   {"process_id", "router_id", "areas": [{"area", "networks": [...]}]},
                         "rip":    {"version", "networks": [...]},
                         "static": [{"prefix", "next_hop"}]}},
  "switching": {switch: {"vlans": [{"id", "name"}],
                         "ports": {iface: {"mode": "access", "vlan": 10}
                                        | {"mode": "trunk", "vlans": [10, 20]}}}}
}
Every set_* function validates first and saves ONLY if there are no errors.
It returns {"ok": bool, "errors": [...]}.
"""
import ipaddress
import re

HOSTNAME = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,62}$")
VLAN_NAME = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


# ---------------------------------------------------------------- helpers
def kind(node):
    """router / switch / pc, guessed from node type and name."""
    if node["type"] == "vpcs" or re.match(r"(?i)pc", node["name"]):
        return "pc"
    if re.search(r"(?i)ovs|switch", node["name"]):
        return "switch"
    return "router"


def _nodes(state):
    return {n["name"]: n for n in state["topology"]["nodes"]}


def _ends(state):
    """All (device, interface) pairs that are cabled."""
    out = []
    for link in state["topology"]["links"]:
        for end in link.split(" <-> "):
            dev, _, iface = end.partition(":")
            out.append((dev, iface))
    return out


def _connected(state, device):
    """ip_interface objects already saved for a device."""
    res = []
    for key, v in state["ips"].items():
        if key.split("|")[0] == device and v.get("ip"):
            res.append(ipaddress.ip_interface(v["ip"].strip().replace(" ", "/")))
    return res


def _result(errors):
    return {"ok": not errors, "errors": errors}


def _split(v):
    """'a, b c' -> ['a','b','c'];  list stays a list;  10 -> [10]."""
    if isinstance(v, (list, tuple)):
        return list(v)
    if isinstance(v, str):
        return [x for x in re.split(r"[,\s]+", v.strip()) if x]
    return [] if v is None else [v]


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------- rows to pre-fill the tables
def device_rows(state):
    """Identity page: one row per device."""
    return [{"device": n["name"], "type": kind(n)} for n in state["topology"]["nodes"]]


def interface_rows(state):
    """IP page: one row per cabled interface."""
    seen, rows = set(), []
    for dev, iface in _ends(state):
        if (dev, iface) not in seen:
            seen.add((dev, iface))
            rows.append({"device": dev, "interface": iface})
    return rows


# ------------------------------------------------------- page: upload
def import_topology(source=None, project="test1"):
    """
    source=None  -> read the live lab from GNS3
    source=dict  -> parsed .gns3 file content
    Returns a fresh state.
    """
    if source is None:
        from gns3_client import get_topology
        topo = get_topology(project)
    else:
        topo = _parse_gns3_file(source)
    return {"topology": topo, "identity": {}, "ips": {}, "routing": {}, "switching": {}}


def _parse_gns3_file(data):
    t = data.get("topology", data)
    by_id = {n["node_id"]: n for n in t.get("nodes", [])}

    def port_name(node, a, p, end):
        for port in node.get("ports", []):
            if port.get("adapter_number") == a and port.get("port_number") == p:
                return port.get("name", f"{a}/{p}")
        t = node["node_type"]                       # real .gns3 files often have no port names
        if t == "docker":
            return f"eth{a}"
        if t == "vpcs":
            return f"Ethernet{a}"
        if t == "ethernet_switch":
            return f"Ethernet{p}"
        return (end.get("label") or {}).get("text") or f"{a}/{p}"

    links = []
    for l in t.get("links", []):
        ends = [f'{by_id[e["node_id"]]["name"]}:{port_name(by_id[e["node_id"]], e["adapter_number"], e["port_number"], e)}'
                for e in l["nodes"]]
        links.append(" <-> ".join(ends))
    nodes = [{"name": n["name"], "type": n["node_type"], "status": "imported"} for n in t.get("nodes", [])]
    return {"project": data.get("name", "imported"), "nodes": nodes, "links": links}


# ---------------------------------------------------- page: identity
def set_identity(state, rows):
    """rows: [{"device", "hostname", "password"}]"""
    nodes, errors, seen = _nodes(state), [], set()
    for r in rows:
        dev = r.get("device")
        if dev not in nodes:
            errors.append(f"{dev}: unknown device")
            continue
        host = r.get("hostname", "")
        if not HOSTNAME.match(host):
            errors.append(f'{dev}: hostname "{host}" is invalid')
        elif host.lower() in seen:
            errors.append(f'{dev}: hostname "{host}" is already used')
        seen.add(host.lower())
        pw = r.get("password", "")
        if kind(nodes[dev]) != "pc" and pw and len(pw) < 8:
            errors.append(f"{dev}: password needs at least 8 characters (or leave it empty to keep the current one)")
    if not errors:
        state["identity"] = {r["device"]: {"hostname": r["hostname"], "password": r.get("password", "")} for r in rows}
    return _result(errors)


# ------------------------------------------------------ page: IPs
def set_ips(state, rows):
    """rows: [{"device", "interface", "ip": "192.168.1.1/24" or "192.168.1.1 255.255.255.0", "gateway"}]"""
    cabled, errors = set(_ends(state)), []
    parsed, used, clean = {}, {}, {}
    for r in rows:
        if not str(r.get("ip", "")).strip():
            continue                                    # empty row = leave this interface unchanged
        who = f'{r["device"]} {r["interface"]}'
        if (r["device"], r["interface"]) not in cabled:
            errors.append(f"{who}: this interface is not in the topology")
            continue
        try:
            iface = ipaddress.ip_interface(r["ip"].strip().replace(" ", "/"))
        except ValueError:
            errors.append(f"{who}: invalid address or mask")
            continue
        if iface.ip in used:
            errors.append(f"{who}: {iface.ip} is already used by {used[iface.ip]}")
        used[iface.ip] = who
        gw = r.get("gateway", "").strip()
        if gw:
            try:
                if ipaddress.ip_address(gw) not in iface.network:
                    errors.append(f"{who}: gateway {gw} is outside {iface.network}")
            except ValueError:
                errors.append(f"{who}: invalid gateway")
        for other_who, other in parsed.get(r["device"], []):
            if iface.network.overlaps(other.network):
                errors.append(f"{who}: subnet {iface.network} overlaps {other_who} ({other.network})")
        parsed.setdefault(r["device"], []).append((who, iface))
        clean[f'{r["device"]}|{r["interface"]}'] = {"ip": str(iface), "gateway": gw}   # always ip/prefix
    if not errors:
        state["ips"] = clean
    return _result(errors)


# --------------------------------------------------- page: routing
def _router_conn(state, device, errors):
    """Connected interfaces of a router, or None (error added)."""
    nodes = _nodes(state)
    if device not in nodes or kind(nodes[device]) != "router":
        errors.append(f"{device}: not a router")
        return None
    conn = _connected(state, device)
    if not conn:
        errors.append(f"{device}: no IP addresses saved yet, complete the IP page first")
        return None
    return conn


def _connected_networks(device, raw, conn, errors, label):
    """Validate a list of networks: each must be a subnet the router is directly connected to."""
    nets, out = {i.network for i in conn}, []
    items = _split(raw)
    if not items:
        errors.append(f"{device}: {label} needs at least one network")
    for n in items:
        try:
            net = ipaddress.ip_network(n, strict=True)
        except ValueError:
            errors.append(f"{device}: invalid {label} network {n}")
            continue
        if net not in nets:
            errors.append(f"{device}: {label} network {net} is not directly connected")
        else:
            out.append(str(net))
    return out


def _area(v):
    """Area as int 0..4294967295 or dotted IPv4; None if invalid."""
    try:
        if isinstance(v, str) and "." in v:
            return str(ipaddress.IPv4Address(v))
        a = int(v)
        return a if 0 <= a <= 4294967295 else None
    except (TypeError, ValueError):
        return None


def set_ospf(state, rows):
    """
    OSPF page rows: [{"device", "process_id", "area", "networks": "192.168.10.0/24, ..." or list,
                      "router_id" (optional)}]
    Several rows may use the same router with different areas.
    """
    errors, grouped = [], {}
    for r in rows:
        dev = r.get("device")
        conn = _router_conn(state, dev, errors)
        if conn is None:
            continue
        pid = _int(r.get("process_id"))
        if pid is None or not 1 <= pid <= 65535:
            errors.append(f"{dev}: process ID must be 1-65535")
        area = _area(r.get("area"))
        if area is None:
            errors.append(f'{dev}: invalid area {r.get("area")}')
        nets = _connected_networks(dev, r.get("networks"), conn, errors, "OSPF")
        g = grouped.setdefault(dev, {"process_id": pid,
                                     "router_id": r.get("router_id") or str(max(i.ip for i in conn)),
                                     "areas": []})
        if g["process_id"] != pid:
            errors.append(f"{dev}: all rows of one router need the same process ID")
        try:
            ipaddress.IPv4Address(g["router_id"])
        except ValueError:
            errors.append(f'{dev}: invalid router-id {g["router_id"]}')
        g["areas"].append({"area": area, "networks": nets})
    if not errors:
        for dev, g in grouped.items():
            state["routing"].setdefault(dev, {})["ospf"] = g
    return _result(errors)


def set_rip(state, rows):
    """rows: [{"device", "version": 1|2 (default 2), "networks": "..." or list}]"""
    errors, grouped = [], {}
    for r in rows:
        dev = r.get("device")
        conn = _router_conn(state, dev, errors)
        if conn is None:
            continue
        ver = _int(r.get("version") or 2)
        if ver not in (1, 2):
            errors.append(f"{dev}: RIP version must be 1 or 2")
        nets = _connected_networks(dev, r.get("networks"), conn, errors, "RIP")
        grouped[dev] = {"version": ver, "networks": nets}
    if not errors:
        for dev, g in grouped.items():
            state["routing"].setdefault(dev, {})["rip"] = g
    return _result(errors)


def set_static(state, rows):
    """rows: [{"device", "prefix": "10.0.0.0/8", "next_hop": "192.168.10.2"}]"""
    errors, grouped = [], {}
    for r in rows:
        dev = r.get("device")
        conn = _router_conn(state, dev, errors)
        if conn is None:
            continue
        try:
            prefix = ipaddress.ip_network(r["prefix"], strict=True)
            nh = ipaddress.ip_address(r["next_hop"])
        except (KeyError, ValueError):
            errors.append(f"{dev}: invalid static route {r}")
            continue
        if not any(nh in i.network for i in conn):
            errors.append(f"{dev}: next hop {nh} is not in a connected subnet")
        grouped.setdefault(dev, []).append({"prefix": str(prefix), "next_hop": str(nh)})
    if not errors:
        for dev, routes in grouped.items():
            state["routing"].setdefault(dev, {})["static"] = routes
    return _result(errors)


# -------------------------------------------------- page: switching
def set_vlans(state, device, vlans, ports):
    """
    VLANs page of one switch.
    vlans = [{"id": 10, "name": "Sales"}]                       (section 1)
    ports = [{"interface": "eth1", "vlan_id": "10", "mode": "access"},
             {"interface": "eth0", "vlan_id": "10,20", "mode": "trunk"}]   (section 2)
    """
    nodes, errors = _nodes(state), []
    if device not in nodes or kind(nodes[device]) != "switch":
        return _result([f"{device}: not a switch"])
    cabled = {i for d, i in _ends(state) if d == device}
    valid = lambda v: v is not None and 1 <= v <= 4094

    ids, names, vlist = set(), set(), []
    for v in vlans:
        vid, name = _int(v.get("id")), (v.get("name") or "").strip()
        if not valid(vid):
            errors.append(f'VLAN id {v.get("id")} must be 1-4094')
            continue
        if vid in ids:
            errors.append(f"VLAN {vid} is listed twice")
        if name and not VLAN_NAME.match(name):
            errors.append(f'VLAN {vid}: name "{name}" is invalid (letters, digits, - and _)')
        if name and name.lower() in names:
            errors.append(f'VLAN name "{name}" is used twice')
        ids.add(vid)
        names.add(name.lower())
        vlist.append({"id": vid, "name": name})

    pcfg = {}
    for p in ports:
        iface, mode = p.get("interface"), (p.get("mode") or "").lower()
        who = f"{device} {iface}"
        if iface not in cabled:
            errors.append(f"{who}: port is not cabled in the topology")
            continue
        wanted = [_int(x) for x in _split(p.get("vlan_id"))]
        if not wanted or not all(valid(x) for x in wanted):
            errors.append(f"{who}: VLAN id must be 1-4094")
        elif any(x not in ids for x in wanted):
            errors.append(f"{who}: uses a VLAN that was not created in section 1")
        elif mode == "access" and len(wanted) == 1:
            pcfg[iface] = {"mode": "access", "vlan": wanted[0]}
        elif mode == "access":
            errors.append(f"{who}: an access port carries exactly one VLAN")
        elif mode == "trunk":
            pcfg[iface] = {"mode": "trunk", "vlans": wanted}
        else:
            errors.append(f'{who}: mode must be "access" or "trunk"')
    if not errors:
        state["switching"][device] = {"vlans": vlist, "ports": pcfg}
    return _result(errors)


# ----------------------------------------------- final: generate config
def generate_config(state):
    """
    Returns {device: {"kind": ..., "commands": [...]}}.
    router: vtysh configure-mode lines   switch: shell ovs-vsctl lines   pc: VPCS lines
    NOTE: FRR has no OSPF process id on a single instance, so it is stored but not emitted.
          VLAN names are stored for documentation (Open vSwitch has no VLAN names).
    """
    out = {}
    for n in state["topology"]["nodes"]:
        name, k, cmds = n["name"], kind(n), []
        ident = state["identity"].get(name, {})
        mine = {key.split("|")[1]: v for key, v in state["ips"].items() if key.split("|")[0] == name}

        if k == "router":
            if ident.get("hostname"):
                cmds.append(f'hostname {ident["hostname"]}')
            if ident.get("password"):
                cmds.append(f'password {ident["password"]}')
            for iface, v in mine.items():
                cmds += [f"interface {iface}", f'ip address {v["ip"]}', "no shutdown", "exit"]
            r = state["routing"].get(name, {})
            if "ospf" in r:
                cmds += ["router ospf", f'ospf router-id {r["ospf"]["router_id"]}']
                for a in r["ospf"]["areas"]:
                    cmds += [f'network {net} area {a["area"]}' for net in a["networks"]]
                cmds.append("exit")
            if "rip" in r:
                cmds += ["router rip", f'version {r["rip"]["version"]}']
                cmds += [f"network {net}" for net in r["rip"]["networks"]]
                cmds.append("exit")
            cmds += [f'ip route {s["prefix"]} {s["next_hop"]}' for s in r.get("static", [])]
        elif k == "switch":
            for port, c in state["switching"].get(name, {}).get("ports", {}).items():
                if c["mode"] == "access":
                    cmds.append(f'ovs-vsctl set port {port} tag={c["vlan"]}')
                else:
                    cmds.append(f'ovs-vsctl set port {port} trunks={",".join(map(str, c["vlans"]))}')
        else:  # pc
            if ident.get("hostname"):
                cmds.append(f'set pcname {ident["hostname"]}')
            for v in mine.values():
                cmds.append(f'ip {v["ip"]} {v["gateway"]}'.strip())
        out[name] = {"kind": k, "commands": cmds}
    return out


# ------------------------------------------------------------- demo
def demo_state():
    import json

    s = import_topology()                       # live from GNS3
    print("nodes:", [n["name"] for n in s["topology"]["nodes"]])

    print(set_identity(s, [
        {"device": "frrouting-frr-1", "hostname": "R1", "password": "Confico123"},
        {"device": "frrouting-frr-2", "hostname": "R2", "password": "Confico123"},
        {"device": "OpenvSwitch-1", "hostname": "SW1", "password": "Confico123"},
        {"device": "PC1", "hostname": "PC1"},
        {"device": "PC2", "hostname": "PC2"},
    ]))
    print(set_ips(s, [
        {"device": "frrouting-frr-1", "interface": "eth0", "ip": "192.168.10.1/24"},
        {"device": "frrouting-frr-2", "interface": "eth0", "ip": "192.168.10.2/24"},
        {"device": "PC1", "interface": "Ethernet0", "ip": "192.168.10.11/24", "gateway": "192.168.10.1"},
        {"device": "PC2", "interface": "Ethernet0", "ip": "192.168.10.12/24", "gateway": "192.168.10.1"},
    ]))
    print(set_ospf(s, [
        {"device": "frrouting-frr-1", "process_id": 1, "area": 0, "networks": "192.168.10.0/24"},
        {"device": "frrouting-frr-2", "process_id": 1, "area": 0, "networks": "192.168.10.0/24"},
    ]))
    print(set_vlans(s, "OpenvSwitch-1",
                    [{"id": 10, "name": "LAN10"}],
                    [{"interface": p, "vlan_id": "10", "mode": "access"} for p in ("eth0", "eth1", "eth2", "eth3")]))

    print(json.dumps(generate_config(s), indent=2))

    # Deliberate mistake: the validator must catch it
    print(set_ips(s, [{"device": "PC1", "interface": "Ethernet0", "ip": "192.168.10.1/24", "gateway": "10.0.0.1"},
                      {"device": "frrouting-frr-1", "interface": "eth0", "ip": "192.168.10.1/24"}]))
    return s


if __name__ == "__main__":
    demo_state()
