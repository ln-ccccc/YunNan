import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.classification_result import ClassificationResult
from applications.models.project import ProjectDataset, ProjectMineBinding
from applications.models.project_spatial import ProjectSpatialResource
from applications.project_hub.service import create_project


class TestProjectMapIsolation(unittest.TestCase):
    def setUp(self):
        self.app = create_app("testing")
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    def test_geojson_and_manifest_use_only_requested_project(self):
        from applications.project_hub.project_map import get_project_geojson, get_project_map_manifest

        first = create_project({"name": "Dali", "region": "Dali"})
        second = create_project({"name": "Kunming", "region": "Kunming"})
        previous_root = os.environ.get("PROJECT_STORAGE_ROOT")
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["PROJECT_STORAGE_ROOT"] = temp_dir
            try:
                for project, fid, longitude in ((first, 1, 100.0), (second, 2, 102.0)):
                    mine_dir = os.path.join(temp_dir, "projects", str(project["id"]), "mines", str(fid))
                    os.makedirs(mine_dir)
                    relative = f"projects/{project['id']}/mines/{fid}/mines.geojson"
                    with open(os.path.join(temp_dir, relative), "w", encoding="utf-8") as stream:
                        json.dump(
                            {
                                "type": "FeatureCollection",
                                "source_path": "projects/1/mines/1/mines.geojson",
                                "features": [
                                    {
                                        "type": "Feature",
                                        "output_dir": "D:/private/output",
                                        "properties": {
                                            "FID_1": fid,
                                            "file_path": "D:/private/mine.geojson",
                                            "metadata": {
                                                "source_path": "projects/1/mines/1/source.geojson",
                                                "label": "保留的公开属性",
                                            },
                                            "nested": [[{"tile_path": "projects/1/tiles/1", "label": "嵌套公开属性"}]],
                                        },
                                        "geometry": {
                                            "type": "Polygon",
                                            "coordinates": [[[longitude, 25], [longitude + 0.1, 25], [longitude, 25.1], [longitude, 25]]],
                                        },
                                    }
                                ],
                            },
                            stream,
                        )
                    db.session.add(
                        ProjectSpatialResource(
                            project_id=project["id"],
                            resource_type="mine_vector",
                            version=1,
                            status="active",
                            source_path=relative,
                            normalized_path=relative,
                            source_format="geojson",
                            bounds_json=json.dumps([longitude, 25, longitude + 0.1, 25.1]),
                        )
                    )
                    db.session.add(
                        ProjectSpatialResource(
                            project_id=project["id"],
                            resource_type="basemap",
                            version=1,
                            status="active",
                            source_path=f"projects/{project['id']}/basemaps/{fid}/source.tif",
                            tile_path=f"projects/{project['id']}/tiles/{fid}",
                            source_format="tif",
                            bounds_json=json.dumps([longitude, 25, longitude + 0.1, 25.1]),
                            min_zoom=8,
                            max_zoom=15,
                        )
                    )
                    tile_dir = Path(temp_dir) / f"projects/{project['id']}/tiles/{fid}/8/0"
                    tile_dir.mkdir(parents=True)
                    (tile_dir.parent.parent / ".active").write_text(str(fid), encoding="ascii")
                    (tile_dir / "0.png").write_bytes(b"png-tile")
                db.session.commit()

                first_geojson = get_project_geojson(first["id"])
                first_properties = first_geojson["features"][0]["properties"]
                self.assertNotIn("source_path", first_geojson)
                self.assertNotIn("output_dir", first_geojson["features"][0])
                self.assertEqual(first_properties["FID_1"], 1)
                self.assertNotIn("file_path", first_properties)
                self.assertEqual(first_properties["metadata"], {"label": "保留的公开属性"})
                self.assertEqual(first_properties["nested"], [[{"label": "嵌套公开属性"}]])
                self.assertEqual(get_project_geojson(second["id"])["features"][0]["properties"]["FID_1"], 2)
                manifest = get_project_map_manifest(second["id"])
                self.assertTrue(manifest["map_ready"])
                self.assertIn(f"/tiles/projects/{second['id']}/", manifest["tile_url"])
                self.assertIn(f"/api/projects/{second['id']}/", manifest["api_tile_url"])
            finally:
                if previous_root is None:
                    os.environ.pop("PROJECT_STORAGE_ROOT", None)
                else:
                    os.environ["PROJECT_STORAGE_ROOT"] = previous_root

    def test_manifest_omits_tile_templates_without_all_active_tile_artifacts(self):
        from applications.project_hub.project_map import get_project_map_manifest

        previous_root = os.environ.get("PROJECT_STORAGE_ROOT")
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["PROJECT_STORAGE_ROOT"] = temp_dir
            try:
                for suffix, tile_path_kind in (
                    ("no-path", "no-path"),
                    ("no-marker", "no-marker"),
                    ("no-png", "no-png"),
                ):
                    project = create_project({"name": suffix, "region": "Kunming"})
                    tile_path = None
                    if tile_path_kind != "no-path":
                        tile_path = f"projects/{project['id']}/tiles/1"
                    db.session.add(
                        ProjectSpatialResource(
                            project_id=project["id"],
                            resource_type="basemap",
                            version=1,
                            status="active",
                            source_path=f"projects/{project['id']}/basemaps/1/source.tif",
                            tile_path=tile_path,
                            source_format="tif",
                            bounds_json=json.dumps([100.0, 25.0, 100.1, 25.1]),
                            min_zoom=8,
                            max_zoom=15,
                        )
                    )
                    if tile_path_kind == "no-marker":
                        tile_file = Path(temp_dir) / tile_path / "8/0/0.png"
                        tile_file.parent.mkdir(parents=True)
                        tile_file.write_bytes(b"png-tile")
                    elif tile_path_kind == "no-png":
                        tile_dir = Path(temp_dir) / tile_path
                        tile_dir.mkdir(parents=True)
                        (tile_dir / ".active").write_text("1", encoding="ascii")
                    db.session.commit()

                    manifest = get_project_map_manifest(project["id"])

                    with self.subTest(tile_path_kind=tile_path_kind):
                        self.assertIsNone(manifest["tile_url"])
                        self.assertIsNone(manifest["api_tile_url"])
            finally:
                if previous_root is None:
                    os.environ.pop("PROJECT_STORAGE_ROOT", None)
                else:
                    os.environ["PROJECT_STORAGE_ROOT"] = previous_root

    def test_project_without_active_mines_does_not_fallback(self):
        from applications.project_hub.project_map import get_project_geojson

        project = create_project({"name": "Empty", "region": "Kunming"})
        with self.assertRaisesRegex(ValueError, "暂无项目数据"):
            get_project_geojson(project["id"])


