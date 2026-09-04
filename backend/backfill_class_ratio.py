import argparse
from pathlib import Path

from applications.kml_roi.tiles import scan_year_masks
from applications.kml_roi.change_matrix import write_class_ratio_json


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output_root", required=True, help="miner/change_matrix_outputs directory")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    root = Path(args.output_root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(str(root))

    count = 0
    for d in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name):
        fid = d.name
        year_masks = scan_year_masks(fid, d)
        if not year_masks:
            continue
        ok = write_class_ratio_json(fid=fid, year_masks=year_masks, out_dir=d)
        if ok:
            count += 1
        if args.limit and count >= args.limit:
            break

    print({"written_fids": count})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

