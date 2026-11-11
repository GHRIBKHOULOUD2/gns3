"""
merge_routing.py - adds the Routing pages (OSPF / RIP / static) INSIDE your existing Identity + IP files.

  page   (html)    <- routing.js is pasted inline, sidebar button "routing prot" is wired
  apply  (python)  <- the file that has apply_identity / apply_ips gets apply_ospf / apply_rip / apply_static
  server (python)  <- the file that has /api/apply/identity gets /api/apply/ospf, /rip, /static

Files are found by their CONTENT, so the names do not matter. Run in the project folder,
with routing.js and apply_routing.py next to this script:

    python merge_routing.py

If you have several candidates, or other names, give them yourself:

    python merge_routing.py --html my_page.html --apply my_apply.py --server my_server.py

Safe to run twice. Nothing is deleted: a .bak copy of every file is saved first.
"""
import argparse
import pathlib
import re
import shutil

ap = argparse.ArgumentParser()
ap.add_argument("--html"), ap.add_argument("--apply"), ap.add_argument("--server")
args = ap.parse_args()

HERE = pathlib.Path(__file__).resolve().parent if False else pathlib.Path(".")
read = lambda p: p.read_text(encoding="utf-8")


def find(given, pattern, suffix, label):
    if given:
        p = pathlib.Path(given)
        return p if p.is_file() else print(f"{label}: {given} not found") or None
    hits = [p for p in sorted(HERE.glob("*" + suffix)) if re.search(pattern, read(p)) and p.name not in ("merge_routing.py", "patch_routing.py", "apply_routing.py")]
    if len(hits) > 1:
        print(f"{label}: several candidates {[h.name for h in hits]}, using {hits[0].name} (use --{label} to choose)")
    if not hits:
        print(f"{label}: no file found (looking for {pattern!r})")
    return hits[0] if hits else None


def backup(p):
    b = p.with_suffix(p.suffix + ".bak")
    if not b.exists():
        shutil.copy(p, b)


page = find(args.html, r'id="rows"', ".html", "html")
apply = find(args.apply, r"def apply_identity", ".py", "apply")
server = find(args.server, r"/api/apply/identity", ".py", "server")
stem = apply.stem if apply else "apply_config"

# ---------------------------------------------------------------- 1) the web page
js = HERE / "routing.js"
if page:
    s = read(page)
    if "function netOf" in s:
        print(f"HTML: {page} already contains the routing code")
    elif not js.is_file():
        print("HTML: routing.js not found next to this script")
    else:
        need = ["function showPage", "function validate", "function interfaces", "function short", "function draw",
                "function msg", "async function api", 'id="thead"', 'id="title"', 'id="menu"', 'class="cols"', 'class="foot"']
        missing = [n for n in need if n not in s]
        if missing:
            print(f"HTML: {page} does not look like the Identity/IP page (missing {missing}). Nothing changed.")
        else:
            backup(page)
            inline = "<script>\n" + read(js) + "\n</script>"
            if '<script src="routing.js"></script>' in s:                  # patch_routing.py was run before
                s = s.replace('<script src="routing.js"></script>', inline)
            else:
                s = s.replace("</body>", inline + "\n</body>", 1)
            if 'data-p="routing"' not in s:
                if "<button>routing prot</button>" in s:
                    s = s.replace("<button>routing prot</button>",
                                  '<button data-p="routing" onclick="showPage(\'routing\')">routing prot</button>')
                else:
                    print('HTML: button "routing prot" not found: give it data-p="routing" onclick="showPage(\'routing\')" yourself')
            page.write_text(s, encoding="utf-8")
            print(f"HTML: routing code pasted inside {page}")

# ---------------------------------------------------------------- 2) python file with apply_identity / apply_ips
mod = HERE / "apply_routing.py"
if apply:
    s = read(apply)
    if "def apply_ospf" in s:
        print(f"APPLY: {apply} already contains the routing functions")
    elif not mod.is_file():
        print("APPLY: apply_routing.py not found next to this script")
    elif "def read_current" not in s:
        print(f"APPLY: {apply} has no read_current(): nothing changed")
    else:
        backup(apply)
        body = read(mod)
        body = body[body.index("def _devices"):]                           # drop docstring + imports of the module
        extra = ["from confico_core import set_ospf, set_rip, set_static"]
        if not re.search(r"^from console import .*\brun_show\b", s, re.M):
            extra.append("from console import run_show")
        if not re.search(r"^from deploy import .*\b_prepare_router\b", s, re.M):
            extra.append("from deploy import SAFE, _prepare_router, _session")
        if not re.search(r"^import re\b", s, re.M):
            extra.append("import re")
        s, n = re.subn(r"^(from confico_core import .*)$", lambda m: m.group(1) + "\n" + "\n".join(extra), s, count=1, flags=re.M)
        if not n:                                                          # no such line: put the imports at the very top
            s = "\n".join(extra) + "\n" + s
        s = s.rstrip() + "\n\n\n# ------------------------------------------------ pages: Routing (OSPF / RIP / static)\n" + body
        apply.write_text(s, encoding="utf-8")
        print(f"APPLY: routing functions appended to {apply}")

# ---------------------------------------------------------------- 3) python file with the endpoints
if server:
    s = read(server)
    if "/api/apply/ospf" in s and "apply_routing" not in s:
        print(f"SERVER: {server} already has the endpoints")
    else:
        backup(server)
        s = s.replace("import apply_routing\n", "")                        # undo patch_routing.py
        s = s.replace("apply_routing.", stem + ".")
        if "/api/apply/ospf" not in s:
            if not re.search(rf"^import {stem}\b|^from {stem} import", s, re.M):
                s = re.sub(r"^(import |from )", f"import {stem}\n" + r"\1", s, count=1, flags=re.M)
            block = f'''# ------------------------------------------------------------- routing pages (OSPF / RIP / static)
@app.post("/api/apply/ospf")
def apply_ospf(rows: list[dict] = Body(...)):
    return {stem}.apply_ospf(_state(), rows)


@app.post("/api/apply/rip")
def apply_rip(rows: list[dict] = Body(...)):
    return {stem}.apply_rip(_state(), rows)


@app.post("/api/apply/static")
def apply_static(rows: list[dict] = Body(...)):
    return {stem}.apply_static(_state(), rows)


'''
            i = s.find("# ------------------------------------------------------------- the web page")
            if i == -1:                                                    # else: before the catch-all route
                m = re.search(r'^@app\.get\("/\{name\}"\)', s, re.M)
                i = m.start() if m else len(s)
            s = s[:i] + block + s[i:]
        server.write_text(s, encoding="utf-8")
        print(f"SERVER: endpoints in {server} now call {stem}")

print("\nDone. Restart uvicorn and reload the page. Originals are saved as *.bak")
