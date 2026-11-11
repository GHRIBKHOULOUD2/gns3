"""
console.py - second tool of the Confico agent: run_show(device, command)

Connects to a node's telnet console (host/port come from get_topology),
runs ONE read-only command and returns its output as text.
Uses plain sockets, so it works on Python 3.13 (no telnetlib needed).

Read-only on purpose: only 'show' and 'ping' commands are allowed.
Configuration changes will go through a separate, validated tool later.
"""
import re
import socket
import sys
import time

from gns3_client import get_topology

_TOPO = {}


def _topology(project, ttl=30):
    """One GNS3 API call per 30 seconds instead of one per command."""
    hit = _TOPO.get(project)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    topo = get_topology(project)
    _TOPO[project] = (time.time(), topo)
    return topo

IAC, DONT, DO, WONT, WILL, SB, SE = 255, 254, 253, 252, 251, 250, 240
# Shell prompt of the Docker nodes ("/ # ") or VPCS prompt ("PC1> ")
PROMPT = re.compile(r"(?:/ # |\S+:\S*# |[A-Za-z0-9_-]+(?:\([a-z0-9-]+\))?[#>] )$")  # shell, renamed shell, vtysh, VPCS
VTY = re.compile(r"[A-Za-z0-9_-]+(?:\([a-z0-9-]+\))?# $")                   # vtysh only
ALLOWED = ("show", "ping")


def _clean(sock, data):
    """Strip telnet negotiation bytes (and politely refuse every option)."""
    out, i = bytearray(), 0
    while i < len(data):
        b = data[i]
        if b != IAC:
            out.append(b)
            i += 1
            continue
        cmd = data[i + 1] if i + 1 < len(data) else None
        if cmd == IAC:
            out.append(IAC)
            i += 2
        elif cmd in (DO, WILL):
            opt = data[i + 2] if i + 2 < len(data) else 0
            sock.sendall(bytes([IAC, WONT if cmd == DO else DONT, opt]))
            i += 3
        elif cmd in (DONT, WONT):
            i += 3
        elif cmd == SB:
            end = data.find(bytes([IAC, SE]), i)
            i = len(data) if end == -1 else end + 2
        else:
            i += 2
    return bytes(out)


def _read_until_prompt(sock, timeout=10.0):
    buf, deadline = "", time.time() + timeout
    sock.settimeout(1.0)
    while time.time() < deadline:
        try:
            chunk = sock.recv(4096)
        except socket.timeout:
            chunk = b""
        if chunk:
            buf += _clean(sock, chunk).decode("utf-8", errors="replace")
            if PROMPT.search(buf.replace("\r", "")):
                return buf
        elif PROMPT.search(buf.replace("\r", "")):
            return buf
    return buf  # timed out: return what we have


def open_console(host, port):
    """Connect and make sure we sit at the plain shell (leave vtysh if a past session left it open)."""
    try:
        s = socket.create_connection((host, port), timeout=5)
    except OSError as e:
        raise ConnectionError(f"console {host}:{port} refuses the connection - is this node fully started in GNS3? ({e.strerror or e})")
    s.sendall(b"\r\n")
    first = _read_until_prompt(s, 5).replace("\r", "")
    if VTY.search(first):
        s.sendall(b"end\r\n")
        _read_until_prompt(s, 5)
        s.sendall(b"exit\r\n")
        _read_until_prompt(s, 5)
    return s


def run_show(device, command, project="test1"):
    """
    TOOL: run a read-only command on a device and return the output.
    Examples:
      run_show("frrouting-frr-1", "show ip route")
      run_show("PC1", "ping 192.168.10.12")
    """
    command = command.strip()
    if command.split()[0].lower() not in ALLOWED:
        return f"REFUSED: run_show only allows read-only commands {ALLOWED}"

    topo = _topology(project)
    node = next((n for n in topo["nodes"] if n["name"] == device), None)
    if node is None:
        names = [n["name"] for n in topo["nodes"]]
        return f"ERROR: unknown device '{device}'. Available: {names}"
    if node["status"] != "started":
        return f"ERROR: {device} is {node['status']}, not started"

    # What we actually type into the console
    if node["type"] == "docker":
        if command.startswith("show"):
            line = f'vtysh -c "{command}"'          # FRR CLI from the shell
        else:
            line = command.replace("ping ", "ping -c 3 -W 1 ", 1)
    else:
        line = command                              # VPCS understands it directly

    with open_console(node["console_host"], node["console_port"]) as s:
        s.sendall(line.encode() + b"\r\n")
        raw = _read_until_prompt(s, 15)

    # Remove carriage returns, the echoed command line and the final prompt
    lines = raw.replace("\r", "").split("\n")
    only_prompt = re.compile(r"^(/ #|\S+:\S*#|[A-Za-z0-9_-]+(\([a-z0-9-]+\))?[#>])$")
    lines = [l for l in lines if l.strip() and line not in l and not only_prompt.match(l.strip())]
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print('usage: python console.py <device> "<command>"')
        sys.exit(1)
    print(run_show(sys.argv[1], sys.argv[2]))
