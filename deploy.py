"""
deploy.py - pushes the validated Configo state to the GNS3 devices, then verifies.

Safety rules built in:
  * the config comes only from generate_config(state), i.e. from validated data
  * every command is checked against a character whitelist (commands end up in a shell)
  * nothing is pushed unless approved=True (a human says YES)
  * router configs are backed up to ./backups before any change
"""
import os
import re
import socket
import time

from confico_core import generate_config, kind
from console import _read_until_prompt, _topology, open_console, run_show
from gns3_client import get_topology

SAFE = re.compile(r"^[A-Za-z0-9 ./:_,=-]+$")      # no quotes, ; | & $ ` etc.


def _session(device, lines, project, wait=15):
    """Type each line into the device console and return the combined output."""
    node = next((n for n in _topology(project)["nodes"] if n["name"] == device), None)
    if node is None:
        raise ValueError(f"{device} not found in GNS3")
    out = []
    with open_console(node["console_host"], node["console_port"]) as s:
        for line in lines:
            s.sendall(line.encode() + b"\r\n")
            out.append(_read_until_prompt(s, wait).replace("\r", ""))
    return "\n".join(out)


def _prepare_router(device, project, daemons=()):
    """Enable ospfd/ripd, make sure FRR runs with the daemons we need, wait until vtysh answers."""
    _session(device, [
        "sed -i 's/^ospfd=no/ospfd=yes/;s/^ripd=no/ripd=yes/' /etc/frr/daemons",
        "echo 'service integrated-vtysh-config' > /etc/frr/vtysh.conf",
        "chown -R frr:frr /etc/frr",
    ], project)
    check = " && ".join(f"pgrep {d} >/dev/null" for d in ["zebra", *daemons])   # zebra = FRR itself, always needed
    out = _session(device, [f"echo FRR_$({check} && echo UP || echo DOWN)"], project)
    if "FRR_UP" not in out:
        print(f"  {device}: FRR not running, starting it")
        _session(device, ["/usr/lib/frr/frrinit.sh restart"], project, wait=30)
    for _ in range(10):                      # wait until vtysh really answers
        if "FRRouting" in _session(device, ['vtysh -c "show version"'], project):
            return
        time.sleep(2)
    raise RuntimeError(f"{device}: FRR did not start")


def push_device(device, info, project):
    cmds = info["commands"]
    if not cmds:
        return "(nothing to push)"
    bad = [c for c in cmds if not SAFE.match(c)]
    if bad:
        raise ValueError(f"{device}: refused, unsafe characters in {bad}")

    if info["kind"] == "router":
        daemons = [d for d, key in (("ospfd", "router ospf"), ("ripd", "router rip")) if key in cmds]
        _prepare_router(device, project, daemons)
        args = " ".join(f'-c "{c}"' for c in ["configure terminal"] + cmds + ["end", "write memory"])
        return _session(device, [f"vtysh {args}"], project, wait=20)
    return _session(device, cmds, project)          # switch (shell) and pc (VPCS)


def backup_routers(state, project):
    os.makedirs("backups", exist_ok=True)
    for n in state["topology"]["nodes"]:
        if kind(n) == "router":
            with open(os.path.join("backups", f'{n["name"]}.conf'), "w") as f:
                f.write(run_show(n["name"], "show running-config", project))
    print("  router configs saved in ./backups")


def _wait(check, timeout=70, step=5):
    end = time.time() + timeout
    while True:
        ok, detail = check()
        if ok or time.time() > end:
            return ok, detail
        time.sleep(step)


def verify(state, project="test1"):
    """Objective tests on the real devices. Returns [{"check", "ok", "detail"}]."""
    report = []
    pcs = [n["name"] for n in state["topology"]["nodes"] if kind(n) == "pc"]
    ip_of = lambda dev: next(v["ip"].split("/")[0] for k, v in state["ips"].items() if k.startswith(dev + "|"))

    for router, cfg in state["routing"].items():
        if "ospf" in cfg:
            ok, out = _wait(lambda: (lambda o: ("Full" in o, o))(run_show(router, "show ip ospf neighbor", project)))
            report.append({"check": f"{router}: OSPF neighbor Full", "ok": ok, "detail": out[-300:]})

    for pc in pcs:
        targets = {state["ips"][k]["gateway"] for k in state["ips"] if k.startswith(pc + "|") and state["ips"][k]["gateway"]}
        targets |= {ip_of(o) for o in pcs if o != pc}
        for t in sorted(targets):
            ok, out = _wait(lambda: (lambda o: ("bytes from" in o, o))(run_show(pc, f"ping {t}", project)), timeout=15, step=2)
            report.append({"check": f"{pc} -> {t}", "ok": ok, "detail": out[-200:]})
    return report


def deploy(state, project="test1", approved=False):
    if not approved:
        raise PermissionError("Deployment needs human approval (approved=True).")
    cfg = generate_config(state)
    order = {"switch": 0, "router": 1, "pc": 2}
    print("Backing up...")
    backup_routers(state, project)
    for dev, info in sorted(cfg.items(), key=lambda kv: order[kv[1]["kind"]]):
        print(f"Pushing {dev} ({len(info['commands'])} commands)")
        push_device(dev, info, project)
    print("Verifying (OSPF can need up to a minute)...")
    return verify(state, project)


if __name__ == "__main__":
    from confico_core import demo_state

    state = demo_state()
    plan = generate_config(state)
    print("\nPLAN:", {d: len(i["commands"]) for d, i in plan.items()}, "commands per device")
    if input("Type YES to deploy to the lab: ").strip() == "YES":
        for r in deploy(state, approved=True):
            print("PASS" if r["ok"] else "FAIL", "-", r["check"])
            if not r["ok"]:
                print("     ", r["detail"].strip().replace("\n", "\n      "))
    else:
        print("Cancelled, nothing was changed.")
