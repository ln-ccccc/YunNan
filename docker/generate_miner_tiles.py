import glob
import os
import subprocess
from pathlib import Path


def parse_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def existing_zoom_levels(tile_root: Path) -> list[int]:
    levels = []
    if not tile_root.exists():
        return levels
    for child in tile_root.iterdir():
        if not child.is_dir():
            continue
        try:
            levels.append(int(child.name))
        except ValueError:
            continue
    return sorted(levels)


def resolve_source_tif() -> Path | None:
    configured = str(os.environ.get("MINER_TILE_TIF_PATH", "")).strip().rstrip("n")
    if configured:
        candidate = Path(configured)
        if candidate.exists() and candidate.is_file():
            return candidate
        if candidate.parent.exists():
            tif_candidates = sorted(candidate.parent.glob("*.tif"))
            level_candidates = [path for path in tif_candidates if "Level_15" in path.name]
            if level_candidates:
                return level_candidates[0]
            if len(tif_candidates) == 1:
                return tif_candidates[0]

    for pattern in ("/offline_maps/dali/*Level_15.tif", "/offline_maps/dali/*.tif"):
        matches = sorted(Path(path) for path in glob.glob(pattern))
        if matches:
            return matches[0]
    return None


def main() -> int:
    provider = str(os.environ.get("MINER_MAP_PROVIDER", "local")).strip().lower()
    if provider != "local":
        print(f"[miner-tiles] Skip generation because provider={provider}", flush=True)
        return 0

    tile_root = Path(os.environ.get("MINER_TILE_ROOT", "/app/miner/public/tiles"))
    min_zoom = parse_int("MINER_TILE_MIN_ZOOM", 8)
    max_zoom = parse_int("MINER_TILE_MAX_ZOOM", 15)
    processes = max(1, parse_int("MINER_TILE_PROCESSES", 4))
    source_tif = resolve_source_tif()

    if source_tif is None:
        print("[miner-tiles] No source tif found under /offline_maps/dali, skip generation.", flush=True)
        return 0

    tile_root.mkdir(parents=True, exist_ok=True)
    zoom_levels = existing_zoom_levels(tile_root)
    current_max_zoom = zoom_levels[-1] if zoom_levels else None
    start_zoom = min_zoom if current_max_zoom is None else max(min_zoom, current_max_zoom + 1)

    if start_zoom > max_zoom:
        print(
            f"[miner-tiles] Existing tiles already cover zoom <= {current_max_zoom}, target={max_zoom}.",
            flush=True,
        )
        return 0

    command = [
        "gdal2tiles.py",
        "--xyz",
        "--resume",
        "--webviewer=none",
        f"--processes={processes}",
        f"--zoom={start_zoom}-{max_zoom}",
        str(source_tif),
        str(tile_root),
    ]
    run_env = os.environ.copy()
    run_env.setdefault("GDAL_DATA", "/opt/conda/envs/MMSeg310/share/gdal")
    run_env.setdefault("PROJ_LIB", "/opt/conda/envs/MMSeg310/share/proj")
    run_env.setdefault("PROJ_DATA", run_env["PROJ_LIB"])
    print(
        f"[miner-tiles] Generating tiles from {source_tif} into {tile_root} for zoom {start_zoom}-{max_zoom}.",
        flush=True,
    )
    subprocess.run(command, check=True, env=run_env)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
