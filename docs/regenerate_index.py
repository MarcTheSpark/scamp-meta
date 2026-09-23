#  ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++  #
#  This file is part of SCAMP (Suite for Computer-Assisted Music in Python)                      #
#  Copyright © 2020 Marc Evanstein <marc@marcevanstein.com>.                                     #
#                                                                                                #
#  This program is free software: you can redistribute it and/or modify it under the terms of    #
#  the GNU General Public License as published by the Free Software Foundation, either version   #
#  3 of the License, or (at your option) any later version.                                      #
#                                                                                                #
#  This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY;     #
#  without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.     #
#  See the GNU General Public License for more details.                                          #
#                                                                                                #
#  You should have received a copy of the GNU General Public License along with this program.    #
#  If not, see <http://www.gnu.org/licenses/>.                                                   #
#  ++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++  #
"""
Build the example gallery model, and regenerate INDEX.md from it.

The examples live in each package's ``examples/`` dir, kept in topic folders for browsing.
``examples.yaml`` (beside this file) is the single source of truth for how they are grouped
and ordered in the docs: a tutorial sequence, then topics -> sections -> ordered example
refs. A ref ``{package: name}`` is an example in that section's folder; ``{package: path}``
(a path from ``<package>/examples/``) cross-lists one that lives elsewhere. Examples present
in a folder but not listed in the YAML are appended to their section; whole folders the YAML
never mentions are appended after the listed topics/sections. The JunkDrawer is ignored.

``build_gallery()`` is the shared model the docs build (build_examples_docs.py) reads too.
Run from anywhere:  python3 regenerate_index.py     (requires scamp importable + PyYAML)
"""

import re
import sys
import pathlib
from pathlib import PurePosixPath
from collections import OrderedDict

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is required (pip install pyyaml).")

HERE = pathlib.Path(__file__).resolve().parent          # the workspace docs/ dir
REPO_ROOT = HERE.parent
YAML_PATH = HERE / "examples.yaml"
INDEX_PATH = HERE / "INDEX.md"

# Packages whose examples/ dirs feed the gallery, in listing order.
PACKAGES = OrderedDict([
    ("scamp", REPO_ROOT / "scamp" / "examples"),
    ("scamp_extensions", REPO_ROOT / "scamp_extensions" / "examples"),
])
# Source links per package (branch each tracks).
GITHUB = {
    "scamp": ("https://github.com/MarcTheSpark/scamp", "main"),
    "scamp_extensions": ("https://github.com/MarcTheSpark/scamp_extensions", "main"),
}

TUTORIAL_DIR = "Tutorial"                # the curated teaching sequence, all in scamp
EXCLUDE = {"JunkDrawer", "__pycache__"}  # scratch / caches, never in the gallery

DOCSTRING_RE = re.compile(r'"""(.*?)"""', re.DOTALL)
TITLE_RE = re.compile(r"SCAMP Example:\s*(.+)", re.IGNORECASE)


# ---- docstring / title metadata -------------------------------------------

def slugify(text):
    """A flat, URL-safe token from a path or title."""
    return re.sub(r"[^0-9a-z]+", "_", text.lower()).strip("_")


def parse_docstring(path):
    """The summary from a script's first docstring (its 'SCAMP Example:' title line
    dropped), or '' if there is none."""
    m = DOCSTRING_RE.search(path.read_text())
    if not m:
        return ""
    body = [ln.strip() for ln in m.group(1).strip().splitlines()
            if not ln.strip().startswith(("SCAMP Example:", "SCAMP EXAMPLE:"))]
    return " ".join(ln for ln in body if ln).strip()


def title_of(path):
    """The 'SCAMP Example: ...' title if present, else a de-underscored file stem, with any
    trailing 'Example' dropped."""
    m = TITLE_RE.search(path.read_text())
    title = m.group(1).strip() if m else path.stem.replace("_", " ")
    return re.sub(r"\s+example$", "", title, flags=re.IGNORECASE)


def parse_about(folder):
    """(title, summary) from a folder's about.txt, or None if absent."""
    about = folder / "about.txt"
    if not about.exists():
        return None
    title, body = None, []
    for ln in (ln.strip() for ln in about.read_text().splitlines()):
        if ln.startswith("SCAMP Example:"):
            title = ln[len("SCAMP Example:"):].strip()
        else:
            body.append(ln)
    return title, " ".join(ln for ln in body if ln).strip()


# ---- example units --------------------------------------------------------

