"""Install staged files into ``<X-Plane>/Custom Data`` reversibly.

Anything already in ``Custom Data`` that we would overwrite is moved to
``Custom Data/.faa2xp-backup/`` first; ``faa2xp-manifest.json`` records what was
installed (with SHA-256s) so ``restore`` removes exactly our files and puts the
originals back. ``Resources/default data`` is never modified.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

MANIFEST = "faa2xp-manifest.json"
BACKUP = ".faa2xp-backup"


class InstallError(RuntimeError):
    pass


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def custom_dir(xp_root: Path) -> Path:
    d = Path(xp_root) / "Custom Data"
    if not (Path(xp_root) / "Resources" / "default data").is_dir():
        raise InstallError(f"{xp_root} does not look like an X-Plane 12 folder (no Resources/default data)")
    return d


def default_dir(xp_root: Path) -> Path:
    return Path(xp_root) / "Resources" / "default data"


def install(xp_root: Path, files: dict[str, Path], cycle: str, *, apply: bool) -> list[str]:
    """Copy ``files`` ({name in Custom Data -> source}) in. Returns the plan lines."""
    cd = custom_dir(xp_root)
    plan = []
    mf_path = cd / MANIFEST
    if mf_path.exists():
        raise InstallError("already installed - run `restore` first (or `status`)")
    backed = []
    for name, src in files.items():
        dst = cd / name
        if dst.exists():
            plan.append(f"backup   {dst} -> {BACKUP}/{name}")
            backed.append(name)
        plan.append(f"install  {src} ({src.stat().st_size:,} bytes) -> {dst}")
    if not apply:
        return ["[dry run: pass --yes to apply]"] + plan
    cd.mkdir(exist_ok=True)
    bdir = cd / BACKUP
    for name in backed:
        bdir.mkdir(exist_ok=True)
        shutil.move(str(cd / name), str(bdir / name))
    installed = {}
    for name, src in files.items():
        shutil.copyfile(src, cd / name)
        installed[name] = _sha(cd / name)
    (cd / MANIFEST).write_text(json.dumps(
        {"tool": "faa2xp", "cycle": cycle, "installed": installed, "backed_up": backed}, indent=2),
        encoding="utf-8")
    return plan


def restore(xp_root: Path, *, apply: bool) -> list[str]:
    cd = custom_dir(xp_root)
    mf_path = cd / MANIFEST
    if not mf_path.exists():
        raise InstallError("nothing to restore (no faa2xp manifest)")
    mf = json.loads(mf_path.read_text(encoding="utf-8"))
    plan = []
    for name, sha in mf["installed"].items():
        f = cd / name
        if not f.exists():
            plan.append(f"missing  {f} (already gone)")
        elif _sha(f) != sha:
            plan.append(f"KEEP     {f} (modified since install; not removed)")
        else:
            plan.append(f"remove   {f}")
    for name in mf["backed_up"]:
        plan.append(f"restore  {BACKUP}/{name} -> {cd / name}")
    if not apply:
        return ["[dry run: pass --yes to apply]"] + plan
    for name, sha in mf["installed"].items():
        f = cd / name
        if f.exists() and _sha(f) == sha:
            f.unlink()
    for name in mf["backed_up"]:
        b = cd / BACKUP / name
        if b.exists() and not (cd / name).exists():
            shutil.move(str(b), str(cd / name))
    bdir = cd / BACKUP
    if bdir.is_dir() and not any(bdir.iterdir()):
        bdir.rmdir()
    mf_path.unlink()
    return plan


def status(xp_root: Path) -> str:
    cd = custom_dir(xp_root)
    mf_path = cd / MANIFEST
    if not mf_path.exists():
        return "not installed"
    mf = json.loads(mf_path.read_text(encoding="utf-8"))
    return f"installed FAA cycle {mf['cycle']}: " + ", ".join(mf["installed"])
