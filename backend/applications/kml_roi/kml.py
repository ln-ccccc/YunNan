import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

NS = {"kml": "http://www.opengis.net/kml/2.2"}


def _parse_coordinates(text: str) -> List[List[float]]:
    coords: List[List[float]] = []
    if not text:
        return coords
    for token in text.replace("\n", " ").replace("\t", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            # 单个脏 token 跳过（与 _extract_geometry_4326 对坏环的宽容策略一致），
            # 不让整任务因一处手写错误失败
            continue
        coords.append([lon, lat])
    if coords and coords[0] != coords[-1]:
        coords.append(coords[0])
    return coords


def _extract_fid(pm: ET.Element) -> Optional[str]:
    for sd in pm.findall(".//kml:SimpleData", NS):
        key = (sd.attrib.get("name") or "").strip().lower()
        if key in {"fid_1", "fid"}:
            val = (sd.text or "").strip()
            if val:
                return val

    name_node = pm.find("kml:name", NS)
    if name_node is not None and name_node.text:
        val = name_node.text.strip()
        if val:
            return val
    return None


def _extract_geometry_4326(pm: ET.Element) -> Optional[Dict]:
    polygons = []
    for poly in pm.findall(".//kml:Polygon", NS):
        outer_node = poly.find("kml:outerBoundaryIs/kml:LinearRing/kml:coordinates", NS)
        if outer_node is None or not outer_node.text:
            continue

        outer_ring = _parse_coordinates(outer_node.text)
        if len(outer_ring) < 4:
            continue

        rings = [outer_ring]
        for inner in poly.findall("kml:innerBoundaryIs/kml:LinearRing/kml:coordinates", NS):
            inner_ring = _parse_coordinates(inner.text or "")
            if len(inner_ring) >= 4:
                rings.append(inner_ring)
        polygons.append(rings)

    if not polygons:
        return None
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def load_kml_features(kml_path: Path) -> List[Tuple[str, Dict]]:
    tree = ET.parse(kml_path)
    root = tree.getroot()
    features: List[Tuple[str, Dict]] = []
    for pm in root.findall(".//kml:Placemark", NS):
        fid = _extract_fid(pm)
        geom = _extract_geometry_4326(pm)
        if fid and geom:
            features.append((fid, geom))
    return features


def load_vector_features(vector_path: Path) -> List[Tuple[str, Dict]]:
    suffix = vector_path.suffix.lower()
    if suffix == ".kml":
        return load_kml_features(vector_path)
    if suffix not in {".geojson", ".json"}:
        raise ValueError("项目矿山资源仅支持 KML/GeoJSON")

    payload = json.loads(vector_path.read_text(encoding="utf-8"))
    features: List[Tuple[str, Dict]] = []
    for feature in payload.get("features") or []:
        properties = feature.get("properties") or {}
        fid = None
        for key in ("FID_1", "FID", "OBJECTID"):
            if properties.get(key) not in (None, ""):
                fid = properties[key]
                break
        if fid in (None, ""):
            fid = feature.get("id")
        geometry = feature.get("geometry")
        if fid not in (None, "") and geometry:
            features.append((str(fid), geometry))
    return features

