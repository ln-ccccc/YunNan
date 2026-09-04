import argparse
import json
import os
from pathlib import Path

from applications import create_app
from applications.project_hub.legacy_migration import migrate_legacy_project_data


def main():
    parser = argparse.ArgumentParser(description="Migrate legacy miner outputs into the project workspace.")
    parser.add_argument("--project-name", default="历史成果迁移项目")
    parser.add_argument("--manager", default=os.getenv("ADMIN_USERNAME", "admin"))
    parser.add_argument("--miner-root")
    parser.add_argument("--output-root")
    parser.add_argument("--kml-path")
    parser.add_argument("--static-root")
    args = parser.parse_args()

    config_name = os.getenv("FLASK_CONFIG", "development")
    app = create_app(config_name)
    with app.app_context():
        result = migrate_legacy_project_data(
            project_name=args.project_name,
            manager=args.manager,
            miner_root=Path(args.miner_root) if args.miner_root else None,
            output_root=Path(args.output_root) if args.output_root else None,
            kml_path=Path(args.kml_path) if args.kml_path else None,
            static_root=Path(args.static_root) if args.static_root else None,
        )
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
