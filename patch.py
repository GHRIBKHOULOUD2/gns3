import re, pathlib
p = pathlib.Path("apply_config.py")
s = p.read_text(encoding="utf-8")

# 1) never skip PCs
old = 'if state.get("baseline", {}).get("ips", {}).get(key) == state["ips"][key]:'
new = 'if kind(nodes[r["device"]]) != "pc" and state.get("baseline", {}).get("ips", {}).get(key) == state["ips"][key]:'
if old in s:
    s = s.replace(old, new)
    print("1 OK: PCs are never skipped now")
else:
    print("1 skipped: the baseline check is not in your file (nothing to fix there)")

# 2) _vtysh: prepare when vtysh.conf is missing or FRR is down
fixed = '''def _vtysh(device, cmds, project):
    out = _session(device, ['echo FRR_$(test -f /etc/frr/vtysh.conf && vtysh -c "show version" | grep -q FRRouting && echo READY || echo NO)'], project)
    if "FRR_READY" not in out:
        _prepare_router(device, project)          # vtysh.conf missing or FRR down
    args = " ".join(f'-c "{c}"' for c in ["configure terminal"] + cmds + ["end", "write memory"])
    return _session(device, [f"vtysh {args}"], project, wait=20)
'''
s, n = re.subn(r"def _vtysh\(device, cmds, project\):\n(?:    .*\n)+", lambda m: fixed, s, count=1)
print("2 OK: _vtysh replaced" if n else "2 NOT FOUND: _vtysh")

p.write_text(s, encoding="utf-8")
