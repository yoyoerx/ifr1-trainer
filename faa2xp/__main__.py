"""CLI: python -m faa2xp {build,install,restore,status} --xplane "E:/.../X-Plane 12" """

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from datasrc import faa as faa_src

from . import install as inst
from .extract import extract
from .merge import build_all

_TABLE = ("earth_nav.dat", "earth_fix.dat", "earth_awy.dat", "earth_hold.dat")


def _cycle_dir(root: Path, cycle: str | None) -> tuple[str, Path]:
    if cycle is None:
        m = faa_src.newest_cached_manifest(root)
        if m is None:
            raise SystemExit("no cached FAA cycle: run `python -m datasrc.faa update` first")
        cycle = m.cycle
    d = root / "faa" / cycle
    if not (d / "FAACIFP18").exists():
        raise SystemExit(f"{d / 'FAACIFP18'} not found")
    return cycle, d


def cmd_build(args) -> Path:
    cycle, d = _cycle_dir(Path(args.data), args.cycle)
    out = Path(args.out) if args.out else Path(args.data) / "xplane_out" / cycle
    ex = extract(d / "FAACIFP18")
    stats = build_all(inst.default_dir(Path(args.xplane)), ex, out, cycle)
    for name, s in stats.items():
        print(f"{name:16} kept {s['kept']:>7}  replaced {s['dropped']:>7}  added {s['added']:>7}")
    for n in ex.notes[:5]:
        print("note:", n)
    print(f"staged in {out}")
    return out


def cmd_install(args) -> None:
    cycle, d = _cycle_dir(Path(args.data), args.cycle)
    out = Path(args.out) if args.out else Path(args.data) / "xplane_out" / cycle
    if not all((out / n).exists() for n in _TABLE):
        out = cmd_build(args)
    files = {n: out / n for n in _TABLE}
    files["FAACIFP18"] = d / "FAACIFP18"
    for line in inst.install(Path(args.xplane), files, cycle, apply=args.yes):
        print(line)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="faa2xp", description=__doc__)
    ap.add_argument("command", choices=["build", "install", "restore", "status"])
    ap.add_argument("--xplane", required=True, help="X-Plane 12 install folder")
    ap.add_argument("--data", default=str(faa_src.default_data_root()), help="trainer data root")
    ap.add_argument("--cycle", help="AIRAC cycle (default: newest cached)")
    ap.add_argument("--out", help="staging folder (default: <data>/xplane_out/<cycle>)")
    ap.add_argument("--yes", action="store_true", help="apply install/restore (default is a dry run)")
    args = ap.parse_args(argv)
    try:
        if args.command == "build":
            cmd_build(args)
        elif args.command == "install":
            cmd_install(args)
        elif args.command == "restore":
            for line in inst.restore(Path(args.xplane), apply=args.yes):
                print(line)
        else:
            print(inst.status(Path(args.xplane)))
    except inst.InstallError as e:
        print("error:", e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
