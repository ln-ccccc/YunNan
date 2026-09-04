from pathlib import Path
from typing import Dict, Optional, Tuple

import cv2
import numpy as np
import rasterio
from applications.common.utils.tiff_processor import read_tiff_as_rgb
from rasterio.features import geometry_mask
from rasterio.mask import mask
from rasterio.warp import transform_bounds, transform_geom
from rasterio.windows import Window, from_bounds

WGS84 = "EPSG:4326"
UI_SEGMENT_SIZE = (512, 512)


def raster_bounds_4326(raster_path: Path) -> Tuple[float, float, float, float]:
    with rasterio.open(raster_path) as src:
        if src.crs is None:
            raise RuntimeError(f"Missing CRS for raster: {raster_path}")
        return transform_bounds(src.crs, WGS84, *src.bounds, densify_pts=21)


def raster_union_bounds_4326(old_tif: Path, new_tif: Path) -> Tuple[float, float, float, float]:
    a = raster_bounds_4326(old_tif)
    b = raster_bounds_4326(new_tif)
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def crop_polygon_from_raster(raster_path: Path, geom_4326: Dict, out_path: Path) -> bool:
    with rasterio.open(raster_path) as src:
        if src.crs is None:
            raise RuntimeError(f"Missing CRS for raster: {raster_path}")

        geom_src = transform_geom(WGS84, src.crs, geom_4326, precision=6)
        try:
            out_image, out_transform = mask(src, [geom_src], crop=True, nodata=0, filled=True)
        except ValueError:
            return False

        if out_image.size == 0 or out_image.shape[1] == 0 or out_image.shape[2] == 0:
            return False

        profile = src.profile.copy()
        profile.update(
            {
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": out_transform,
                "count": out_image.shape[0],
                "compress": "lzw",
            }
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(out_image)
    return True


def _iter_geom_points(geom: Dict):
    gtype = geom.get("type")
    coords = geom.get("coordinates", [])
    if gtype == "Polygon":
        for ring in coords:
            for x, y in ring:
                yield float(x), float(y)
    elif gtype == "MultiPolygon":
        for poly in coords:
            for ring in poly:
                for x, y in ring:
                    yield float(x), float(y)


def _geom_bounds(geom: Dict) -> Optional[Tuple[float, float, float, float]]:
    pts = list(_iter_geom_points(geom))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def crop_bbox_from_raster(raster_path: Path, geom_4326: Dict, out_path: Path) -> bool:
    """Crop by polygon bounding box and keep original pixels (no polygon masking)."""
    with rasterio.open(raster_path) as src:
        if src.crs is None:
            raise RuntimeError(f"Missing CRS for raster: {raster_path}")

        geom_src = transform_geom(WGS84, src.crs, geom_4326, precision=6)
        bounds = _geom_bounds(geom_src)
        if bounds is None:
            return False
        minx, miny, maxx, maxy = bounds
        if minx >= maxx or miny >= maxy:
            return False

        try:
            window = from_bounds(minx, miny, maxx, maxy, src.transform)
            raster_window = Window(0, 0, src.width, src.height)
            window = window.intersection(raster_window).round_offsets().round_lengths()
        except Exception:
            return False

        if window.width <= 0 or window.height <= 0:
            return False

        out_image = src.read(window=window, boundless=False)
        if out_image.size == 0 or out_image.shape[1] == 0 or out_image.shape[2] == 0:
            return False

        profile = src.profile.copy()
        profile.update(
            {
                "height": out_image.shape[1],
                "width": out_image.shape[2],
                "transform": src.window_transform(window),
                "count": out_image.shape[0],
                "compress": "lzw",
            }
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(out_path, "w", **profile) as dst:
            dst.write(out_image)
    return True


def tif_to_png(tif_path: Path, png_path: Path, resize_to: Optional[Tuple[int, int]] = UI_SEGMENT_SIZE) -> bool:
    try:
        # Reuse the UI TIFF conversion path so ROI inference sees the same RGB mapping.
        rgb = read_tiff_as_rgb(str(tif_path))
        if resize_to is not None:
            rgb = cv2.resize(rgb, resize_to, interpolation=cv2.INTER_LINEAR)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        return bool(cv2.imwrite(str(png_path), rgb[:, :, ::-1]))
    except Exception:
        return False


def border_connected_bg_mask(tile_img: np.ndarray) -> np.ndarray:
    """
    Infer background mask from border-connected pure black/white regions.
    Works for both old black-background and new white-background previews.
    """
    if tile_img is None or tile_img.ndim != 3:
        return np.zeros((0, 0), dtype=bool)

    h, w = tile_img.shape[:2]
    border = np.concatenate(
        [
            tile_img[0, :, :].reshape(-1, 3),
            tile_img[h - 1, :, :].reshape(-1, 3),
            tile_img[:, 0, :].reshape(-1, 3),
            tile_img[:, w - 1, :].reshape(-1, 3),
        ],
        axis=0,
    )
    black_border = int(np.count_nonzero(np.all(border == 0, axis=1)))
    white_border = int(np.count_nonzero(np.all(border == 255, axis=1)))
    if white_border >= black_border:
        bg = np.all(tile_img == 255, axis=2)
    else:
        bg = np.all(tile_img == 0, axis=2)

    visited = np.zeros((h, w), dtype=np.uint8)
    stack = []
    for x in range(w):
        if bg[0, x]:
            visited[0, x] = 1
            stack.append((0, x))
        if bg[h - 1, x] and not visited[h - 1, x]:
            visited[h - 1, x] = 1
            stack.append((h - 1, x))
    for y in range(h):
        if bg[y, 0] and not visited[y, 0]:
            visited[y, 0] = 1
            stack.append((y, 0))
        if bg[y, w - 1] and not visited[y, w - 1]:
            visited[y, w - 1] = 1
            stack.append((y, w - 1))

    while stack:
        cy, cx = stack.pop()
        for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
            if 0 <= ny < h and 0 <= nx < w and bg[ny, nx] and not visited[ny, nx]:
                visited[ny, nx] = 1
                stack.append((ny, nx))
    return visited.astype(bool)


def build_polygon_mask_for_prediction(
    raster_path: Path,
    geom_4326: Dict,
    target_size: Tuple[int, int],
) -> Optional[np.ndarray]:
    try:
        with rasterio.open(raster_path) as src:
            if src.crs is None:
                raise RuntimeError(f"Missing CRS for raster: {raster_path}")

            geom_src = transform_geom(WGS84, src.crs, geom_4326, precision=6)
            mask_arr = geometry_mask(
                [geom_src],
                out_shape=(src.height, src.width),
                transform=src.transform,
                invert=True,
            )

        target_w, target_h = target_size
        resized = cv2.resize(mask_arr.astype(np.uint8), (target_w, target_h), interpolation=cv2.INTER_NEAREST)
        return resized > 0
    except Exception:
        return None


def crop_prediction_by_polygon(
    *,
    pred_image_path: Path,
    pred_mask_path: Path,
    raster_path: Path,
    geom_4326: Dict,
    out_image_path: Path,
    out_mask_path: Path,
) -> bool:
    pred_image = cv2.imread(str(pred_image_path), cv2.IMREAD_COLOR)
    pred_mask = cv2.imread(str(pred_mask_path), cv2.IMREAD_UNCHANGED)
    if pred_image is None or pred_mask is None:
        return False
    if pred_mask.ndim == 3:
        pred_mask = pred_mask[:, :, 0]

    h, w = pred_mask.shape[:2]
    poly_mask = build_polygon_mask_for_prediction(raster_path, geom_4326, (w, h))
    if poly_mask is None or not np.any(poly_mask):
        return False

    ys, xs = np.where(poly_mask)
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1

    outlined_image = pred_image.copy()
    boundary_mask = poly_mask.astype(np.uint8)
    contours, _ = cv2.findContours(boundary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        cv2.drawContours(outlined_image, contours, -1, (0, 255, 255), 3)
    cropped_image = outlined_image[y0:y1, x0:x1]

    cropped_mask = np.full(pred_mask.shape, 255, dtype=np.uint8)
    cropped_mask[poly_mask] = pred_mask[poly_mask]
    cropped_mask = cropped_mask[y0:y1, x0:x1]

    out_image_path.parent.mkdir(parents=True, exist_ok=True)
    ok_img = bool(cv2.imwrite(str(out_image_path), cropped_image))
    ok_mask = bool(cv2.imwrite(str(out_mask_path), cropped_mask))
    return ok_img and ok_mask


def draw_polygon_boundary_on_prediction(
    *,
    pred_image_path: Path,
    pred_mask_path: Path,
    tile_image_path: Path,
    raster_path: Optional[Path] = None,
    geom_4326: Optional[Dict] = None,
    out_image_path: Path,
    out_mask_path: Path,
    boundary_color: Tuple[int, int, int] = (0, 255, 255),
    boundary_width: int = 3,
) -> bool:
    pred_img = cv2.imread(str(pred_image_path), cv2.IMREAD_COLOR)
    pred_mask = cv2.imread(str(pred_mask_path), cv2.IMREAD_UNCHANGED)
    tile_img = cv2.imread(str(tile_image_path), cv2.IMREAD_COLOR)
    if pred_img is None or pred_mask is None or tile_img is None:
        return False
    if pred_mask.ndim == 3:
        pred_mask = pred_mask[:, :, 0]
    if tile_img.shape[:2] != pred_img.shape[:2]:
        tile_img = cv2.resize(tile_img, (pred_img.shape[1], pred_img.shape[0]), interpolation=cv2.INTER_NEAREST)

    contours = []
    if raster_path is not None and geom_4326 is not None:
        poly_mask = build_polygon_mask_for_prediction(raster_path, geom_4326, (pred_img.shape[1], pred_img.shape[0]))
        if poly_mask is not None and np.any(poly_mask):
            contours, _ = cv2.findContours(poly_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        valid = (~border_connected_bg_mask(tile_img)).astype(np.uint8)
        contours, _ = cv2.findContours(valid, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    outlined = pred_img.copy()
    if contours:
        cv2.drawContours(outlined, contours, -1, boundary_color, boundary_width)

    # Keep full-scene prediction mask. Only overlay the KML boundary line on the image.
    out_mask = pred_mask.copy()

    out_image_path.parent.mkdir(parents=True, exist_ok=True)
    ok_img = bool(cv2.imwrite(str(out_image_path), outlined))
    ok_mask = bool(cv2.imwrite(str(out_mask_path), out_mask))
    return ok_img and ok_mask
