import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.project import Project, ProjectActivityLog, ProjectMineBinding

try:
    from applications.project_hub.yunnan_seed import (
        PROJECT_NAME,
        seed_yunnan_project,
    )
except ModuleNotFoundError:
    PROJECT_NAME = "云南矿山生态修复监测项目"
    seed_yunnan_project = None


class TestYunnanProjectSeed(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.assertEqual(self.app.config["SQLALCHEMY_DATABASE_URI"], "sqlite:///:memory:")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        self.temp_dir = tempfile.TemporaryDirectory(prefix="yunnan-project-seed-")
        self.kml_path = Path(self.temp_dir.name) / "yunnan.kml"

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        self.temp_dir.cleanup()

    def _write_kml(self, placemarks):
        body = []
        for placemark_name, fields in placemarks:
            simple_data = "\n".join(
                f'          <SimpleData name="{name}">{value}</SimpleData>'
                for name, value in fields.items()
            )
            body.append(
                f"""    <Placemark>
      <name>{placemark_name}</name>
      <ExtendedData>
        <SchemaData schemaUrl="#yunnan">
{simple_data}
        </SchemaData>
      </ExtendedData>
    </Placemark>"""
            )
        self.kml_path.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
"""
            + "\n".join(body)
            + """
  </Document>
</kml>""",
            encoding="utf-8",
        )

    def _seed(self, expected_count=2):
        self.assertIsNotNone(seed_yunnan_project, "云南项目初始化服务尚未实现")
        return seed_yunnan_project(self.kml_path, expected_count=expected_count)

    def test_creates_unique_yunnan_project_and_preserves_existing_bindings_on_rerun(self):
        self._write_kml(
            [
                (
                    "Placemark 2",
                    {
                        "FID_1": "2",
                        "SHENG": "云南省",
                        "GGKSMC": "矿山乙",
                        "SHI": "昆明市",
                        "TBTYMJ_1": "12.5",
                        "HFZLQK": "已治理",
                    },
                ),
                (
                    "Placemark 1",
                    {
                        "FID_1": "1",
                        "SHENG": "云南省",
                        "SBKSMC": "矿山甲",
                        "SHI_1": "曲靖市",
                        "SHAPE_Area": "8.75",
                        "ZLHFZLQK": "治理中",
                    },
                ),
            ]
        )

        first = self._seed()

        self.assertTrue(first["created"])
        self.assertEqual(first["mine_count"], 2)
        project = Project.query.one()
        self.assertEqual(first["project_id"], project.id)
        self.assertEqual(project.name, "云南矿山生态修复监测项目")
        self.assertEqual(project.region, "云南省")
        self.assertEqual(project.status, "active")
        self.assertEqual(project.manager, "admin")
        self.assertEqual(project.remark, "system_seed:yunnan_kml")
        self.assertEqual(project.monitor_start_year, 2017)
        self.assertEqual(project.monitor_end_year, 2025)

        bindings = ProjectMineBinding.query.order_by(ProjectMineBinding.sort_order).all()
        self.assertEqual([row.mine_fid for row in bindings], [1, 2])
        self.assertEqual([row.mine_name_snapshot for row in bindings], ["矿山甲", "矿山乙"])
        self.assertEqual([row.city_snapshot for row in bindings], ["曲靖市", "昆明市"])
        self.assertEqual([row.area_snapshot for row in bindings], [8.75, 12.5])
        self.assertEqual([row.status_snapshot for row in bindings], ["治理中", "已治理"])
        self.assertEqual([row.sort_order for row in bindings], [1, 2])
        self.assertEqual(
            [row.event_type for row in ProjectActivityLog.query.all()],
            ["yunnan_project_seeded"],
        )

        bindings[0].city_snapshot = "用户后续修改"
        db.session.commit()
        second = self._seed()

        self.assertFalse(second["created"])
        self.assertEqual(second["project_id"], project.id)
        self.assertEqual(second["mine_count"], 2)
        self.assertEqual(Project.query.count(), 1)
        self.assertEqual(ProjectMineBinding.query.count(), 2)
        self.assertEqual(ProjectActivityLog.query.count(), 1)
        self.assertEqual(
            ProjectMineBinding.query.filter_by(mine_fid=1).one().city_snapshot,
            "用户后续修改",
        )

    def test_seeds_project_from_repository_yunnan_kml(self):
        real_kml_path = Path(__file__).resolve().parents[1] / "miner" / "yunnan.kml"

        result = seed_yunnan_project(real_kml_path)

        self.assertTrue(result["created"])
        self.assertEqual(result["mine_count"], 565)
        project = Project.query.one()
        self.assertEqual(project.name, "云南矿山生态修复监测项目")
        self.assertEqual(project.region, "云南省")
        self.assertEqual(project.status, "active")
        self.assertEqual(project.manager, "admin")
        self.assertEqual(project.remark, "system_seed:yunnan_kml")
        self.assertEqual(project.monitor_start_year, 2017)
        self.assertEqual(project.monitor_end_year, 2025)

        bindings = ProjectMineBinding.query.order_by(ProjectMineBinding.sort_order).all()
        mine_fids = [row.mine_fid for row in bindings]
        self.assertEqual(len(bindings), 565)
        self.assertEqual(len(set(mine_fids)), 565)
        self.assertTrue(any(row.mine_name_snapshot for row in bindings))
        self.assertTrue(any(row.city_snapshot for row in bindings))

    def test_rolls_back_all_writes_when_commit_fails(self):
        self._write_kml(
            [
                ("Mine 1", {"FID_1": "1", "SHENG": "云南省"}),
                ("Mine 2", {"FID_1": "2", "SHENG": "云南省"}),
            ]
        )

        with patch.object(
            db.session,
            "commit",
            side_effect=RuntimeError("commit failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "commit failed"):
                self._seed()

        self.assertEqual(Project.query.count(), 0)
        self.assertEqual(ProjectMineBinding.query.count(), 0)

    def test_rejects_existing_non_yunnan_project_without_writing(self):
        db.session.add(
            Project(
                name="江西矿山生态修复监测项目",
                region="江西省",
                status="active",
            )
        )
        db.session.commit()
        self._write_kml(
            [
                ("Mine 1", {"FID_1": "1", "SHENG": "云南省"}),
                ("Mine 2", {"FID_1": "2", "SHENG": "云南省"}),
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "存在非云南项目"):
            self._seed()

        self.assertEqual(Project.query.count(), 1)
        self.assertIsNone(Project.query.filter_by(name=PROJECT_NAME).first())
        self.assertEqual(ProjectMineBinding.query.count(), 0)

    def test_allows_another_yunnan_project_on_rerun(self):
        self._write_kml(
            [
                ("Mine 1", {"FID_1": "1", "SHENG": "云南省"}),
                ("Mine 2", {"FID_1": "2", "SHENG": "云南省"}),
            ]
        )
        first = self._seed()
        db.session.add(Project(name="云南用户项目", region=" 云南省 ", status="active"))
        db.session.commit()

        second = self._seed()

        self.assertFalse(second["created"])
        self.assertEqual(second["project_id"], first["project_id"])
        self.assertEqual(Project.query.count(), 2)
        self.assertEqual(ProjectMineBinding.query.count(), 2)

    def test_allows_project_with_empty_region_on_rerun(self):
        self._write_kml(
            [
                ("Mine 1", {"FID_1": "1", "SHENG": "云南省"}),
                ("Mine 2", {"FID_1": "2", "SHENG": "云南省"}),
            ]
        )
        first = self._seed()
        db.session.add(Project(name="待补区域项目", region="  ", status="draft"))
        db.session.commit()

        second = self._seed()

        self.assertFalse(second["created"])
        self.assertEqual(second["project_id"], first["project_id"])
        self.assertEqual(Project.query.count(), 2)

    def test_allows_kunming_subregion_project_on_rerun(self):
        self._write_kml(
            [
                ("Mine 1", {"FID_1": "1", "SHENG": "云南省"}),
                ("Mine 2", {"FID_1": "2", "SHENG": "云南省"}),
            ]
        )
        first = self._seed()
        db.session.add(Project(name="kunming", region="昆明", status="draft"))
        db.session.commit()

        second = self._seed()

        self.assertFalse(second["created"])
        self.assertEqual(second["project_id"], first["project_id"])
        self.assertEqual(Project.query.count(), 2)

    def test_finds_renamed_seed_project_by_remark_without_overwriting_user_changes(self):
        self._write_kml(
            [
                ("Mine 1", {"FID_1": "1", "SHENG": "云南省", "SHI": "昆明市"}),
                ("Mine 2", {"FID_1": "2", "SHENG": "云南省"}),
            ]
        )
        first = self._seed()
        project = db.session.get(Project, first["project_id"])
        project.name = "用户重命名的云南项目"
        project.region = "  "
        project.mines[0].city_snapshot = "用户修改的州市"
        db.session.commit()

        second = self._seed()

        self.assertFalse(second["created"])
        self.assertEqual(second["project_id"], first["project_id"])
        self.assertEqual(Project.query.count(), 1)
        self.assertEqual(project.name, "用户重命名的云南项目")
        self.assertEqual(project.region, "  ")
        self.assertEqual(project.mines[0].city_snapshot, "用户修改的州市")
        self.assertEqual(ProjectMineBinding.query.count(), 2)

    def test_rejects_kml_containing_non_yunnan_mine_without_writing(self):
        self._write_kml(
            [
                ("Mine 1", {"FID_1": "1", "SHENG": "云南省"}),
                ("Mine 2", {"FID_1": "2", "SHENG": "江西省"}),
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "非云南矿山"):
            self._seed()

        self.assertEqual(Project.query.count(), 0)
        self.assertEqual(ProjectMineBinding.query.count(), 0)

    def test_rejects_unexpected_valid_mine_count_without_writing(self):
        self._write_kml(
            [("Mine 1", {"FID_1": "1", "SHENG": "云南省"})]
        )

        with self.assertRaisesRegex(RuntimeError, "预期 565"):
            self._seed(expected_count=565)

        self.assertEqual(Project.query.count(), 0)
        self.assertEqual(ProjectMineBinding.query.count(), 0)

    def test_rejects_existing_yunnan_project_with_wrong_region(self):
        project = Project(name=PROJECT_NAME, region="江西省", status="active")
        db.session.add(project)
        db.session.commit()
        self._write_kml(
            [("Mine 1", {"FID_1": "1", "SHENG": "云南省"})]
        )

        with self.assertRaises(RuntimeError):
            self._seed(expected_count=1)

        self.assertEqual(Project.query.count(), 1)
        self.assertEqual(ProjectMineBinding.query.count(), 0)


if __name__ == "__main__":
    unittest.main()
