import argparse
import json
import os
from pathlib import Path

from applications import create_app
from applications.project_hub.yunnan_seed import EXPECTED_MINE_COUNT, seed_yunnan_project


def main():
    parser = argparse.ArgumentParser(description="Seed the Yunnan project from KML data.")
    parser.add_argument(
        "--kml-path",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "miner" / "yunnan.kml",
    )
    parser.add_argument("--expected-count", type=int, default=EXPECTED_MINE_COUNT)
    parser.add_argument("--manager", default=os.getenv("ADMIN_USERNAME", "admin"))
    args = parser.parse_args()

    app = create_app(os.getenv("FLASK_CONFIG", "development"))
    with app.app_context():
        result = seed_yunnan_project(
            kml_path=args.kml_path,
            expected_count=args.expected_count,
            manager=args.manager,
        )
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
