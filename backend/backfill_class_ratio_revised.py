# -*- coding: utf-8 -*-
"""存量补算（2026-10-31 方案 b；2026-11-24 随派生统计重构改写）。

跑法（容器，与 test 同环境）：
    python -B backfill_class_ratio_revised.py [--project-id N] [--dry-run]

- 为每条「当前修订号 > 0」的成果补写修订派生统计 sidecar
  （outputs/classification-results/<result_id>/revisions/<版本号>.stats.json）；
  已存在且图斑数对得上的跳过；
- 为涉及人工修订的每座矿山按当前有效矢量重算一次审校变化矩阵。

两项都是幂等的派生缓存维护，可重复执行，不修改任何业务数据。平时不需要跑：
新保存会自动写 sidecar 并触发矩阵重算，溯源读取发现矩阵待重算也会自动补请求；
本脚本用于把本次改造之前保存的历史修订一次性补齐，或缓存文件被误删后的重建。
历史遗留的 class_ratio_percent.json 里的 revised 块不再被任何读端采信，
下次推理发布会整文件重建该文件，无需清理。
"""
import argparse
import json
import logging

from applications import create_app
from applications.extensions import db
from applications.models.classification_result import ClassificationResult
from applications.project_hub.classification_stats import (
    compute_revised_change_matrix,
    read_revision_stats,
    write_revision_stats,
)

logging.basicConfig(level=logging.INFO, format="%(message)s")
LOGGER = logging.getLogger("backfill_revised")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=int, default=0, help="只处理指定项目")
    parser.add_argument("--dry-run", action="store_true", help="只统计缺什么，不写任何文件")
    args = parser.parse_args()

    app = create_app("production")
    with app.app_context():
        query = ClassificationResult.query.filter(
            ClassificationResult.vector_status != "vector_failed",
            ClassificationResult.current_revision_no > 0,
        )
        if args.project_id:
            query = query.filter(ClassificationResult.project_id == args.project_id)
        # 修订过的成果数量很少（需要它们的矢量来现算），整行加载可以接受
        rows = query.order_by(ClassificationResult.id).all()

        stats_written = stats_present = stats_unreadable = 0
        touched_mines = []
        for result in rows:
            revision_no = int(result.current_revision_no)
            stored = read_revision_stats(result.project_id, result.id, revision_no)
            if stored is not None and stored.get("feature_count") == (result.feature_count or 0):
                stats_present += 1
            else:
                try:
                    collection = json.loads(result.current_feature_collection_json or "null")
                except (TypeError, ValueError):
                    collection = None
                if not isinstance(collection, dict):
                    stats_unreadable += 1
                    continue
                if not args.dry_run:
                    write_revision_stats(result.project_id, result.id, revision_no, collection)
                stats_written += 1
                LOGGER.info(
                    "补写修订统计 result_id=%s mine_fid=%s year=%s revision=%s",
                    result.id, result.mine_fid, result.year, revision_no,
                )
            if (result.project_id, result.mine_fid) not in touched_mines:
                touched_mines.append((result.project_id, result.mine_fid))

        matrix_ok = matrix_failed = 0
        if not args.dry_run:
            # 矩阵按"项目+矿山"整算一次：同一矿山两个年份都修订过时不必算两遍
            for project_id, mine_fid in touched_mines:
                try:
                    if compute_revised_change_matrix(project_id, mine_fid):
                        matrix_ok += 1
                    else:
                        matrix_failed += 1
                except Exception:  # noqa: BLE001 - 单矿失败不中断整批
                    matrix_failed += 1
                    LOGGER.exception("重算矩阵失败 project_id=%s mine_fid=%s", project_id, mine_fid)
        db.session.commit()
        LOGGER.info({
            "dry_run": args.dry_run,
            "scanned_revised_results": len(rows),
            "stats_written": stats_written,
            "stats_already_present": stats_present,
            "stats_unreadable_vector": stats_unreadable,
            "mines_with_revisions": len(touched_mines),
            "matrix_rebuilt": matrix_ok,
            "matrix_not_written": matrix_failed,
        })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
