"""
apply_routing.py - Routing pages: validate the OSPF / RIP / static rows (confico_core),
then push them to the FRR routers. Nothing is sent unless every row is valid.
"""
import re

from apply_config import read_current
from confico_core import set_ospf, set_rip, set_static
from console import run_show
from deploy import SAFE, _prepare_router, _session


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
    _prepare_router(dev, project, daemons)                 # enables ospfd / ripd and restarts FRR if needed
    args = " ".join(f'-c "{c}"' for c in ["configure terminal"] + cmds + ["end", "write memory"])
    return _session(dev, [f"vtysh {args}"], project, wait=20)


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
