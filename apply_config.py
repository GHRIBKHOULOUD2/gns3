"""
apply_config.py - reads the REAL hostnames / IPs / gateways from the GNS3 devices (to fill the pages)
and applies the pages (Identity, IP Addresses, Routing) to them when VALIDATE is clicked.

An empty password = keep the current one. An empty IP row = leave that interface unchanged.
Every value is validated (confico_core) before anything is sent to a device.
Values that already match the device are not sent again (the "baseline" saved by read_current).
"""
import ipaddress
import re
from collections import defaultdict

from confico_core import _ends, kind, set_identity, set_ips
from confico_core import set_ospf, set_rip, set_static, set_vlans
from console import run_show
from deploy import SAFE, _prepare_router, _session

ADDR = re.compile(r"^(eth\d+)\s+\S+\s+\S+\s+(\d+\.\d+\.\d+\.\d+/\d+)", re.M)   # "eth0  up  default  192.168.10.1/24"
DEFROUTE = re.compile(r"^ip route 0\.0\.0\.0/0 (\S+)", re.M)                    # router "gateway" = default route
_PW_DONE = {}        # (project, device) -> last password applied. Kept here so /api/state never shows it.


def _project(state):
    return state["topology"]["project"]


def _show(device, command, project):
    """run_show, but a stopped / unknown node raises an error instead of returning an empty answer."""
    out = run_show(device, command, project)
    if out.startswith(("ERROR", "REFUSED")):
        raise RuntimeError(out)
    return out


def _shell_hostname(device, project):
    lines = [l.strip() for l in _session(device, ["hostname"], project).split("\n")]
    return next((l for l in lines if l and l != "hostname" and not l.endswith(("#", ">"))), "")


def _ensure_frr(device, project, daemons=()):
    """One quick check. FRR is (re)started only when vtysh.conf is missing or zebra / the daemons are not running."""
    need = " && ".join(f"pgrep {d} >/dev/null" for d in ["zebra", *daemons])
    out = _session(device, [f'echo FRR_$(test -f /etc/frr/vtysh.conf && {need} && vtysh -c "show version" | grep -q FRRouting && echo READY || echo NO)'], project)
    if "FRR_READY" not in out:
        _prepare_router(device, project, daemons)


def _vtysh(device, cmds, project, daemons=()):
    _ensure_frr(device, project, daemons)
    args = " ".join(f'-c "{c}"' for c in ["configure terminal"] + cmds + ["end", "write memory"])
    return _session(device, [f"vtysh {args}"], project, wait=20)


# ------------------------------------------------ read what is really configured
def read_current(state):
    """{"hostnames": {device: name}, "ips": {"device|iface": {"ip": "a/p", "gateway": "x"}}, "errors": {...}}"""
    project, ends = _project(state), _ends(state)
    hostnames, ips, errors = {}, {}, {}
    for n in state["topology"]["nodes"]:
        name, k = n["name"], kind(n)
        try:
            if k == "pc":                                   # VPCS: "show ip"
                out = _show(name, "show ip", project)
                m = re.search(r"NAME\s*:\s*([^\s\[]+)", out)
                if m:
                    hostnames[name] = m.group(1)
                ip, gw = re.search(r"IP/MASK\s*:\s*(\S+)", out), re.search(r"GATEWAY\s*:\s*(\S+)", out)
                if ip and not ip.group(1).startswith("0.0.0.0"):
                    iface = next((i for d, i in ends if d == name), "Ethernet0")
                    ips[f"{name}|{iface}"] = {"ip": ip.group(1), "gateway": "" if not gw or gw.group(1) == "0.0.0.0" else gw.group(1)}
            elif k == "router":                             # FRR: running-config + interface list
                _ensure_frr(name, project)                  # FRR is not running after a node start
                cfg = _show(name, "show running-config", project)
                m = re.search(r"^hostname (\S+)", cfg, re.M)
                hostnames[name] = m.group(1) if m else _shell_hostname(name, project)
                for iface, addr in ADDR.findall(_show(name, "show interface brief", project)):
                    ips[f"{name}|{iface}"] = {"ip": addr, "gateway": ""}
                m = DEFROUTE.search(cfg)                    # the default route is the router's gateway
                if m:
                    gw = ipaddress.ip_address(m.group(1))
                    for key, v in ips.items():
                        if key.startswith(name + "|") and gw in ipaddress.ip_interface(v["ip"]).network:
                            v["gateway"] = str(gw)
                            break
            else:                                           # switch: only a name
                hostnames[name] = _shell_hostname(name, project)
        except Exception as e:
            errors[name] = str(e)
    hostnames = {k: v for k, v in hostnames.items() if v}
    state["baseline"] = {"hostnames": dict(hostnames), "ips": {k: dict(v) for k, v in ips.items()}}   # what the devices have now
    return {"hostnames": hostnames, "ips": ips, "errors": errors}


