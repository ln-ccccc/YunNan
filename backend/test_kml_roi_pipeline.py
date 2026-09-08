import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.kml_roi.pipeline import run_kml_roi_pipeline


class TestKmlRoiPipeline(unittest.TestCase):
    def _paths(self, root):
        old_tif = root / "old.tif"
        new_tif = root / "new.tif"
        old_tif.touch()
        new_tif.touch()
        vector_path = root / "mines.geojson"
        vector_path.write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "properties": {"FID_1": 101},
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [[[100, 25], [101, 25], [101, 26], [100, 25]]],
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return old_tif, new_tif, vector_path

    def test_pipeline_loads_geojson_project_vectors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_tif, new_tif, vector_path = self._paths(root)
            prepare = Mock(
                return_value=(
                    ["101"],
                    ["101+2022_tile.png"],
                    {"101": [{"dst_base": "101+2022"}]},
                )
            )
            with (
                patch(
                    "applications.kml_roi.pipeline.raster_union_bounds_4326",
                    return_value=(99, 24, 102, 27),
                ),
                patch(
                    "applications.kml_roi.pipeline.filter_features_by_bounds",
                    side_effect=lambda features, _: features,
                ),
                patch("applications.kml_roi.pipeline.prepare_tiles", prepare),
                patch(
                    "applications.kml_roi.pipeline.distribute_outputs",
                    return_value={
                        "written_fids": 1,
                        "written_fid_list": ["101"],
                        "missing_fids": [],
                    },
                ),
            ):
                result = run_kml_roi_pipeline(
                    old_tif=old_tif,
                    new_tif=new_tif,
                    kml_path=vector_path,
                    output_root=root / "outputs",
                    work_dir=root / "work",
                    model_id="cc-ln/CUGRS",
                    device="cpu",
                    year="2022",
                    keep_workdir=True,
                    tile_runner=Mock(return_value=([], {})),
                )

        self.assertEqual(result["status"], "succeeded")
        loaded_features = prepare.call_args.args[2]
        self.assertEqual([fid for fid, _ in loaded_features], ["101"])

    def test_pipeline_filters_to_explicit_selected_fids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_tif, new_tif, vector_path = self._paths(root)
            payload = json.loads(vector_path.read_text(encoding="utf-8"))
            second = dict(payload["features"][0])
            second["properties"] = {"FID_1": 202}
            payload["features"].append(second)
            vector_path.write_text(json.dumps(payload), encoding="utf-8")
            prepare = Mock(
                return_value=(
                    ["101"],
                    ["101+2022_tile.png"],
                    {"101": [{"dst_base": "101+2022"}]},
                )
            )
            with (
                patch(
                    "applications.kml_roi.pipeline.raster_union_bounds_4326",
                    return_value=(99, 24, 102, 27),
                ),
                patch(
                    "applications.kml_roi.pipeline.filter_features_by_bounds",
                    side_effect=lambda features, _: features,
                ),
                patch("applications.kml_roi.pipeline.prepare_tiles", prepare),
                patch(
                    "applications.kml_roi.pipeline.distribute_outputs",
                    return_value={
                        "written_fids": 1,
                        "written_fid_list": ["101"],
                        "missing_fids": [],
                    },
                ),
            ):
                run_kml_roi_pipeline(
                    old_tif=old_tif,
                    new_tif=new_tif,
                    kml_path=vector_path,
                    output_root=root / "outputs",
                    work_dir=root / "work",
                    model_id="cc-ln/CUGRS",
                    device="cpu",
                    year="2022",
                    selected_fids=[101],
                    keep_workdir=True,
                    tile_runner=Mock(return_value=([], {})),
                )

        loaded_features = prepare.call_args.args[2]
        self.assertEqual([fid for fid, _ in loaded_features], ["101"])

    def test_pipeline_reports_stage_durations_in_summary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_tif, new_tif, vector_path = self._paths(root)
            with (
                patch(
                    "applications.kml_roi.pipeline.raster_union_bounds_4326",
                    return_value=(99, 24, 102, 27),
                ),
                patch(
                    "applications.kml_roi.pipeline.filter_features_by_bounds",
                    side_effect=lambda features, _: features,
                ),
                patch(
                    "applications.kml_roi.pipeline.prepare_tiles",
                    Mock(return_value=(["101"], ["101+2022_tile.png"], {"101": [{"dst_base": "101+2022"}]})),
                ),
                patch(
                    "applications.kml_roi.pipeline.distribute_outputs",
                    return_value={
                        "written_fids": 1,
                        "written_fid_list": ["101"],
                        "missing_fids": [],
                    },
                ),
            ):
                result = run_kml_roi_pipeline(
                    old_tif=old_tif,
                    new_tif=new_tif,
                    kml_path=vector_path,
                    output_root=root / "outputs",
                    work_dir=root / "work",
                    model_id="cc-ln/CUGRS",
                    device="cpu",
                    year="2022",
                    keep_workdir=True,
                    tile_runner=Mock(return_value=([], {})),
                )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(
            set(result["stage_durations"]),
            {"prep_dirs", "kml_load", "bounds_filter", "tiles", "inference", "distribute"},
        )
        self.assertGreaterEqual(result["total_seconds"], 0.0)


if __name__ == "__main__":
    unittest.main()