def _folder_pys(d):
    return sorted(p for p in d.rglob("*.py") if "__pycache__" not in p.parts)


def make_unit(package, rel, is_tutorial=False):
    """An example unit: its package, its path within that package's examples/ (a .py file or
    a folder), and its title/summary. Deduplicated by ``key`` so a cross-listed example is one
    unit shown in several places."""
    path = PACKAGES[package] / rel
    is_folder = path.is_dir()
    pys = _folder_pys(path) if is_folder else [path]
    if is_folder:
        main = next((p for p in pys if p.stem == path.name), None)
        about = parse_about(path)
        if about:
            title, summary = about[0] or path.name.replace("_", " ").title(), about[1]
        elif len(pys) == 1:
            title, summary = title_of(pys[0]), parse_docstring(pys[0])
        elif main:
            title, summary = title_of(main), ""
        else:
            title, summary = path.name.replace("_", " ").title(), ""
    else:
        title, summary = title_of(path), parse_docstring(path)
    return {
        "package": package, "rel": rel, "key": f"{package}:{rel}", "path": path,
        "is_folder": is_folder, "is_tutorial": is_tutorial,
        "title": title, "summary": summary, "py_paths": pys,
        "needs_ext": package == "scamp_extensions",
    }


def _section_children(section_dir):
    """(name, is_folder) for each example unit directly in a section dir: .py files, and
    subfolders that contain .py (folder examples). Sorted; caches/JunkDrawer skipped."""
    out = []
    if not section_dir.is_dir():
        return out
    for c in sorted(section_dir.iterdir(), key=lambda p: p.name):
        if c.name in EXCLUDE:
            continue
        if c.is_file() and c.suffix == ".py":
            out.append((c.name, False))
        elif c.is_dir() and any("__pycache__" not in p.parts for p in c.rglob("*.py")):
            out.append((c.name, True))
    return out


def _rel_for(ref, topic, section):
    """The path within a package's examples/ for a YAML ref: a bare name lives in this
    section's folder; a ref containing '/' is already a full path (a cross-listing)."""
    if "/" in ref:
        return ref
    return f"{topic}/{section}/{ref}" if section else f"{topic}/{ref}"


# ---- the gallery model ----------------------------------------------------

def build_gallery():
    """Read examples.yaml + the package folders into the ordered gallery model:
      tutorial:  [unit, ...]                       (teaching order)
      topics:    {topic: {section: [unit, ...]}}   (YAML order, then unlisted appended)
      locations: {unit key: [(topic, section_or_None), ...]}   (every place it's listed)
      units:     {key: unit}
    """
    data = yaml.safe_load(YAML_PATH.read_text()) or {}
    units, locations = {}, {}

    def get_unit(package, rel, tut=False):
        u = units.get(f"{package}:{rel}")
        if u is None:
            u = make_unit(package, rel, tut)
            units[u["key"]] = u
        return u

    tutorial = [get_unit("scamp", f"{TUTORIAL_DIR}/{ref}", True)
                for ref in (data.get("tutorial") or [])]

    # topics/sections present on disk (any package), to append what the YAML omits.
    fs = OrderedDict()                       # topic -> ordered set of section names ("" = loose)
    for base in PACKAGES.values():
        if not base.is_dir():
            continue
        for topic_dir in sorted(base.iterdir(), key=lambda p: p.name):
            if not topic_dir.is_dir() or topic_dir.name in EXCLUDE or topic_dir.name == TUTORIAL_DIR:
                continue
            secs = fs.setdefault(topic_dir.name, OrderedDict())
            if any(c.is_file() and c.suffix == ".py" for c in topic_dir.iterdir()):
                secs.setdefault("", None)    # loose .py examples directly under the topic
            for sec_dir in sorted(topic_dir.iterdir(), key=lambda p: p.name):
                if sec_dir.is_dir() and sec_dir.name not in EXCLUDE:
                    secs.setdefault(sec_dir.name, None)

    yaml_topics = data.get("topics") or {}
    topic_order = list(yaml_topics) + [t for t in fs if t not in yaml_topics]

    topics = OrderedDict()
    for topic in topic_order:
        yaml_secs = yaml_topics.get(topic) or {}
        sec_order = list(yaml_secs) + [s for s in fs.get(topic, ()) if s not in yaml_secs]
        sections = OrderedDict()
        for sec in sec_order:
            secname = "" if sec == "_" else sec
            listed, seen = [], set()

            def add(package, rel):
                if (package, rel) in seen:
                    return
                u = get_unit(package, rel)
                listed.append(u)
                seen.add((package, rel))
                locations.setdefault(u["key"], []).append((topic, secname or None))

            for entry in (yaml_secs.get(sec) or []):
                (package, ref), = entry.items()
                add(package, _rel_for(ref, topic, secname))
            for package in PACKAGES:
                if secname:                                 # a section: its files + folder examples
                    for name, _ in _section_children(PACKAGES[package] / topic / secname):
                        add(package, f"{topic}/{secname}/{name}")
                else:                                        # loose: only .py files (subdirs are sections)
                    tdir = PACKAGES[package] / topic
                    for c in sorted(tdir.iterdir(), key=lambda p: p.name) if tdir.is_dir() else []:
                        if c.is_file() and c.suffix == ".py" and c.name not in EXCLUDE:
                            add(package, f"{topic}/{c.name}")
            sections[secname] = listed
        topics[topic] = sections

    return {"tutorial": tutorial, "topics": topics, "locations": locations, "units": units}