# ------------------------------------------------ page: Device Identity
def apply_identity(state, rows):
    """rows: [{"device", "hostname", "password"}] -> validate, then set only what changed on each device."""
    res = set_identity(state, rows)
    if not res["ok"]:
        return {**res, "applied": []}
    bad = [r["device"] for r in rows if r.get("password") and not SAFE.match(r["password"])]
    if bad:                                                 # passwords are typed into a console: whitelist the characters
        return {"ok": False, "applied": [], "errors": [f"{d}: password may only contain letters, digits and . / : _ , = - and space" for d in bad]}

    project, nodes = _project(state), {n["name"]: n for n in state["topology"]["nodes"]}
    base = state.setdefault("baseline", {}).setdefault("hostnames", {})
    applied, errors = [], []
    for r in rows:
        dev, host, pw = r["device"], r["hostname"], r.get("password", "")
        new_host = base.get(dev) != host
        new_pw = bool(pw) and _PW_DONE.get((project, dev)) != pw
        if not (new_host or new_pw):
            continue                                        # nothing new for this device
        try:
            k = kind(nodes[dev])
            if k == "router":
                cmds = ([f"hostname {host}"] if new_host else []) + ([f"password {pw}"] if new_pw else [])
                _vtysh(dev, cmds, project)
            elif k == "pc":
                _session(dev, [f"set pcname {host}"], project)
            else:                                           # switch (OVS shell)
                lines = ([f"hostname {host}"] if new_host else [])
                if new_pw:
                    lines.append(f"echo 'root:{pw}' | chpasswd")
                _session(dev, lines, project)
            base[dev] = host
            if new_pw:
                _PW_DONE[(project, dev)] = pw
            applied.append(dev)
        except Exception as e:
            errors.append(f"{dev}: {e}")
    return {"ok": not errors, "errors": errors, "applied": applied}


