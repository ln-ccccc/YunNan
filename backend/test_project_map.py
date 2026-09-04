import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications import create_app
from applications.extensions import db
from applications.models.project import ProjectMineBinding
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
                                "features": [
                                    {
                                        "type": "Feature",
                                        "properties": {"FID_1": fid},
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
                db.session.commit()

                self.assertEqual(get_project_geojson(first["id"])["features"][0]["properties"]["FID_1"], 1)
                self.assertEqual(get_project_geojson(second["id"])["features"][0]["properties"]["FID_1"], 2)
                manifest = get_project_map_manifest(second["id"])
                self.assertTrue(manifest["map_ready"])
                self.assertIn(f"/tiles/projects/{second['id']}/", manifest["tile_url"])
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
        self.assertIn(f"/api/projects/{first['id']}/", payload["data"][0]["after_img"])
        self.assertNotIn("202+2022.png", payload["data"][0]["after_img"])


if __name__ == "__main__":
    unittest.main()