# ---- public scamp API scan (for the INDEX API lines) ----------------------

def collect_api_names():
    import scamp
    callables, classes = set(), set()
    for name in dir(scamp):
        if name.startswith("_"):
            continue
        obj = getattr(scamp, name)
        if isinstance(obj, type):
            classes.add(name)
        elif callable(obj):
            callables.add(name)
    for cls_name in ("Session", "ScampInstrument", "Ensemble", "Clock", "Performance",
                     "PerformancePart", "Score", "Envelope", "NoteHandle", "ChordHandle"):
        cls = getattr(scamp, cls_name, None)
        if cls is not None:
            callables.update(n for n in vars(cls) if not n.startswith("_"))
    return callables, classes


def scan_api(paths, callables, classes, limit=12):
    text = "\n".join(p.read_text() for p in paths)
    used = {n for n in callables if re.search(rf"\b{re.escape(n)}\s*\(", text)}
    used |= {n for n in classes if re.search(rf"\b{re.escape(n)}\b", text)}
    listed = sorted(used)
    return listed[:limit] + ["..."] if len(listed) > limit else listed


# ---- INDEX.md -------------------------------------------------------------

HEADER = """\
# SCAMP Examples Index

<!-- GENERATED FILE - do not edit. Regenerate with: python3 docs/regenerate_index.py -->

A map of every example across the SCAMP packages, for humans and AI assistants alike. The
gallery's grouping and order live in `docs/examples.yaml`; the example files live in each
package's `examples/` dir. An example tagged *(scamp_extensions)* ships in that package.
"""


def main():
    gallery = build_gallery()
    callables, classes = collect_api_names()
    locations = gallery["locations"]
    out = [HEADER, "\n## Tutorial\n", "The curated teaching set -- work through it in order.\n"]

    def emit(u, homed):
        tag = " *(scamp_extensions)*" if u["needs_ext"] else ""
        if not homed:                              # a cross-listing: one line, points home
            out.append(f"- {u['title']}{tag} — *(home: `{u['package']}:{u['rel']}`)*")
            return
        out.append(f"- **{u['title']}**{tag} — `{u['package']}:{u['rel']}`")
        details = [u["summary"]] if u["summary"] else []
        api = scan_api(u["py_paths"], callables, classes)
        if api:
            details.append("*API:* " + ", ".join(f"`{a}`" for a in api))
        elsewhere = [f"{t} › {s}" if s else t for t, s in locations.get(u["key"], [])]
        if len(elsewhere) > 1:
            details.append("*also under:* " + ", ".join(elsewhere))
        if details:
            out.append("  <br>" + " — ".join(details))

    for u in gallery["tutorial"]:
        emit(u, True)

    for topic, sections in gallery["topics"].items():
        out.append(f"\n## {topic}\n")
        for section, us in sections.items():
            if section:
                out.append(f"### {section}\n")
            for u in us:
                hp = PurePosixPath(u["rel"]).parts     # home = the folder the example lives in
                home = (hp[0], hp[1] if len(hp) > 2 else "")
                emit(u, (topic, section) == home)
            out.append("")

    INDEX_PATH.write_text("\n".join(out) + "\n")
    n = len(gallery["units"])
    print(f"INDEX.md: {n} examples across {len(gallery['topics'])} topics")


if __name__ == "__main__":
    main()