# ------------------------------------------------ page: IP Addresses
def apply_ips(state, rows):
    """rows: [{"device", "interface", "ip", "gateway"}] (rows with an empty ip are skipped)."""
    res = set_ips(state, rows)
    if not res["ok"]:
        return {**res, "applied": []}
    project, nodes = _project(state), {n["name"]: n for n in state["topology"]["nodes"]}
    base = state.setdefault("baseline", {}).setdefault("ips", {})
    per_router, applied, errors = defaultdict(list), [], []

    for r in rows:
        dev, key = r["device"], f'{r["device"]}|{r["interface"]}'
        if key not in state["ips"]:
            continue                                        # empty row = unchanged
        new, gw = state["ips"][key]["ip"], state["ips"][key]["gateway"]
        k = kind(nodes[dev])
        if k != "pc" and base.get(key) == state["ips"][key]:
            continue                                        # the router already has exactly this
        if k == "pc":                                       # PCs are always (re)applied: VPCS forgets its IP on restart
            try:
                _session(dev, [f"ip {new} {gw}".strip()], project)
                base[key] = dict(state["ips"][key])
                applied.append(dev)
            except Exception as e:
                errors.append(f"{dev}: {e}")
        elif k == "router":
            per_router[dev].append((r["interface"], new, gw))

    for dev, items in per_router.items():
        try:
            _ensure_frr(dev, project)
            old = defaultdict(list)                         # FRR adds addresses, so remove the old one first
            for iface, addr in ADDR.findall(_show(dev, "show interface brief", project)):
                old[iface].append(addr)
            cmds = []
            for iface, new, _ in items:
                cmds.append(f"interface {iface}")
                cmds += [f"no ip address {o}" for o in old[iface] if o != new]
                cmds += [f"ip address {new}", "no shutdown", "exit"]

            gw = next((g for _, _, g in items if g), "")    # one default gateway per router
            if gw:
                olds = DEFROUTE.findall(_show(dev, "show running-config", project))
                cmds += [f"no ip route 0.0.0.0/0 {o}" for o in olds if o != gw]
                cmds.append(f"ip route 0.0.0.0/0 {gw}")
            _vtysh(dev, cmds, project)
            for iface, _, _ in items:
                base[f"{dev}|{iface}"] = dict(state["ips"][f"{dev}|{iface}"])
            applied.append(dev)
        except Exception as e:
            errors.append(f"{dev}: {e}")
    return {"ok": not errors, "errors": errors, "applied": applied}


# ------------------------------------------------ pages: Routing (OSPF / RIP / static)
def _devices(rows):
    return list(dict.fromkeys(r.get("device") for r in rows if r.get("device")))


def _fill_ips(state, devices):
    """set_* compares networks with state["ips"]. If the IP page was never validated,
    read the real addresses from the routers so the networks can still be checked."""
    missing = [d for d in devices if not any(k.startswith(d + "|") for k in state["ips"])]
    if missing:
        for key, v in read_current(state)["ips"].items():
            if key.split("|")[0] in missing:
                state["ips"].setdefault(key, v)


def _has(dev, word, project):
    """Is 'router ospf' / 'router rip' already in the running config?"""
    return bool(re.search(rf"^{word}\b", run_show(dev, "show running-config", project), re.M))


def _push(dev, cmds, project, daemons=()):
    bad = [c for c in cmds if not SAFE.match(c)]
    if bad:
        raise ValueError(f"refused, unsafe characters in {bad}")
    return _vtysh(dev, cmds, project, daemons)             # enables ospfd / ripd and (re)starts FRR only if needed


def _apply_each(state, devices, make_cmds, daemons):
    project, applied, errors = state["topology"]["project"], [], []
    for dev in devices:
        try:
            _push(dev, make_cmds(dev, project), project, daemons)
            applied.append(dev)
        except Exception as e:
            errors.append(f"{dev}: {e}")
    return {"ok": not errors, "errors": errors, "applied": applied}


# ------------------------------------------------ OSPF
def apply_ospf(state, rows):
    """rows: [{"device", "process_id", "area", "networks"}]"""
    devs = _devices(rows)
    _fill_ips(state, devs)
    res = set_ospf(state, rows)
    if not res["ok"]:
        return {**res, "applied": []}

    def cmds(dev, project):
        o = state["routing"][dev]["ospf"]
        c = ["no router ospf"] if _has(dev, "router ospf", project) else []   # start clean: table = reality
        c += ["router ospf", f'ospf router-id {o["router_id"]}']
        for a in o["areas"]:
            c += [f'network {n} area {a["area"]}' for n in a["networks"]]
        return c + ["exit"]

    return _apply_each(state, devs, cmds, ["ospfd"])


