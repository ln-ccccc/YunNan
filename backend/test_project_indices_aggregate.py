"""项目级光谱指数聚合测试：indices 产物 → 项目 stats 的 ndviStats。

覆盖：全量可用聚合、部分缺失容错（缺失矿山计 no_data）、
无产物项目（全缺失）、stats 接入（/stats 响应含真实均值）。
"""

import json

from test_project_api import TestProjectAPI


class TestProjectIndicesAggregate(TestProjectAPI):
    def _write_indices(self, project_id, fid, mean, mk="upward", years=(2017, 2019, 2021)):
        data = [{"year": y, "value": round(mean + 0.01 * i, 4)} for i, y in enumerate(years)]
        path = (
            self.storage_root
            / "projects" / str(project_id) / "outputs" / "indices" / f"{fid}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "fid": fid,
            "ndvi": {
                "data": data,
                "mean": mean,
                "trend": 0.01,
                "mk_trend": mk,
                "available": True,
                "source_file": "NDVI_2year.xlsx",
            },
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _project_mine_count(self, project_id):
        # geojson 无矿山绑定不影响聚合——聚合按项目矿山绑定走，测试项目无 KML
        # 绑定时 fids 为空 → 全缺失路径。这里直接读响应验证。
        response = self.client.get(f"/api/projects/{project_id}/stats")
        return self._json(response)

    def test_aggregate_over_available_indices(self):
        self.login_as_admin()
        project_id = self._create_project("光谱聚合项目")
        for fid, mean in [(101, 0.30), (102, 0.40), (103, 0.50)]:
            self._write_indices(project_id, fid, mean, mk="upward" if fid != 102 else "downward")

        from applications.project_hub.project_map import _aggregate_project_indices

        result = _aggregate_project_indices(project_id, [101, 102, 103])
        stats = result["ndviStats"]
        self.assertEqual(stats["available_mine_count"], 3)
        self.assertAlmostEqual(stats["mean"], 0.4, places=3)
        self.assertEqual(stats["mk_trend_counter"]["upward"], 2)
        self.assertEqual(stats["mk_trend_counter"]["downward"], 1)
        self.assertTrue(stats["yearly"], "应有逐年均值序列")

    def test_aggregate_counts_missing_mines_as_no_data(self):
        self.login_as_admin()
        project_id = self._create_project("光谱部分缺失项目")
        self._write_indices(project_id, 201, 0.35)

        from applications.project_hub.project_map import _aggregate_project_indices

        result = _aggregate_project_indices(project_id, [201, 202, 203])
        stats = result["ndviStats"]
        self.assertEqual(stats["available_mine_count"], 1)
        self.assertEqual(stats["mk_trend_counter"]["no_data"], 2)
        self.assertAlmostEqual(stats["mean"], 0.35, places=3)

    def test_aggregate_empty_when_no_indices(self):
        self.login_as_admin()
        project_id = self._create_project("光谱无产物项目")

        from applications.project_hub.project_map import _aggregate_project_indices

        result = _aggregate_project_indices(project_id, [301, 302])
        stats = result["ndviStats"]
        self.assertEqual(stats["available_mine_count"], 0)
        self.assertEqual(stats["mean"], 0)
        self.assertEqual(stats["yearly"], [])

    def test_stats_endpoint_exposes_real_ndvi_stats(self):
        """端到端：矿山资源 + indices 产物 → /stats 下发真实 ndviStats。"""
        self.login_as_admin()
        project_id = self._create_project("stats 接入项目")

        # 建矿山资源（1 座矿山 fid=401）
        mine_resource_path = f"projects/{project_id}/mines/1/mines.geojson"
        normalized_mine_path = self.storage_root / mine_resource_path
        normalized_mine_path.parent.mkdir(parents=True, exist_ok=True)
        normalized_mine_path.write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[100.0, 25.0], [100.1, 25.0], [100.1, 25.1], [100.0, 25.1], [100.0, 25.0]]],
                            },
                            "properties": {"OBJECTID": 401, "name": "聚合矿山"},
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        from applications.extensions import db
        from applications.models.project import ProjectMineBinding
        from applications.models.project_spatial import ProjectSpatialResource

        db.session.add(
            ProjectSpatialResource(
                project_id=project_id,
                resource_type="mine_vector",
                version=1,
                status="active",
                source_path=mine_resource_path,
                normalized_path=mine_resource_path,
                source_format="geojson",
            )
        )
        db.session.add(
            ProjectMineBinding(
                project_id=project_id,
                mine_fid=401,
                sort_order=0,
            )
        )
        db.session.commit()

        self._write_indices(project_id, 401, 0.42)

        response = self.client.get(f"/api/projects/{project_id}/stats")
        self.assertEqual(response.status_code, 200)
        body = self._json(response)
        ndvi_stats = body["data"]["ndviStats"]
        self.assertEqual(ndvi_stats["available_mine_count"], 1)
        self.assertAlmostEqual(ndvi_stats["mean"], 0.42, places=3)
        self.assertIn("yearly", ndvi_stats)
        self.assertIn("mk_trend_counter", ndvi_stats)
