# -*- coding: utf-8 -*-
"""存量补算（2026-10-31 方案 b）：已人工修订但尚无审校统计的成果回填。

跑法（容器，与 test 同环境）：
    python -B backfill_class_ratio_revised.py [--limit N]

- 扫全部 vector_status != vector_failed 且 current_revision_no > 0 的成果；
- 幂等：class_ratio_percent.json 的 revised.revision_no 已等于当前修订号则跳过；
- 同步重算审校变化矩阵（≥2 年可算）。
"""
import argparse
import json
import logging

from applications import create_app
from applications.extensions import db
from applications.models.classification_result import ClassificationResult
from applications.project_hub.classification_stats import (
    _inference_fid_dir,
    apply_revised_class_ratio,
    compute_revised_change_matrix,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
LOGGER = logging.getLogger("backfill_revised")


def _already_current(result):
    path = _inference_fid_dir(result.project_id, result.mine_fid) / "class_ratio_percent.json"
    if not path.is_file():
        return False
    try:
        revised = json.loads(path.read_text(encoding="utf-8")).get("revised") or {}
    except (OSError, ValueError):
        return False
    per_year = revised.get("per_year")
    if isinstance(per_year, dict):
        entry = per_year.get(str(result.year))
        if entry and entry.get("revision_no") == result.current_revision_no:
            return True
    return revised.get("revision_no") == result.current_revision_no


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    app = create_app("production")
    with app.app_context():
        rows = (
            ClassificationResult.query.filter(
                ClassificationResult.vector_status != "vector_failed",
                ClassificationResult.current_revision_no > 0,
            )
            .order_by(ClassificationResult.id)
            .all()
        )
        updated = 0
        for result in rows:
            if _already_current(result):
                continue
            try:
                collection = json.loads(result.current_feature_collection_json or "null")
            except (TypeError, ValueError):
                collection = None
            if not collection:
                continue
            ratio_ok = apply_revised_class_ratio(
                result.project_id, result.mine_fid, result.year,
                result.current_revision_no, collection, result_id=result.id,
            )
            matrix_ok = compute_revised_change_matrix(result.project_id, result.mine_fid)
            if ratio_ok or matrix_ok:
                updated += 1
                LOGGER.info(
                    "回填 result_id=%s mine_fid=%s year=%s ratio=%s matrix=%s",
                    result.id, result.mine_fid, result.year, ratio_ok, matrix_ok,
                )
            if args.limit and updated >= args.limit:
                break
        db.session.commit()
        LOGGER.info({"backfilled": updated, "scanned": len(rows)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