# ------------------------------------------------ RIP
def apply_rip(state, rows):
    """rows: [{"device", "version", "networks"}]"""
    devs = _devices(rows)
    _fill_ips(state, devs)
    res = set_rip(state, rows)
    if not res["ok"]:
        return {**res, "applied": []}

    def cmds(dev, project):
        r = state["routing"][dev]["rip"]
        c = ["no router rip"] if _has(dev, "router rip", project) else []
        c += ["router rip", f'version {r["version"]}']
        c += [f"network {n}" for n in r["networks"]]
        return c + ["exit"]

    return _apply_each(state, devs, cmds, ["ripd"])


# ------------------------------------------------ static
def apply_static(state, rows):
    """rows: [{"device", "prefix", "next_hop"}]"""
    devs = _devices(rows)
    _fill_ips(state, devs)
    old = {d: list(v.get("static", [])) for d, v in state["routing"].items()}
    res = set_static(state, rows)
    if not res["ok"]:
        return {**res, "applied": []}

    def cmds(dev, project):
        new = state["routing"][dev]["static"]
        gone = [f'no ip route {r["prefix"]} {r["next_hop"]}' for r in old.get(dev, []) if r not in new]
        return gone + [f'ip route {r["prefix"]} {r["next_hop"]}' for r in new]

    return _apply_each(state, devs, cmds, ())


# ------------------------------------------------ page: Switching > VLANs (Open vSwitch)
def read_vlans(state, device):
    """What the switch really has: the VLAN id of every access port, the allowed VLANs of every trunk port.
    OVS has no VLAN names or VLAN objects, so the VLAN list is rebuilt from the ports."""
    cabled = {i for d, i in _ends(state) if d == device}
    out = _session(device, ["ovs-vsctl --columns=name,tag,trunks list Port"], _project(state)).replace("\r", "")
    ports, ids = [], set()
    for block in re.split(r"\n\s*\n", out):
        name = re.search(r'^name\s*:\s*"?([^"\s]+)"?', block, re.M)
        tag = re.search(r"^tag\s*:\s*(\d+)", block, re.M)
        trunks = re.search(r"^trunks\s*:\s*\[(.*?)\]", block, re.M)
        if not name or name.group(1) not in cabled:
            continue                                            # skip br0 and anything not cabled
        if tag:
            ports.append({"interface": name.group(1), "vlan_id": tag.group(1), "mode": "access"})
            ids.add(int(tag.group(1)))
        elif trunks and trunks.group(1).strip():
            vs = [int(x) for x in re.findall(r"\d+", trunks.group(1))]
            ports.append({"interface": name.group(1), "vlan_id": ",".join(map(str, vs)), "mode": "trunk"})
            ids.update(vs)
    return {"vlans": [{"id": i, "name": ""} for i in sorted(ids)], "ports": ports}


def apply_vlans(state, data):
    """data = {"device", "vlans": [{"id","name"}], "ports": [{"interface","vlan_id","mode"}]}
    Validates, then sets each listed port as access (tag) or trunk. Ports that are not listed stay as they are."""
    device = data.get("device")
    res = set_vlans(state, device, data.get("vlans", []), data.get("ports", []))
    if not res["ok"]:
        return {**res, "applied": []}
    lines = []
    for port, c in state["switching"][device]["ports"].items():
        if c["mode"] == "access":
            lines += [f"ovs-vsctl set port {port} tag={c['vlan']}", f"ovs-vsctl clear port {port} trunks"]
        else:
            lines += [f"ovs-vsctl set port {port} trunks={','.join(map(str, c['vlans']))}", f"ovs-vsctl clear port {port} tag"]
    if not lines:
        return {"ok": True, "errors": [], "applied": []}
    bad = [l for l in lines if not SAFE.match(l)]
    if bad:
        return {"ok": False, "applied": [], "errors": [f"refused, unsafe characters in {bad}"]}
    try:
        _session(device, lines, _project(state))
    except Exception as e:
        return {"ok": False, "applied": [], "errors": [f"{device}: {e}"]}
    return {"ok": True, "errors": [], "applied": [device]}
