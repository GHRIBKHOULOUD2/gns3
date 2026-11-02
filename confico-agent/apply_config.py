"""
apply_config.py - reads the REAL hostnames / IPs from the GNS3 devices (to fill the pages)
and applies the Device Identity and IP Addresses pages to them (VALIDATE button).

An empty password = keep the current one. An empty IP row = leave that interface unchanged.
Every value is validated (confico_core) before anything is sent to a device.
"""
import re
from collections import defaultdict

from confico_core import _ends, kind, set_identity, set_ips
from console import run_show
from deploy import SAFE, _prepare_router, _session

ADDR = re.compile(r"^(eth\d+)\s+\S+\s+\S+\s+(\d+\.\d+\.\d+\.\d+/\d+)", re.M)   # "eth0  up  default  192.168.10.1/24"


def _project(state):
    return state["topology"]["project"]


def _shell_hostname(device, project):
    lines = [l.strip() for l in _session(device, ["hostname"], project).split("\n")]
    return next((l for l in lines if l and l != "hostname" and not l.endswith(("#", ">"))), "")


def _vtysh(device, cmds, project):
    _prepare_router(device, project)
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
                out = run_show(name, "show ip", project)
                m = re.search(r"NAME\s*:\s*([^\s\[]+)", out)
                if m:
                    hostnames[name] = m.group(1)
                ip, gw = re.search(r"IP/MASK\s*:\s*(\S+)", out), re.search(r"GATEWAY\s*:\s*(\S+)", out)
                if ip and not ip.group(1).startswith("0.0.0.0"):
                    iface = next((i for d, i in ends if d == name), "Ethernet0")
                    ips[f"{name}|{iface}"] = {"ip": ip.group(1), "gateway": "" if not gw or gw.group(1) == "0.0.0.0" else gw.group(1)}
            elif k == "router":                             # FRR: running-config + interface list
                m = re.search(r"^hostname (\S+)", run_show(name, "show running-config", project), re.M)
                hostnames[name] = m.group(1) if m else _shell_hostname(name, project)
                for iface, addr in ADDR.findall(run_show(name, "show interface brief", project)):
                    ips[f"{name}|{iface}"] = {"ip": addr, "gateway": ""}
            else:                                           # switch: only a name
                hostnames[name] = _shell_hostname(name, project)
        except Exception as e:
            errors[name] = str(e)
    return {"hostnames": {k: v for k, v in hostnames.items() if v}, "ips": ips, "errors": errors}


# ------------------------------------------------ page: Device Identity
def apply_identity(state, rows):
    """rows: [{"device", "hostname", "password"}] -> validate, then set the name (and password) on each device."""
    res = set_identity(state, rows)
    if not res["ok"]:
        return {**res, "applied": []}
    bad = [r["device"] for r in rows if r.get("password") and not SAFE.match(r["password"])]
    if bad:
        return {"ok": False, "applied": [], "errors": [f"{d}: password may only contain letters, digits and . / : _ , = - and space" for d in bad]}

    project, nodes = _project(state), {n["name"]: n for n in state["topology"]["nodes"]}
    applied, errors = [], []
    for r in rows:
        dev, host, pw = r["device"], r["hostname"], r.get("password", "")
        try:
            k = kind(nodes[dev])
            if k == "router":
                _vtysh(dev, [f"hostname {host}"] + ([f"password {pw}"] if pw else []), project)
            elif k == "pc":
                _session(dev, [f"set pcname {host}"], project)
            else:
                _session(dev, [f"hostname {host}"], project)
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
    per_router, applied, errors = defaultdict(list), [], []

    for r in rows:
        key = f'{r["device"]}|{r["interface"]}'
        if key not in state["ips"]:
            continue                                        # empty row = unchanged
        new, gw = state["ips"][key]["ip"], state["ips"][key]["gateway"]
        k = kind(nodes[r["device"]])
        if k == "pc":
            try:
                _session(r["device"], [f"ip {new} {gw}".strip()], project)
                applied.append(r["device"])
            except Exception as e:
                errors.append(f'{r["device"]}: {e}')
        elif k == "router":
            per_router[r["device"]].append((r["interface"], new))

    for dev, items in per_router.items():
        try:
            old = defaultdict(list)                         # FRR adds addresses, so remove the old one first
            for iface, addr in ADDR.findall(run_show(dev, "show interface brief", project)):
                old[iface].append(addr)
            cmds = []
            for iface, new in items:
                cmds.append(f"interface {iface}")
                cmds += [f"no ip address {o}" for o in old[iface] if o != new]
                cmds += [f"ip address {new}", "no shutdown", "exit"]
            _vtysh(dev, cmds, project)
            applied.append(dev)
        except Exception as e:
            errors.append(f"{dev}: {e}")
    return {"ok": not errors, "errors": errors, "applied": applied}
