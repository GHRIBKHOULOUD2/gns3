"""
gns3_client.py - first tool of the Confico agent.
Reads nodes and links of a GNS3 project through the REST API.

Credentials are read from the GNS3 server ini file (or env vars),
so the password never appears in the code.
"""
import configparser
import json
import os
import sys

import requests

BASE_URL = os.environ.get("GNS3_URL", "http://127.0.0.1:3080/v2")
INI_PATH = os.path.join(os.environ.get("APPDATA", ""), "GNS3", "2.2", "gns3_server.ini")


def _auth():
    """Return (user, password) from env vars or from gns3_server.ini."""
    user = os.environ.get("GNS3_USER")
    password = os.environ.get("GNS3_PASSWORD")
    if user and password:
        return user, password
    cfg = configparser.ConfigParser()
    cfg.read(INI_PATH)
    return cfg["Server"]["user"], cfg["Server"]["password"]


def _get(path):
    r = requests.get(f"{BASE_URL}{path}", auth=_auth(), timeout=30)
    r.raise_for_status()
    return r.json()


def find_project_id(name):
    """Look up a project id by its name."""
    for p in _get("/projects"):
        if p["name"] == name:
            return p["project_id"]
    raise ValueError(f"Project '{name}' not found")


def get_topology(project_name="test1"):
    """
    TOOL: returns a compact description of the lab the LLM can read.
    {
      "project": "test1",
      "nodes": [{"name", "type", "status", "console_type", "console_host", "console_port"}],
      "links": ["frr-1:eth0 <-> OpenvSwitch-1:eth0", ...]
    }
    """
    pid = find_project_id(project_name)
    nodes_raw = _get(f"/projects/{pid}/nodes")
    links_raw = _get(f"/projects/{pid}/links")

    by_id = {n["node_id"]: n for n in nodes_raw}

    def port_name(node, adapter, port):
        for p in node.get("ports", []):
            if p["adapter_number"] == adapter and p["port_number"] == port:
                return p["name"]
        return f"{adapter}/{port}"

    nodes = [
        {
            "name": n["name"],
            "type": n["node_type"],
            "status": n["status"],
            "console_type": n.get("console_type"),
            "console_host": n.get("console_host"),
            "console_port": n.get("console"),
        }
        for n in nodes_raw
    ]

    links = []
    for l in links_raw:
        ends = []
        for end in l["nodes"]:
            node = by_id[end["node_id"]]
            ends.append(f'{node["name"]}:{port_name(node, end["adapter_number"], end["port_number"])}')
        links.append(" <-> ".join(ends))

    return {"project": project_name, "nodes": nodes, "links": links}


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "test1"
    print(json.dumps(get_topology(name), indent=2))
