import copy
from pathlib import Path
from typing import Dict, Optional
from xml.etree import ElementTree as ET

NS = {"kml": "http://www.opengis.net/kml/2.2"}
KML_NS = NS["kml"]
ET.register_namespace("", KML_NS)


def _tag(name: str) -> str:
    return f"{{{KML_NS}}}{name}"


def _new_doc() -> ET.ElementTree:
    root = ET.Element(_tag("kml"))
    ET.SubElement(root, _tag("Document"))
    return ET.ElementTree(root)


def _document(root: ET.Element) -> ET.Element:
    doc = root.find("kml:Document", NS)
    if doc is None:
        doc = ET.SubElement(root, _tag("Document"))
    return doc


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


def _has_polygon(pm: ET.Element) -> bool:
    return pm.find(".//kml:Polygon", NS) is not None


def _parent_map(root: ET.Element) -> Dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in parent}


def merge_kml_increment(base_kml: Path, incoming_kml: Path) -> Dict:
    if not incoming_kml.exists():
        raise FileNotFoundError(f"kml_path not found: {incoming_kml}")

    if base_kml.exists():
        base_tree = ET.parse(base_kml)
    else:
        base_tree = _new_doc()
    incoming_tree = ET.parse(incoming_kml)

    base_root = base_tree.getroot()
    incoming_root = incoming_tree.getroot()
    base_doc = _document(base_root)
    parents = _parent_map(base_root)

    existing = {}
    for pm in base_root.findall(".//kml:Placemark", NS):
        fid = _extract_fid(pm)
        if fid:
            existing[fid] = (pm, parents.get(pm, base_doc))

    inserted = 0
    updated = 0
    skipped = 0
    fids = []

    for pm in incoming_root.findall(".//kml:Placemark", NS):
        fid = _extract_fid(pm)
        if not fid or not _has_polygon(pm):
            skipped += 1
            continue
        new_pm = copy.deepcopy(pm)
        if fid in existing:
            old_pm, parent = existing[fid]
            parent.remove(old_pm)
            parent.append(new_pm)
            existing[fid] = (new_pm, parent)
            updated += 1
        else:
            base_doc.append(new_pm)
            existing[fid] = (new_pm, base_doc)
            inserted += 1
        fids.append(int(fid) if fid.isdigit() else fid)

    base_kml.parent.mkdir(parents=True, exist_ok=True)
    base_tree.write(base_kml, encoding="utf-8", xml_declaration=True)
    return {
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "fids": fids,
        "kml_path": str(base_kml),
    }
