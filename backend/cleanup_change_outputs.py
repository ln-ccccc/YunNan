"""清理变化检测成果目录中超出保留年限的历史年份产物。

默认 dry-run 只预览不删除；确认无误后加 --apply 才执行。
"""
import argparse
from pathlib import Path

from applications.kml_roi.tiles import _cleanup_keep_set, cleanup_output_dir


def _preview_output_dir(fid: str, fid_dir: Path, keep_last_years: int = 3) -> list:
    """返回将被删除的文件名列表，不做任何删除（保留集与 cleanup_output_dir 同源）。"""
    keep = _cleanup_keep_set(fid, fid_dir, keep_last_years)
    return sorted(p.name for p in fid_dir.iterdir() if p.is_file() and p.name not in keep)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output_root", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--fid", default="")
    ap.add_argument("--keep_last_years", type=int, default=3)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="实际执行删除；缺省 dry-run 只预览待删清单（2026-09-20 审查加固）",
    )
    args = ap.parse_args()

    root = Path(args.output_root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(str(root))

    removed_total = 0
    visited = 0

    if args.fid:
        d = root / str(args.fid)
        if d.exists() and d.is_dir():
            if args.apply:
                removed_total += cleanup_output_dir(str(args.fid), d, keep_last_years=args.keep_last_years)
            else:
                would_remove = _preview_output_dir(str(args.fid), d, keep_last_years=args.keep_last_years)
                print({"dry_run": True, "fid": str(args.fid), "would_remove": would_remove})
                return 0
            visited = 1
        print({"visited": visited, "removed": removed_total})
        return 0

    for d in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name):
        fid = d.name
        if args.apply:
            removed_total += cleanup_output_dir(fid, d, keep_last_years=args.keep_last_years)
        else:
            would_remove = _preview_output_dir(fid, d, keep_last_years=args.keep_last_years)
            if would_remove:
                print({"dry_run": True, "fid": fid, "would_remove": would_remove})
        visited += 1
        if args.limit and visited >= args.limit:
            break

    print({"visited": visited, "removed": removed_total} if args.apply else {"dry_run": True, "visited": visited})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
