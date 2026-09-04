from pathlib import Path
from typing import Dict, Iterable, List, Optional

from openpyxl import Workbook, load_workbook


INDEX_FILE_MAP = {
    "NDVI": "NDVI_2year.xlsx",
    "NDBI": "NDBI_by_fid_2year_avg.xlsx",
    "NDWI": "NDWI_by_fid_2year_avg.xlsx",
    "NDSI": "NDSI_by_fid_2year_avg.xlsx",
}


def _default_miner_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "miner"


def _fid_cell_value(fid):
    text = str(fid).strip()
    return int(text) if text.isdigit() else text


def _find_header(headers: List[object], candidates: Iterable[str]) -> Optional[int]:
    lowered = {str(item).strip().lower() for item in candidates}
    for idx, value in enumerate(headers):
        if str(value or "").strip().lower() in lowered:
            return idx + 1
    return None


def sync_miner_index_rows(
    index_type: str,
    year: str,
    rows: List[Dict],
    miner_dir: Optional[Path] = None,
    overwrite_existing: bool = True,
) -> Dict:
    index_key = str(index_type or "").strip().upper()
    if index_key not in INDEX_FILE_MAP:
        return {"synced": False, "reason": "unsupported_index", "rows": 0}

    year_text = str(year or "").strip()
    if not year_text.isdigit() or len(year_text) != 4:
        return {"synced": False, "reason": "missing_year", "rows": 0}

    valid_rows = [
        row for row in rows
        if row.get("fid") not in (None, "") and row.get("mean") is not None
    ]
    if not valid_rows:
        return {"synced": False, "reason": "no_fid_stats", "rows": 0}

    target_dir = Path(miner_dir) if miner_dir else _default_miner_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / INDEX_FILE_MAP[index_key]

    if target_path.exists():
        wb = load_workbook(target_path)
        ws = wb[wb.sheetnames[0]]
    else:
        wb = Workbook()
        ws = wb.active
        ws.title = target_path.stem[:31]
        ws.cell(row=1, column=1, value="FID_1")

    headers = [cell.value for cell in ws[1]]
    fid_col = _find_header(headers, ("fid_1", "fid"))
    if fid_col is None:
        fid_col = 1
        ws.cell(row=1, column=fid_col, value="FID_1")

    headers = [cell.value for cell in ws[1]]
    year_col = _find_header(headers, (year_text,))
    if year_col is None:
        year_col = ws.max_column + 1
        ws.cell(row=1, column=year_col, value=year_text)

    row_by_fid = {}
    for row_idx in range(2, ws.max_row + 1):
        value = ws.cell(row=row_idx, column=fid_col).value
        if value in (None, ""):
            continue
        row_by_fid[str(value).strip()] = row_idx

    updated = 0
    inserted = 0
    skipped_existing = 0
    for item in valid_rows:
        fid_text = str(item["fid"]).strip()
        row_idx = row_by_fid.get(fid_text)
        if row_idx is None:
            row_idx = ws.max_row + 1
            ws.cell(row=row_idx, column=fid_col, value=_fid_cell_value(fid_text))
            row_by_fid[fid_text] = row_idx
            inserted += 1
        else:
            updated += 1
        target_cell = ws.cell(row=row_idx, column=year_col)
        if not overwrite_existing and target_cell.value not in (None, ""):
            skipped_existing += 1
            continue
        target_cell.value = float(item["mean"])

    wb.save(target_path)
    return {
        "synced": True,
        "index_type": index_key,
        "year": year_text,
        "path": str(target_path),
        "rows": len(valid_rows),
        "inserted": inserted,
        "updated": updated,
        "skipped_existing": skipped_existing,
    }
