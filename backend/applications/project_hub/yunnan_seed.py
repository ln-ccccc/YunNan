import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

from applications.extensions import db
from applications.models.project import Project, ProjectActivityLog, ProjectMineBinding


KML_NS = {"kml": "http://www.opengis.net/kml/2.2"}
PROJECT_NAME = "云南矿山生态修复监测项目"
PROJECT_REGION = "云南省"
PROJECT_REMARK = "system_seed:yunnan_kml"
EXPECTED_MINE_COUNT = 565
MAX_DB_INT = 2147483647
YUNNAN_SUBREGIONS = {
    "昆明", "曲靖", "玉溪", "昭通", "保山", "丽江", "普洱", "临沧",
    "楚雄", "红河", "文山", "西双版纳", "大理", "德宏", "怒江", "迪庆",
    "kunming", "qujing", "yuxi", "zhaotong", "baoshan", "lijiang", "puer", "lincang",
    "chuxiong", "honghe", "wenshan", "xishuangbanna", "dali", "dehong", "nujiang", "diqing",
}


def _text(value):
    return str(value or "").strip()


def _is_yunnan_region(value):
    region = _text(value).casefold()
    if not region or "云南" in region or "yunnan" in region:
        return True
    return any(name in region for name in YUNNAN_SUBREGIONS)


def _to_fid(value):
    text = _text(value)
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite() or number != number.to_integral_value():
        return None
    fid = int(number)
    return fid if 0 < fid <= MAX_DB_INT else None


def _to_float(value):
    text = _text(value)
    if not text:
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _first(fields, names):
    for name in names:
        value = _text(fields.get(name))
        if value:
            return value
    return ""


def _parse_kml(kml_path):
    root = ET.parse(Path(kml_path)).getroot()
    mines_by_fid = {}
    for placemark in root.findall(".//kml:Placemark", KML_NS):
        fields = {}
        for item in placemark.findall(".//kml:SimpleData", KML_NS):
            key = _text(item.attrib.get("name"))
            if key:
                fields[key] = _text("".join(item.itertext()))

        province = _text(fields.get("SHENG"))
        if province and province != PROJECT_REGION:
            raise RuntimeError(f"KML 中存在非云南矿山：SHENG={province}")

        fid = _to_fid(fields.get("FID_1"))
        if fid is None:
            continue

        placemark_name = _text(
            placemark.findtext("kml:name", default="", namespaces=KML_NS)
        )
        area_value = _first(fields, ("TBTYMJ_1", "TBTYMJ", "SHAPE_Area"))
        mines_by_fid[fid] = {
            "mine_fid": fid,
            "mine_name_snapshot": _first(fields, ("GGKSMC", "SBKSMC", "ZLKSMC"))
            or placemark_name
            or f"矿山 {fid}",
            "city_snapshot": _first(fields, ("SHI", "SHI_1")) or None,
            "area_snapshot": _to_float(area_value),
            "status_snapshot": _first(fields, ("HFZLQK", "ZLHFZLQK")) or None,
        }
    return [mines_by_fid[fid] for fid in sorted(mines_by_fid)]


def seed_yunnan_project(kml_path, expected_count=EXPECTED_MINE_COUNT, manager="admin"):
    mines = _parse_kml(kml_path)
    if len(mines) != expected_count:
        raise RuntimeError(f"KML 有效矿山数为 {len(mines)}，预期 {expected_count}")

    projects = Project.query.filter(Project.deleted_at.is_(None)).all()
    for candidate in projects:
        region = _text(candidate.region)
        if not _is_yunnan_region(region):
            raise RuntimeError(f"数据库中存在非云南项目：{candidate.name}")

    project = next(
        (candidate for candidate in projects if candidate.remark == PROJECT_REMARK),
        None,
    )
    if project is None:
        project = next(
            (candidate for candidate in projects if candidate.name == PROJECT_NAME),
            None,
        )
    if project is not None:
        return {
            "created": False,
            "project_id": project.id,
            "mine_count": len(project.mines),
        }

    project = Project(
        name=PROJECT_NAME,
        region=PROJECT_REGION,
        status="active",
        manager=_text(manager) or "admin",
        remark=PROJECT_REMARK,
        monitor_start_year=2017,
        monitor_end_year=2025,
    )
    try:
        db.session.add(project)
        db.session.flush()

        for sort_order, mine in enumerate(mines, start=1):
            db.session.add(
                ProjectMineBinding(
                    project_id=project.id,
                    mine_fid=mine["mine_fid"],
                    mine_name_snapshot=mine["mine_name_snapshot"],
                    city_snapshot=mine["city_snapshot"],
                    area_snapshot=mine["area_snapshot"],
                    status_snapshot=mine["status_snapshot"],
                    sort_order=sort_order,
                )
            )
        db.session.add(
            ProjectActivityLog(
                project_id=project.id,
                event_type="yunnan_project_seeded",
                actor=project.manager,
                payload_json=json.dumps({"mine_count": len(mines)}, ensure_ascii=False),
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return {
        "created": True,
        "project_id": project.id,
        "mine_count": len(mines),
    }
