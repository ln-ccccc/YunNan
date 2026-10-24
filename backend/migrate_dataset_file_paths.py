# -*- coding: utf-8 -*-
"""一次性迁移：ProjectDataset.file_path 绝对路径 → storage 根相对键（契约 B）。

背景（2026-10-24 审查 P1-1）：clip/slice 曾把服务器绝对路径写进
ProjectDataset.file_path，导致推理输入校验与跨环境快照导入两处死路。
新代码只写相对键；本脚本把“绝对路径且位于当前 PROJECT_STORAGE_ROOT 下”
的存量行收敛为相对键，storage 根之外的行保留原值并列入报告待人工处置。

用法（容器内执行；执行前先按 volume_backups 惯例备份 MySQL 卷）：
    python -B migrate_dataset_file_paths.py --dry-run   # 只打印计划，不落库
    python -B migrate_dataset_file_paths.py             # 实际执行并提交

幂等条件：只匹配绝对形态（POSIX 绝对或 Windows 盘符开头），相对键行永不触碰。
不进启动钩子：绝对前缀依部署环境而异，误判风险必须由人工看 dry-run 报告兜底。
"""
import argparse
import json
import os
import re
from pathlib import Path

from applications import create_app
from applications.extensions import db
from applications.models.project import ProjectDataset
from applications.project_hub.spatial_storage import get_storage_root

_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:(?:[\\/]|$)")


def _is_absolute_form(text):
    return text.startswith("/") or text.startswith("\\") or bool(_WINDOWS_DRIVE.match(text))


def plan_migration():
    """返回 (待迁移行, 跳过行)。跳过 = 绝对路径但不在当前 storage 根内。"""
    storage_root = get_storage_root()
    rows = []
    skipped = []
    for dataset in ProjectDataset.query.all():
        text = str(dataset.file_path or "").strip()
        if not text or not _is_absolute_form(text):
            continue
        try:
            relative = Path(text).expanduser().resolve().relative_to(storage_root).as_posix()
        except (ValueError, OSError):
            skipped.append({
                "dataset_id": dataset.id,
                "file_path": text,
                "reason": "不在当前 PROJECT_STORAGE_ROOT 内，需人工确认归属",
            })
            continue
        rows.append({"dataset_id": dataset.id, "from": text, "to": relative})
    return rows, skipped


def main():
    parser = argparse.ArgumentParser(
        description="把 ProjectDataset.file_path 存量绝对路径迁移为 storage 根相对键"
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印迁移计划，不修改数据库")
    args = parser.parse_args()

    app = create_app(os.getenv("FLASK_CONFIG", "development"))
    with app.app_context():
        rows, skipped = plan_migration()
        if not args.dry_run:
            for item in rows:
                dataset = db.session.get(ProjectDataset, item["dataset_id"])
                if dataset is not None:
                    dataset.file_path = item["to"]
            db.session.commit()
        print(json.dumps(
            {
                "dry_run": args.dry_run,
                "migrated" if not args.dry_run else "to_migrate": len(rows),
                "items": rows,
                "skipped": skipped,
            },
            ensure_ascii=False,
            indent=2,
        ))


if __name__ == "__main__":
    main()
