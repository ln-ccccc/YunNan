import importlib.util
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import numpy as np


def load_runner_module():
    module_path = Path(__file__).parent / "applications" / "kml_roi" / "inference_runner.py"
    spec = importlib.util.spec_from_file_location("inference_runner", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_caller_module():
    package_names = ("applications", "applications.common")
    previous = {name: sys.modules.get(name) for name in package_names}
    previous_path_global = sys.modules.get("applications.common.path_global")
    try:
        for name in package_names:
            package = types.ModuleType(name)
            package.__path__ = []
            sys.modules[name] = package
        path_global = types.ModuleType("applications.common.path_global")
        path_global.generate_url = "/generated/"
        sys.modules["applications.common.path_global"] = path_global

        module_path = Path(__file__).parent / "applications" / "interface" / "mmseg_inference_caller.py"
        spec = importlib.util.spec_from_file_location("mmseg_inference_caller_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if previous_path_global is None:
            sys.modules.pop("applications.common.path_global", None)
        else:
            sys.modules["applications.common.path_global"] = previous_path_global


def load_service_module():
    package_names = ("applications", "applications.kml_roi")
    previous = {name: sys.modules.get(name) for name in package_names}
    previous_merge = sys.modules.get("applications.kml_roi.kml_merge")
    try:
        for name in package_names:
            package = types.ModuleType(name)
            package.__path__ = []
            sys.modules[name] = package
        merge_module = types.ModuleType("applications.kml_roi.kml_merge")
        merge_module.merge_kml_increment = Mock()
        sys.modules["applications.kml_roi.kml_merge"] = merge_module

        module_path = Path(__file__).parent / "applications" / "kml_roi" / "service.py"
        spec = importlib.util.spec_from_file_location("kml_roi_service_test", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if previous_merge is None:
            sys.modules.pop("applications.kml_roi.kml_merge", None)
        else:
            sys.modules["applications.kml_roi.kml_merge"] = previous_merge


def load_worker_module():
    module_name = "inference_worker_test"
    module_path = Path(__file__).parent / "applications" / "inference" / "worker.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(module_name, None)


class TestInferenceRunner(unittest.TestCase):
    def test_default_model_loader_uses_inference_checkpoint_and_device(self):
        worker_module = load_worker_module()
        caller_module = types.ModuleType("applications.interface.mmseg_inference_caller")
        caller_module.get_model_paths = Mock(return_value=("config.py", "model.inference.pth"))
        segmentation_module = types.ModuleType("applications.interface.mmseg_segmentation")
        loaded_model = object()
        segmentation_module.load_model = Mock(return_value=loaded_model)
        module_names = (
            "applications.interface.mmseg_inference_caller",
            "applications.interface.mmseg_segmentation",
        )
        previous = {name: sys.modules.get(name) for name in module_names}
        try:
            sys.modules[module_names[0]] = caller_module
            sys.modules[module_names[1]] = segmentation_module

            result = worker_module._default_model_loader("cuda:0")
        finally:
            for name, module in previous.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        caller_module.get_model_paths.assert_called_once_with(
            "cc-ln/CUGRS",
            require_inference_checkpoint=True,
        )
        segmentation_module.load_model.assert_called_once_with(
            "config.py",
            "model.inference.pth",
            device="cuda:0",
        )
        self.assertIs(result, loaded_model)

    def test_model_forward_smoke_executes_injected_runner(self):
        worker_module = load_worker_module()
        model = object()
        runner = Mock()

        worker_module.run_model_forward_smoke_test(model, inference_runner=runner)

        runner.assert_called_once_with(model)

    def test_model_forward_smoke_reports_stable_failure_text(self):
        worker_module = load_worker_module()

        with self.assertRaisesRegex(RuntimeError, "model forward smoke failed"):
            worker_module.run_model_forward_smoke_test(
                object(),
                inference_runner=Mock(side_effect=RuntimeError("bad forward")),
            )

    def test_run_mmseg_tiles_calls_execute_once_for_all_files(self):
        caller = Mock()
        caller.execute.return_value = {
            "status": "completed",
            "results": [
                {"input_name": "a.png", "status": "success"},
                {"input_name": "b.png", "status": "error", "error": "bad tile"},
            ],
        }

        failed, errors = load_runner_module().run_mmseg_tiles(
            model_id="cc-ln/CUGRS",
            data_path="tiles",
            out_dir="out",
            file_names=["a.png", "b.png"],
            device="cpu",
            caller=caller,
        )

        caller.execute.assert_called_once_with(
            model_id="cc-ln/CUGRS",
            data_path="tiles",
            out_dir="out",
            names=["a.png", "b.png"],
            device="cpu",
            return_details=True,
        )
        self.assertEqual(failed, ["b.png"])
        self.assertEqual(errors, {"b.png": "bad tile"})

    def test_caller_returns_normalized_details_when_requested(self):
        caller = load_caller_module()
        original_run = caller.subprocess.run
        payload = {
            "status": "completed",
            "results": [
                {"input_name": "a.png", "name": "pred_a.png", "mask_name": "mask_a.png", "status": "success"},
                {"input_name": "b.png", "name": "b.png", "status": "error", "error": "bad tile"},
            ],
        }
        caller.get_model_paths = Mock(return_value=("config.py", "model.pth"))
        caller._resolve_mmseg_python = Mock(return_value=["python"])
        caller._build_mmseg_env = Mock(return_value={})
        completed_process = types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )
        with patch.object(
            caller.subprocess,
            "run",
            return_value=completed_process,
        ):
            result = caller.execute(
                model_id="cc-ln/CUGRS",
                data_path="tiles",
                out_dir="out",
                names=["a.png", "b.png"],
                device="cpu",
                return_details=True,
            )

        self.assertIs(caller.subprocess.run, original_run)
        self.assertEqual(result["results"][0]["output_name"], "pred_a.png")
        self.assertEqual(result["results"][1]["input_name"], "b.png")
        self.assertEqual(result["results"][1]["error"], "bad tile")

    def test_development_model_path_reports_both_missing_checkpoints(self):
        caller = load_caller_module()
        original_config = caller.CUGRS_CONFIG
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            inference_checkpoint = temp_path / "model.inference.pth"
            training_checkpoint = temp_path / "model.pth"
            caller.CUGRS_CONFIG = {
                "model_id": "cc-ln/CUGRS",
                "config_path": str(temp_path / "config.py"),
                "inference_checkpoint_path": str(inference_checkpoint),
                "checkpoint_path": str(training_checkpoint),
            }
            try:
                with self.assertRaises(FileNotFoundError) as caught:
                    caller.get_model_paths("cc-ln/CUGRS")
            finally:
                caller.CUGRS_CONFIG = original_config

        message = str(caught.exception)
        self.assertIn("MODEL_CHECKPOINT_MISSING", message)
        self.assertIn(str(inference_checkpoint.resolve()), message)
        self.assertIn(str(training_checkpoint.resolve()), message)

    def test_caller_prefers_inference_only_checkpoint_when_present(self):
        caller = load_caller_module()

        _, checkpoint_path = caller.get_model_paths("cc-ln/CUGRS")

        self.assertEqual(Path(checkpoint_path).name, "model.inference.pth")
        self.assertTrue(Path(checkpoint_path).is_file())

    def test_production_model_path_requires_inference_checkpoint(self):
        caller = load_caller_module()
        original_config = caller.CUGRS_CONFIG
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            training_checkpoint = temp_path / "model.pth"
            training_checkpoint.touch()
            caller.CUGRS_CONFIG = {
                "model_id": "cc-ln/CUGRS",
                "config_path": str(temp_path / "config.py"),
                "inference_checkpoint_path": str(temp_path / "model.inference.pth"),
                "checkpoint_path": str(training_checkpoint),
            }
            try:
                with self.assertRaisesRegex(FileNotFoundError, "MODEL_CHECKPOINT_MISSING"):
                    caller.get_model_paths(
                        "cc-ln/CUGRS",
                        require_inference_checkpoint=True,
                    )
            finally:
                caller.CUGRS_CONFIG = original_config

    def test_development_model_path_can_fall_back_to_training_checkpoint(self):
        caller = load_caller_module()
        original_config = caller.CUGRS_CONFIG
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            training_checkpoint = temp_path / "model.pth"
            training_checkpoint.touch()
            caller.CUGRS_CONFIG = {
                "model_id": "cc-ln/CUGRS",
                "config_path": str(temp_path / "config.py"),
                "inference_checkpoint_path": str(temp_path / "model.inference.pth"),
                "checkpoint_path": str(training_checkpoint),
            }
            try:
                _, checkpoint_path = caller.get_model_paths("cc-ln/CUGRS")
            finally:
                caller.CUGRS_CONFIG = original_config

        self.assertEqual(checkpoint_path, str(training_checkpoint.resolve()))

    def test_caller_does_not_modify_installed_mmdet_at_runtime(self):
        caller = load_caller_module()

        self.assertFalse(hasattr(caller, "_patch_mmdet_mmcv_guard"))

    def test_parse_last_json_rejects_missing_json(self):
        with self.assertRaisesRegex(RuntimeError, "未返回有效 JSON"):
            load_service_module()._parse_last_json("plain log output")

    def test_worker_reuses_loaded_model_across_jobs(self):
        worker_module = load_worker_module()
        resolution = types.SimpleNamespace(
            requested="auto",
            effective="cuda:0",
            fallback_reason=None,
            warnings=(),
        )
        resolver = Mock()
        resolver.resolve.return_value = resolution
        model_loader = Mock(return_value=object())
        pipeline_runner = Mock(return_value={"status": "succeeded", "written_fids": 1})
        job_store = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = worker_module.InferenceWorker(
                device_resolver=resolver,
                model_loader=model_loader,
                inference_fn=Mock(),
                pipeline_runner=pipeline_runner,
                job_store=job_store,
                runtime_root=Path(temp_dir),
            )
            worker.initialize()
            for job_id in ("job-a", "job-b"):
                job = types.SimpleNamespace(
                    id=job_id,
                    status="running",
                    cancel_requested=False,
                    request_payload_json=json.dumps(
                        {
                            "old_tif_path": "old.tif",
                            "new_tif_path": "new.tif",
                            "kml_path": "roi.kml",
                            "output_root": "outputs",
                        }
                    ),
                )
                worker.run_job(job)

        model_loader.assert_called_once_with("cuda:0")
        self.assertEqual(pipeline_runner.call_count, 2)

    def test_project_worker_stages_outputs_and_passes_selected_fids(self):
        worker_module = load_worker_module()
        pipeline_runner = Mock(return_value={"status": "succeeded"})
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            worker = worker_module.InferenceWorker(
                pipeline_runner=pipeline_runner,
                job_store=Mock(),
                runtime_root=root,
            )
            worker.resolution = types.SimpleNamespace(effective="cpu")
            work_dir = root / "job-work"
            worker._run_pipeline(
                {
                    "project_id": 7,
                    "old_tif_path": "old.tif",
                    "new_tif_path": "new.tif",
                    "kml_path": "mines.geojson",
                    "output_root": str(root / "project-output"),
                    "mine_fids": [101, 102],
                },
                work_dir,
            )

        kwargs = pipeline_runner.call_args.kwargs
        self.assertEqual(kwargs["output_root"], work_dir / "staged_outputs")
        self.assertEqual(kwargs["selected_fids"], [101, 102])
        self.assertTrue(kwargs["keep_workdir"])

    def test_project_worker_publishes_successful_results_before_finishing_job(self):
        worker_module = load_worker_module()
        publisher = Mock(
            return_value={
                "synced_fids": [101],
                "display_results": [
                    {
                        "fid": 101,
                        "year": 2022,
                        "before_img": "/before.png",
                        "after_img": "/after.png",
                    }
                ],
                "classification_results": [],
            }
        )
        job_store = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = worker_module.InferenceWorker(
                device_resolver=Mock(),
                model_loader=Mock(),
                inference_fn=Mock(),
                pipeline_runner=Mock(
                    return_value={
                        "status": "succeeded",
                        "written_fid_list": ["101"],
                        "output_root": str(Path(temp_dir) / "job-project" / "staged_outputs"),
                    }
                ),
                project_result_publisher=publisher,
                job_store=job_store,
                runtime_root=Path(temp_dir),
            )
            worker.model = object()
            worker.resolution = types.SimpleNamespace(
                requested="cpu",
                effective="cpu",
                fallback_reason=None,
                warnings=(),
            )
            payload = {
                "project_id": 7,
                "mine_fids": [101],
                "year": "2022",
                "old_tif_path": "old.tif",
                "new_tif_path": "new.tif",
                "kml_path": "mines.geojson",
                "output_root": "project-output",
            }
            job = types.SimpleNamespace(
                id="job-project",
                status="running",
                cancel_requested=False,
                request_payload_json=json.dumps(payload),
            )

            worker.run_job(job)

        publisher.assert_called_once()
        result = job_store.finish_job.call_args.kwargs["result"]
        self.assertEqual(result["routing"]["synced_fids"], [101])
        self.assertEqual(result["display_results"][0]["fid"], 101)

    def test_run_forever_isolates_poisoned_job_and_keeps_serving(self):
        worker_module = load_worker_module()
        publisher = Mock(
            return_value={
                "synced_fids": [101],
                "display_results": [
                    {"fid": 101, "year": 2022, "before_img": "/b.png", "after_img": "/a.png"}
                ],
                "classification_results": [],
            }
        )
        job_store = Mock()
        poisoned = types.SimpleNamespace(
            id="job-poison",
            status="running",
            cancel_requested=False,
            request_payload_json="{not-valid-json",
        )
        good_payload = {
            "project_id": 7,
            "mine_fids": [101],
            "year": "2022",
            "old_tif_path": "old.tif",
            "new_tif_path": "new.tif",
            "kml_path": "mines.geojson",
            "output_root": "project-output",
        }
        good = types.SimpleNamespace(
            id="job-good",
            status="running",
            cancel_requested=False,
            request_payload_json=json.dumps(good_payload),
        )
        queue = [poisoned, good, None]
        claims = {"count": 0}

        def claim_next_job(_worker_id):
            index = claims["count"]
            claims["count"] += 1
            return queue[index] if index < len(queue) else None

        job_store.claim_next_job.side_effect = claim_next_job

        with tempfile.TemporaryDirectory() as temp_dir:
            worker = worker_module.InferenceWorker(
                device_resolver=Mock(),
                model_loader=Mock(),
                inference_fn=Mock(),
                pipeline_runner=Mock(
                    return_value={
                        "status": "succeeded",
                        "written_fid_list": ["101"],
                        "output_root": str(Path(temp_dir) / "job-good" / "staged_outputs"),
                    }
                ),
                project_result_publisher=publisher,
                job_store=job_store,
                runtime_root=Path(temp_dir),
                poll_interval=0,
            )
            worker.initialize = Mock()
            worker.model = object()
            worker.resolution = types.SimpleNamespace(
                requested="cpu",
                effective="cpu",
                fallback_reason=None,
                warnings=(),
            )

            worker.run_forever(should_stop=lambda: claims["count"] >= len(queue))

        finished = {call.args[0].id: call.kwargs for call in job_store.finish_job.call_args_list}
        self.assertEqual(finished["job-poison"]["status"], "failed")
        self.assertEqual(finished["job-poison"]["error_code"], "PAYLOAD_INVALID")
        self.assertEqual(finished["job-good"]["status"], "succeeded")
        publisher.assert_called_once()

    def test_workdir_conflict_marks_job_failed_and_preserves_existing_dir(self):
        worker_module = load_worker_module()
        job_store = Mock()
        payload = {
            "project_id": 7,
            "mine_fids": [101],
            "year": "2022",
            "old_tif_path": "old.tif",
            "new_tif_path": "new.tif",
            "kml_path": "mines.geojson",
            "output_root": "project-output",
        }
        job = types.SimpleNamespace(
            id="job-conflict",
            status="running",
            cancel_requested=False,
            request_payload_json=json.dumps(payload),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_root = Path(temp_dir)
            existing = runtime_root / "job-conflict"
            existing.mkdir()
            marker = existing / "leftover.marker"
            marker.write_text("keep", encoding="utf-8")
            worker = worker_module.InferenceWorker(
                device_resolver=Mock(),
                model_loader=Mock(),
                inference_fn=Mock(),
                pipeline_runner=Mock(),
                project_result_publisher=Mock(),
                job_store=job_store,
                runtime_root=runtime_root,
            )
            worker.model = object()
            worker.resolution = types.SimpleNamespace(
                requested="cpu",
                effective="cpu",
                fallback_reason=None,
                warnings=(),
            )

            worker.run_job(job)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

        kwargs = job_store.finish_job.call_args.kwargs
        self.assertEqual(kwargs["status"], "failed")
        self.assertEqual(kwargs["error_code"], "WORKDIR_CONFLICT")

    def test_worker_honors_cpu_requested_by_single_job_and_restores_gpu_model(self):
        worker_module = load_worker_module()
        resolution = types.SimpleNamespace(
            requested="auto",
            effective="cuda:0",
            fallback_reason=None,
            warnings=(),
        )
        resolver = Mock()
        resolver.resolve.return_value = resolution
        model_loader = Mock(side_effect=["gpu-model", "cpu-model"])
        pipeline_runner = Mock(return_value={"status": "succeeded", "written_fids": 1})
        job_store = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = worker_module.InferenceWorker(
                device_resolver=resolver,
                model_loader=model_loader,
                inference_fn=Mock(),
                pipeline_runner=pipeline_runner,
                job_store=job_store,
                runtime_root=Path(temp_dir),
            )
            worker.initialize()
            job = types.SimpleNamespace(
                id="job-cpu",
                status="running",
                cancel_requested=False,
                request_payload_json=json.dumps(
                    {
                        "old_tif_path": "old.tif",
                        "new_tif_path": "new.tif",
                        "kml_path": "roi.kml",
                        "output_root": "outputs",
                        "requested_device": "cpu",
                    }
                ),
            )
            worker.run_job(job)

        self.assertEqual(model_loader.call_args_list, [call("cuda:0"), call("cpu")])
        self.assertEqual(pipeline_runner.call_args.kwargs["device"], "cpu")
        self.assertEqual(job_store.finish_job.call_args.kwargs["effective_device"], "cpu")
        self.assertIs(worker.model, "gpu-model")
        self.assertIs(worker.resolution, resolution)

    def test_worker_falls_back_to_cpu_after_cuda_out_of_memory(self):
        worker_module = load_worker_module()
        resolution = types.SimpleNamespace(
            requested="auto",
            effective="cuda:0",
            fallback_reason=None,
            warnings=(),
        )
        resolver = Mock()
        resolver.resolve.return_value = resolution
        model_loader = Mock(side_effect=["gpu-model", "cpu-model"])
        pipeline_runner = Mock(
            side_effect=[
                {
                    "status": "failed",
                    "failed_tiles": ["a.png"],
                    "tile_errors": {"a.png": "CUDA out of memory"},
                    "written_fids": 0,
                },
                {"status": "succeeded", "failed_tiles": [], "tile_errors": {}, "written_fids": 1},
            ]
        )
        job_store = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = worker_module.InferenceWorker(
                device_resolver=resolver,
                model_loader=model_loader,
                inference_fn=Mock(),
                pipeline_runner=pipeline_runner,
                job_store=job_store,
                runtime_root=Path(temp_dir),
                allow_cpu_fallback=True,
            )
            worker.initialize()
            job = types.SimpleNamespace(
                id="job-oom",
                status="running",
                cancel_requested=False,
                request_payload_json=json.dumps(
                    {
                        "old_tif_path": "old.tif",
                        "new_tif_path": "new.tif",
                        "kml_path": "roi.kml",
                        "output_root": "outputs",
                    }
                ),
            )
            worker.run_job(job)

        self.assertEqual(model_loader.call_args_list[1].args, ("cpu",))
        self.assertEqual(pipeline_runner.call_count, 2)
        finish_args = job_store.finish_job.call_args.kwargs
        self.assertEqual(finish_args["status"], "succeeded_with_fallback")
        self.assertEqual(finish_args["effective_device"], "cpu")
        self.assertEqual(finish_args["fallback_reason"], "CUDA_OUT_OF_MEMORY")

    def test_worker_records_cancelled_when_pipeline_stops_at_tile_boundary(self):
        worker_module = load_worker_module()
        resolution = types.SimpleNamespace(
            requested="auto",
            effective="cpu",
            fallback_reason="GPU_NOT_VISIBLE",
            warnings=("fallback",),
        )
        resolver = Mock()
        resolver.resolve.return_value = resolution
        job_store = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = worker_module.InferenceWorker(
                device_resolver=resolver,
                model_loader=Mock(return_value=object()),
                inference_fn=Mock(),
                pipeline_runner=Mock(side_effect=RuntimeError("INFERENCE_CANCELLED")),
                job_store=job_store,
                runtime_root=Path(temp_dir),
            )
            job = types.SimpleNamespace(
                id="job-cancel",
                status="running",
                cancel_requested=False,
                request_payload_json=json.dumps(
                    {
                        "old_tif_path": "old.tif",
                        "new_tif_path": "new.tif",
                        "kml_path": "roi.kml",
                        "output_root": "outputs",
                    }
                ),
            )
            worker.run_job(job)

        self.assertEqual(job_store.finish_job.call_args.kwargs["status"], "cancelled")

    def test_worker_records_job_timeout_with_stable_error_code(self):
        worker_module = load_worker_module()
        resolution = types.SimpleNamespace(
            requested="cpu",
            effective="cpu",
            fallback_reason=None,
            warnings=(),
        )
        resolver = Mock()
        resolver.resolve.return_value = resolution
        job_store = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = worker_module.InferenceWorker(
                device_resolver=resolver,
                model_loader=Mock(return_value=object()),
                inference_fn=Mock(),
                pipeline_runner=Mock(side_effect=RuntimeError("INFERENCE_TIMEOUT")),
                job_store=job_store,
                runtime_root=Path(temp_dir),
            )
            job = types.SimpleNamespace(
                id="job-timeout",
                status="running",
                cancel_requested=False,
                request_payload_json=json.dumps(
                    {
                        "old_tif_path": "old.tif",
                        "new_tif_path": "new.tif",
                        "kml_path": "roi.kml",
                        "output_root": "outputs",
                    }
                ),
            )
            worker.run_job(job)

        finish_args = job_store.finish_job.call_args.kwargs
        self.assertEqual(finish_args["status"], "failed")
        self.assertEqual(finish_args["error_code"], "JOB_TIMEOUT")

    def test_worker_checks_deadline_at_tile_boundary(self):
        worker_module = load_worker_module()

        def inference_fn(_model, **kwargs):
            kwargs["should_cancel"]()
            return {"status": "completed", "results": []}

        worker = worker_module.InferenceWorker(
            device_resolver=Mock(),
            model_loader=Mock(),
            inference_fn=inference_fn,
            pipeline_runner=Mock(),
            job_store=Mock(),
            runtime_root=Path(tempfile.gettempdir()),
        )
        worker.model = object()
        worker.active_job = types.SimpleNamespace(cancel_requested=False)
        worker.job_deadline = time.monotonic() - 1

        with self.assertRaisesRegex(RuntimeError, "INFERENCE_TIMEOUT"):
            worker._tile_runner(
                model_id="cc-ln/CUGRS",
                data_path="tiles",
                out_dir="out",
                file_names=["a.png"],
                device="cpu",
            )

    def test_worker_marks_previous_process_jobs_failed_before_claiming_new_work(self):
        worker_module = load_worker_module()
        job_store = Mock()
        should_stop = Mock(side_effect=[False, True])
        worker = worker_module.InferenceWorker(
            device_resolver=Mock(),
            model_loader=Mock(),
            inference_fn=Mock(),
            pipeline_runner=Mock(),
            job_store=job_store,
            runtime_root=Path(tempfile.gettempdir()),
            poll_interval=0,
            worker_id="worker-host:42",
        )
        worker.model = object()
        worker.resolution = types.SimpleNamespace(
            requested="cpu",
            effective="cpu",
            fallback_reason=None,
            warnings=(),
        )
        job_store.recover_abandoned_jobs.return_value = 0
        job_store.claim_next_job.return_value = None

        worker.run_forever(should_stop=should_stop)

        job_store.recover_abandoned_jobs.assert_called_once_with("worker-host", "worker-host:42")
        job_store.claim_next_job.assert_called_once_with("worker-host:42")

    def test_worker_maps_no_features_to_succeeded_job_status(self):
        worker_module = load_worker_module()
        resolution = types.SimpleNamespace(
            requested="cpu",
            effective="cpu",
            fallback_reason=None,
            warnings=(),
        )
        resolver = Mock()
        resolver.resolve.return_value = resolution
        job_store = Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            worker = worker_module.InferenceWorker(
                device_resolver=resolver,
                model_loader=Mock(return_value=object()),
                inference_fn=Mock(),
                pipeline_runner=Mock(return_value={"status": "no_features", "message": "none"}),
                job_store=job_store,
                runtime_root=Path(temp_dir),
            )
            job = types.SimpleNamespace(
                id="job-empty",
                status="running",
                cancel_requested=False,
                request_payload_json=json.dumps(
                    {
                        "old_tif_path": "old.tif",
                        "new_tif_path": "new.tif",
                        "kml_path": "roi.kml",
                        "output_root": "outputs",
                    }
                ),
            )
            worker.run_job(job)

        self.assertEqual(job_store.finish_job.call_args.kwargs["status"], "succeeded")


class TestJobTimeoutEstimation(unittest.TestCase):
    """任务超时按影像规模放宽（移植江西 2026-09-19）：GB 级影像推理远超
    1 小时兜底时限，曾被固定 deadline 误杀。"""

    def _make_worker(self, worker_module, *, timeout=3600, pipeline_runner=None):
        return worker_module.InferenceWorker(
            device_resolver=Mock(),
            model_loader=Mock(),
            inference_fn=Mock(),
            pipeline_runner=pipeline_runner or Mock(return_value={"status": "completed"}),
            job_store=Mock(),
            runtime_root=Path(tempfile.gettempdir()),
            job_timeout_seconds=timeout,
        )

    def test_formula_boundaries(self):
        worker_module = load_worker_module()
        estimate = worker_module.estimate_inference_timeout_seconds
        # 切片数×6 秒不超过基线时维持基线
        self.assertEqual(estimate(3600, 1), 3600)
        self.assertEqual(estimate(3600, 600), 3600)
        # 超过基线后按切片数放宽
        self.assertEqual(estimate(3600, 601), 3606)
        # 上限 4 小时夹逼
        self.assertEqual(estimate(3600, 100000), 14400)
        # 配置基线高于估算时尊重配置（只增不减）
        self.assertEqual(estimate(20000, 1), 20000)
        # 0/负值表示不启用超时
        self.assertEqual(estimate(0, 100000), 0)

    def test_timeout_extended_for_large_image(self):
        worker_module = load_worker_module()
        worker = self._make_worker(worker_module)
        # 30000×30000 → 59×59=3481 片 → 41772s 夹到 14400（4 小时）
        fake_rasterio = MagicMock()
        fake_rasterio.open.return_value.__enter__.return_value = types.SimpleNamespace(
            height=30000, width=30000
        )
        fake_rasterio.open.return_value.__exit__.return_value = False
        with patch.dict(sys.modules, {"rasterio": fake_rasterio}):
            seconds = worker._job_timeout_for({"old_tif_path": "big.tif"})
        self.assertEqual(seconds, 14400)

    def test_timeout_falls_back_to_base_when_raster_unreadable(self):
        worker_module = load_worker_module()
        worker = self._make_worker(worker_module)
        self.assertEqual(worker._job_timeout_for({"old_tif_path": "missing.tif"}), 3600)
        self.assertEqual(worker._job_timeout_for({}), 3600)

    def test_small_image_keeps_configured_base(self):
        worker_module = load_worker_module()
        worker = self._make_worker(worker_module)
        with tempfile.TemporaryDirectory() as temp_dir:
            import rasterio
            from rasterio.transform import from_bounds

            tif_path = str(Path(temp_dir) / "small.tif")
            with rasterio.open(
                tif_path,
                "w",
                driver="GTiff",
                height=512,
                width=512,
                count=3,
                dtype="uint8",
                transform=from_bounds(100.0, 25.0, 100.1, 25.1, 512, 512),
            ) as dst:
                dst.write(np.zeros((3, 512, 512), dtype=np.uint8))
            self.assertEqual(worker._job_timeout_for({"old_tif_path": tif_path}), 3600)

    def test_run_job_uses_estimated_deadline(self):
        worker_module = load_worker_module()
        deadline_margin = {}

        def pipeline_runner(**_kwargs):
            deadline_margin["value"] = worker.job_deadline - time.monotonic()
            return {"status": "completed"}

        worker = self._make_worker(worker_module, pipeline_runner=pipeline_runner)
        job = types.SimpleNamespace(
            id="job-estimate",
            status="running",
            cancel_requested=False,
            request_payload_json=json.dumps(
                {
                    "old_tif_path": "big.tif",
                    "new_tif_path": "big.tif",
                    "kml_path": "roi.kml",
                    "output_root": "outputs",
                }
            ),
        )
        fake_rasterio = MagicMock()
        fake_rasterio.open.return_value.__enter__.return_value = types.SimpleNamespace(
            height=30000, width=30000
        )
        fake_rasterio.open.return_value.__exit__.return_value = False
        with tempfile.TemporaryDirectory() as temp_dir:
            worker.runtime_root = Path(temp_dir)
            with patch.dict(sys.modules, {"rasterio": fake_rasterio}):
                worker.run_job(job)

        # pipeline 执行时距 deadline 应接近 4 小时上限，而非配置的 1 小时
        self.assertGreater(deadline_margin["value"], 3600)
        self.assertLessEqual(deadline_margin["value"], 14400)


if __name__ == "__main__":
    unittest.main()
