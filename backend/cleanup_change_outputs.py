import argparse
from pathlib import Path

from applications.kml_roi.tiles import cleanup_output_dir


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output_root", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--fid", default="")
    ap.add_argument("--keep_last_years", type=int, default=3)
    args = ap.parse_args()

    root = Path(args.output_root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(str(root))

    removed_total = 0
    visited = 0

    if args.fid:
        d = root / str(args.fid)
        if d.exists() and d.is_dir():
            removed_total += cleanup_output_dir(str(args.fid), d, keep_last_years=args.keep_last_years)
            visited = 1
        print({"visited": visited, "removed": removed_total})
        return 0

    for d in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name):
        fid = d.name
        removed_total += cleanup_output_dir(fid, d, keep_last_years=args.keep_last_years)
        visited += 1
        if args.limit and visited >= args.limit:
            break

    print({"visited": visited, "removed": removed_total})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
