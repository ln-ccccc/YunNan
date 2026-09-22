"""遥感影像格式探测与校验（S1，2026-09-22）。

策略：直读不转换——IMG(ERDAS Imagine/HFA)、ENVI(.dat/.bin+.hdr 成对)、JP2 与
GeoTIFF 一样由 rasterio 驱动自动识别（容器内 HFA/ENVI/JPEG/PNG 驱动已实测可用），
原样落盘零转换；本模块只负责"放行哪些扩展名、ENVI 是否成对、文件是否可打开且带 CRS"。
"""
import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)

# 地理栅格格式：单文件即可读（rasterio 按内容/扩展名自动选驱动）
SUPPORTED_RASTER_EXTENSIONS = {"tif", "tiff", "img", "jp2"}
# ENVI 数据文件扩展名（必须与同名 .hdr 头文件成对出现）
ENVI_DATA_EXTENSIONS = {"dat", "bin"}
ENVI_HEADER_SUFFIX = ".hdr"

SUPPORTED_IMAGERY_EXTENSIONS = SUPPORTED_RASTER_EXTENSIONS | ENVI_DATA_EXTENSIONS | {"hdr"}


class RasterFormatError(ValueError):
    """面向客户端的格式/校验错误（400 语义）。"""


def _strip_ext(name):
    return Path(str(name or "")).suffix.lstrip(".").lower()


def is_supported_raster(filename) -> bool:
    """地理栅格数据文件放行判定：单文件格式或 ENVI 数据文件。

    注意不含 .hdr——头文件是伴生文本，不是栅格本体；配对由 detect_raster_kind 负责，
    上传时须与数据文件共享 UUID 词干落盘（否则 rasterio ENVI 驱动按同名约定找不到头）。
    """
    ext = _strip_ext(filename)
    return ext in SUPPORTED_RASTER_EXTENSIONS or ext in ENVI_DATA_EXTENSIONS


# 兼容别名：旧调用方语义即"可作为推理原始影像"，泛化后行为只增不减
is_tiff_file = is_supported_raster


def detect_raster_kind(filenames):
    """批量探测上传批次里的地理栅格条目。

    返回 [{kind, filename}]：kind ∈ geotiff / erdas_img / envi / envi_header /
    jp2；ENVI 数据文件缺同名 .hdr、或 .hdr 缺数据文件时抛 RasterFormatError。
    """
    names = [str(name or "") for name in (filenames or [])]
    by_stem = {}
    for name in names:
        by_stem.setdefault(Path(name).stem.lower(), []).append(name)

    entries = []
    envi_pairs = {}
    for name in names:
        ext = _strip_ext(name)
        if ext in ("tif", "tiff"):
            entries.append({"kind": "geotiff", "filename": name})
        elif ext == "img":
            entries.append({"kind": "erdas_img", "filename": name})
        elif ext == "jp2":
            entries.append({"kind": "jp2", "filename": name})
        elif ext in ENVI_DATA_EXTENSIONS:
            stem = Path(name).stem.lower()
            header = next(
                (
                    candidate
                    for candidate in by_stem.get(stem, [])
                    if _strip_ext(candidate) == "hdr"
                ),
                None,
            )
            if header is None:
                raise RasterFormatError(
                    f"ENVI 影像 '{name}' 缺少同名 .hdr 头文件，请将两者一起上传"
                )
            envi_pairs[stem] = {"data": name, "header": header}
        elif ext == "hdr":
            stem = Path(name).stem.lower()
            data = next(
                (
                    candidate
                    for candidate in by_stem.get(stem, [])
                    if _strip_ext(candidate) in ENVI_DATA_EXTENSIONS
                ),
                None,
            )
            if data is None:
                raise RasterFormatError(
                    f"ENVI 头文件 '{name}' 缺少同名数据文件（.dat/.bin），请将两者一起上传"
                )
            envi_pairs[stem] = {"data": data, "header": name}
    for stem, pair in sorted(envi_pairs.items()):
        entries.append({"kind": "envi", "filename": pair["data"], "header": pair["header"]})
    return entries


def validate_raster(path):
    """可读性+CRS 校验：rasterio 打开（驱动自动识别），返回摘要 dict。

    ENVI 传入数据文件路径（.hdr 需在同目录，rasterio ENVI 驱动按同名约定找头）。
    """
    import rasterio

    path = str(path)
    try:
        with rasterio.open(path) as src:
            if src.crs is None:
                raise RasterFormatError(
                    f"影像 '{Path(path).name}' 缺少坐标系（CRS），无法用于空间解译"
                )
            return {
                "driver": src.driver,
                "width": src.width,
                "height": src.height,
                "count": src.count,
                "crs": str(src.crs),
            }
    except RasterFormatError:
        raise
    except Exception as error:
        raise RasterFormatError(
            f"影像 '{Path(path).name}' 无法读取（格式不正确或驱动不支持）: {type(error).__name__}"
        ) from error
