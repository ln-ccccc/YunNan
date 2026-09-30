# -*- coding: utf-8 -*-
"""项目导出 DXF writer（优化建议四.1：兼容 DXF 通用格式，对接 CAD/GIS）。

输出最简 ASCII DXF R12：每图斑一个闭合 POLYLINE 实体，地类经图层绑定
（图层名 CLASS_<class_code>，与表结构附带的图层色号对应）——DXF 无要素属性表，
按图层分地类是 ArcGIS/QGIS 互转的通行约定。带 class_code 的解译成果要素按
地类分层；矿山边界等无地类属性的默认导出统一落在 CLASS_NA 层。
坐标直接放经纬度，与 GeoJSON 导出同为 EPSG:4326；洞环作为独立闭合线输出。
"""
from collections import OrderedDict

_CLASS_ACI_COLORS = (1, 3, 5, 2, 4, 6, 30, 8, 9, 90)


def _pairs(ring):
    points = []
    for point in ring or []:
        try:
            x = float(point[0])
            y = float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        points.append((x, y))
    return points


def _polygon_rings(geometry):
    if not isinstance(geometry, dict):
        return []
    if geometry.get("type") == "Polygon":
        return [geometry.get("coordinates") or []]
    if geometry.get("type") == "MultiPolygon":
        return [polygon for polygon in (geometry.get("coordinates") or []) if polygon]
    return []


def _layer_name(class_code):
    """class_code 白名单：None/空串回退层；bool/整数/整值浮点合法；
    其余（字符串数字、容器等客户端可控值）抛校验错误而非解释器异常外泄。"""
    if class_code is None or class_code == "":
        return "CLASS_NA"
    if isinstance(class_code, bool) or not isinstance(class_code, (int, float)):
        raise ValueError("features[].properties.class_code 必须是数值（DXF 图层名）")
    if isinstance(class_code, float) and not class_code.is_integer():
        raise ValueError("features[].properties.class_code 必须是整数（DXF 图层名）")
    return f"CLASS_{int(class_code)}"


def _codes(dxf, code, value=None):
    dxf.append(f"{code}")
    dxf.append("" if value is None else str(value))


def _write_polyline(dxf, layer, ring):
    points = _pairs(ring)
    if len(points) < 3:
        return False
    _codes(dxf, 0, "POLYLINE")
    _codes(dxf, 8, layer)
    _codes(dxf, 66, 1)
    _codes(dxf, 70, 1)
    _codes(dxf, 10, "0.0")
    _codes(dxf, 20, "0.0")
    _codes(dxf, 30, "0.0")
    for x, y in points:
        _codes(dxf, 0, "VERTEX")
        _codes(dxf, 8, layer)
        _codes(dxf, 10, f"{x:.7f}")
        _codes(dxf, 20, f"{y:.7f}")
        _codes(dxf, 30, "0.0")
    _codes(dxf, 0, "SEQEND")
    _codes(dxf, 8, layer)
    return True


def build_dxf_document(features):
    """GeoJSON features → ASCII DXF R12 文本行列表（UTF-8）。"""
    layers = OrderedDict()
    for feature in features or []:
        properties = (feature or {}).get("properties") or {}
        class_code = properties.get("class_code")
        layer = _layer_name(class_code)
        if layer not in layers:
            color_index = len(layers)
            layers[layer] = _CLASS_ACI_COLORS[color_index % len(_CLASS_ACI_COLORS)]

    dxf = []
    _codes(dxf, 0, "SECTION")
    _codes(dxf, 2, "TABLES")
    _codes(dxf, 0, "TABLE")
    _codes(dxf, 2, "LAYER")
    _codes(dxf, 70, len(layers))
    for layer, color in layers.items():
        _codes(dxf, 0, "LAYER")
        _codes(dxf, 2, layer)
        _codes(dxf, 70, 0)
        _codes(dxf, 62, color)
        _codes(dxf, 6, "CONTINUOUS")
    _codes(dxf, 0, "ENDTAB")
    _codes(dxf, 0, "ENDSEC")

    _codes(dxf, 0, "SECTION")
    _codes(dxf, 2, "ENTITIES")
    entity_count = 0
    for feature in features or []:
        properties = (feature or {}).get("properties") or {}
        layer = _layer_name(properties.get("class_code"))
        for polygon in _polygon_rings((feature or {}).get("geometry")):
            for ring in polygon:
                if _write_polyline(dxf, layer, ring):
                    entity_count += 1
    _codes(dxf, 0, "ENDSEC")
    _codes(dxf, 0, "EOF")
    return dxf, entity_count


def write_dxf(path, features):
    """写 DXF 制品文件，返回实体（闭合多段线）数量。"""
    dxf, entity_count = build_dxf_document(features)
    with open(path, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write("\n".join(dxf))
        handle.write("\n")
    return entity_count
