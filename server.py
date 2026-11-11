"""
server.py - backend API for the Configo front end (no agent yet).

Run:   pip install fastapi uvicorn
       uvicorn server:app --reload
Test the API without any front end:  http://127.0.0.1:8000/docs

configo.html (or index.html), topology.js and your images are served from this same folder
at http://127.0.0.1:8000/   (only .html .js .css and image files, never your .py files)
Every POST that saves data returns {"ok": true/false, "errors": [...]}.
"""
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse

import apply_config
import confico_core as core
from deploy import deploy as run_deploy

HERE = Path(__file__).parent
app = FastAPI(title="Configo")
LAB = {"state": None}          # one lab, one user (enough for the project)


def _state():
    if LAB["state"] is None:
        raise HTTPException(409, "Upload a .gns3 file or load the live topology first")
    return LAB["state"]


def _overview(s):
    return {"topology": s["topology"], "devices": core.device_rows(s), "interfaces": core.interface_rows(s)}


# ------------------------------------------------------------- page 1: upload
@app.post("/api/topology/import")
def import_file(data: dict = Body(...)):
    """Body = the JSON content of the .gns3 file (the front end reads the file and posts its text)."""
    try:
        LAB["state"] = core.import_topology(data)
    except (KeyError, ValueError, TypeError) as e:
        raise HTTPException(400, f"Not a valid .gns3 file: {e!r}")
    return _overview(LAB["state"])


@app.post("/api/topology/live")
def import_live(project: str = "test1"):
    """Read the running lab straight from GNS3."""
    try:
        LAB["state"] = core.import_topology(project=project)
    except Exception as e:
        raise HTTPException(502, f"GNS3 not reachable or project not found: {e}")
    return _overview(LAB["state"])


@app.get("/api/state")
def get_state():
    """Everything saved so far (passwords are hidden)."""
    s = _state()
    return {**s, "identity": {d: {**v, "password": "***" if v.get("password") else ""} for d, v in s["identity"].items()}}


# ------------------------------------------------------------- wizard pages
@app.post("/api/identity")
def identity(rows: list[dict] = Body(...)):
    return core.set_identity(_state(), rows)


@app.post("/api/ips")
def ips(rows: list[dict] = Body(...)):
    return core.set_ips(_state(), rows)


@app.post("/api/routing/ospf")
def ospf(rows: list[dict] = Body(...)):
    return core.set_ospf(_state(), rows)


@app.post("/api/routing/rip")
def rip(rows: list[dict] = Body(...)):
    return core.set_rip(_state(), rows)


@app.post("/api/routing/static")
def static(rows: list[dict] = Body(...)):
    return core.set_static(_state(), rows)


@app.post("/api/switching/vlans")
def vlans(data: dict = Body(...)):
    """Body: {"device": "OpenvSwitch-1", "vlans": [{"id": 10, "name": "Sales"}],
              "ports": [{"interface": "eth1", "vlan_id": "10", "mode": "access"}]}"""
    return core.set_vlans(_state(), data.get("device"), data.get("vlans", []), data.get("ports", []))


# ------------------------------------------------------------- final step
@app.get("/api/config")
def preview_config():
    """The commands that would be sent to each device (nothing is changed)."""
    return core.generate_config(_state())


@app.post("/api/deploy")
def deploy(data: dict = Body(...)):
    """Body: {"approved": true}. Pushes the config to GNS3, then returns the verification report.
    Takes up to a minute (OSPF needs time to converge)."""
    if data.get("approved") is not True:
        raise HTTPException(400, "Deployment needs explicit approval: send {\"approved\": true}")
    s = _state()
    try:
        return run_deploy(s, project=s["topology"]["project"], approved=True)
    except Exception as e:
        raise HTTPException(500, f"Deployment failed: {e}")


# ------------------------------------------------------------- read / apply (Identity and IP pages)
@app.get("/api/current")
def current():
    """Real hostnames, IPs and gateways read from the running devices (takes some seconds)."""
    return apply_config.read_current(_state())


@app.post("/api/apply/identity")
def apply_identity(rows: list[dict] = Body(...)):
    """Validate, then set hostname / password on the devices."""
    return apply_config.apply_identity(_state(), rows)


@app.post("/api/apply/ips")
def apply_ips(rows: list[dict] = Body(...)):
    """Validate, then set IP address and gateway on the devices."""
    return apply_config.apply_ips(_state(), rows)


@app.post("/api/apply/ospf")
def apply_ospf(rows: list[dict] = Body(...)):
    """Validate, then configure OSPF on the routers."""
    return apply_config.apply_ospf(_state(), rows)


@app.post("/api/apply/rip")
def apply_rip(rows: list[dict] = Body(...)):
    """Validate, then configure RIP on the routers."""
    return apply_config.apply_rip(_state(), rows)


@app.post("/api/apply/static")
def apply_static(rows: list[dict] = Body(...)):
    """Validate, then configure static routes on the routers."""
    return apply_config.apply_static(_state(), rows)


@app.get("/api/current/vlans")
def current_vlans(device: str):
    """VLAN ids and port modes really configured on a switch."""
    return apply_config.read_vlans(_state(), device)


@app.post("/api/apply/vlans")
def apply_vlans(data: dict = Body(...)):
    """Validate, then set the VLANs / port modes on the switch."""
    return apply_config.apply_vlans(_state(), data)


# ------------------------------------------------------------- the web page (must stay last)
FILE_TYPES = {".html", ".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico"}


@app.get("/")
def home():
    for name in ("configo.html", "index.html"):
        if (HERE / name).is_file():
            return FileResponse(HERE / name)
    raise HTTPException(404, "configo.html not found next to server.py")


@app.get("/{name}")
def asset(name: str):
    path = HERE / name
    if path.suffix.lower() in FILE_TYPES and path.is_file():
        return FileResponse(path)
    raise HTTPException(404, "Not found")