class TestProjectInferenceOutputFiles(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_root = os.environ.get("PROJECT_STORAGE_ROOT")
        os.environ["PROJECT_STORAGE_ROOT"] = self.temp_dir.name
        self.app = create_app("testing")
        self.client = self.app.test_client()
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        from applications.auth.service import sync_admin_from_env

        self.app.config["ADMIN_USERNAME"] = "admin"
        self.app.config["ADMIN_PASSWORD"] = "Secret123!"
        sync_admin_from_env()
        response = self.client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "Secret123!"},
        )
        self.assertEqual(response.status_code, 200)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.ctx.pop()
        if self.previous_root is None:
            os.environ.pop("PROJECT_STORAGE_ROOT", None)
        else:
            os.environ["PROJECT_STORAGE_ROOT"] = self.previous_root
        self.temp_dir.cleanup()

    def test_bound_project_output_image_is_served(self):
        project = create_project({"name": "Dali", "region": "Dali"})
        db.session.add(
            ProjectMineBinding(
                project_id=project["id"],
                mine_fid=101,
                sort_order=0,
            )
        )
        db.session.commit()
        output = (
            Path(self.temp_dir.name)
            / f"projects/{project['id']}/outputs/inference/101/101+2022.png"
        )
        output.parent.mkdir(parents=True)
        output.write_bytes(b"project-image")

        response = self.client.get(
            f"/api/projects/{project['id']}/outputs/inference/101/101+2022.png"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"project-image")
        self.assertEqual(response.mimetype, "image/png")
        response.close()

    def test_bound_project_output_label_geotiff_is_served_as_binary(self):
        project = create_project({"name": "Dali", "region": "Dali"})
        db.session.add(
            ProjectMineBinding(
                project_id=project["id"],
                mine_fid=101,
                sort_order=0,
            )
        )
        db.session.commit()
        label_bytes = b"II*\x00georeferenced-label"
        output = (
            Path(self.temp_dir.name)
            / f"projects/{project['id']}/outputs/inference/101/101+2022_label.tif"
        )
        output.parent.mkdir(parents=True)
        output.write_bytes(label_bytes)

        response = self.client.get(
            f"/api/projects/{project['id']}/outputs/inference/101/101+2022_label.tif"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, label_bytes)
        response.close()

    def test_bound_project_output_rejects_non_label_geotiff(self):
        project = create_project({"name": "Dali", "region": "Dali"})
        db.session.add(
            ProjectMineBinding(
                project_id=project["id"],
                mine_fid=101,
                sort_order=0,
            )
        )
        db.session.commit()
        output = (
            Path(self.temp_dir.name)
            / f"projects/{project['id']}/outputs/inference/101/101+2022.tif"
        )
        output.parent.mkdir(parents=True)
        output.write_bytes(b"unexpected-tiff")

        response = self.client.get(
            f"/api/projects/{project['id']}/outputs/inference/101/101+2022.tif"
        )

        self.assertEqual(response.status_code, 400)
        response.close()

    def test_project_output_rejects_unbound_fid_and_requires_login(self):
        project = create_project({"name": "Dali", "region": "Dali"})
        db.session.add(
            ProjectMineBinding(
                project_id=project["id"],
                mine_fid=101,
                sort_order=0,
            )
        )
        db.session.commit()
        path = f"/api/projects/{project['id']}/outputs/inference/202/202+2022.png"

        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.app.test_client().get(path).status_code, 401)

    def test_interpretation_history_lists_only_requested_project_outputs(self):
        first = create_project({"name": "Dali", "region": "Dali"})
        second = create_project({"name": "Kunming", "region": "Kunming"})
        for project, fid in ((first, 101), (second, 202)):
            db.session.add(
                ProjectMineBinding(
                    project_id=project["id"],
                    mine_fid=fid,
                    sort_order=0,
                )
            )
            output = (
                Path(self.temp_dir.name)
                / f"projects/{project['id']}/outputs/inference/{fid}/{fid}+2022.png"
            )
            output.parent.mkdir(parents=True)
            output.write_bytes(b"project-image")
        db.session.commit()

        response = self.client.get(
            f"/api/analysis/kml_roi_history?project_id={first['id']}"
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.data.decode("utf-8"))
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["data"][0]["data"]["fid"], 101)
        self.assertIsNone(payload["data"][0]["data"]["result_id"])
        self.assertIsNone(payload["data"][0]["data"]["vector_status"])
        self.assertIsNone(payload["data"][0]["data"]["vector_error"])
        self.assertIn(f"/api/projects/{first['id']}/", payload["data"][0]["after_img"])
        self.assertNotIn("202+2022.png", payload["data"][0]["after_img"])

    def test_interpretation_history_includes_persisted_classification_result_link(self):
        project = create_project({"name": "Dali", "region": "Dali"})
        db.session.add(ProjectMineBinding(project_id=project["id"], mine_fid=101, sort_order=0))
        output = (
            Path(self.temp_dir.name)
            / f"projects/{project['id']}/outputs/inference/101/101+2024.png"
        )
        output.parent.mkdir(parents=True)
        output.write_bytes(b"project-image")
        result = ClassificationResult(
            project_id=project["id"],
            mine_fid=101,
            year=2024,
            inference_job_id="job-history-link",
            model_id="cc-ln/CUGRS",
            mine_resource_id=1,
            vector_status="vector_failed",
            vector_error="label_missing",
        )
        db.session.add(result)
        db.session.flush()
        db.session.add(
            ProjectDataset(
                project_id=project["id"],
                dataset_kind="inference_result",
                display_name="地物分类 FID 101 2024",
                file_path=str(output.parent),
                source_format="classification_dir",
                mine_fid=101,
                year_start=2024,
                year_end=2024,
                slice_config_json=json.dumps(
                    {"classification_result_id": result.id},
                    ensure_ascii=False,
                ),
            )
        )
        db.session.commit()

        response = self.client.get(
            f"/api/analysis/kml_roi_history?project_id={project['id']}"
        )

        self.assertEqual(response.status_code, 200)
        data = response.get_json()["data"][0]["data"]
        self.assertEqual(data["result_id"], result.id)
        self.assertEqual(data["vector_status"], "vector_failed")
        self.assertEqual(data["vector_error"], "label_missing")


if __name__ == "__main__":
    unittest.main()
