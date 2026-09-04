import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


def load_device_module():
    module_name = "inference_device_test"
    module_path = Path(__file__).parent / "applications" / "inference" / "device.py"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(module_name, None)


def load_config_module():
    module_path = Path(__file__).parent / "applications" / "configs" / "config.py"
    spec = importlib.util.spec_from_file_location("inference_config_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeCuda:
    def __init__(self, *, available=True, count=1, name="NVIDIA GPU", capability=(8, 9)):
        self.available = available
        self.count = count
        self.name = name
        self.capability = capability

    def is_available(self):
        return self.available

    def device_count(self):
        return self.count

    def get_device_name(self, index):
        return self.name

    def get_device_capability(self, index):
        return self.capability


class FakeTorch:
    def __init__(self, cuda):
        self.cuda = cuda
        self.empty_calls = []

    def empty(self, size, *, device):
        self.empty_calls.append((size, device))
        return object()


class TestDeviceResolver(unittest.TestCase):
    def test_mmcv_cuda_smoke_executes_injected_compiled_op(self):
        module = load_device_module()
        op_runner = Mock()

        module.run_mmcv_cuda_smoke_test("cuda:1", op_runner=op_runner)

        op_runner.assert_called_once_with("cuda:1")

    def test_mmcv_cuda_smoke_reports_stable_failure_text(self):
        module = load_device_module()

        with self.assertRaisesRegex(RuntimeError, "MMCV CUDA op smoke failed"):
            module.run_mmcv_cuda_smoke_test(
                "cuda:0",
                op_runner=Mock(side_effect=RuntimeError("invalid device function")),
            )

    def test_auto_uses_cuda_when_smoke_test_passes(self):
        module = load_device_module()
        torch_module = FakeTorch(FakeCuda(count=2, name="Any NVIDIA GPU", capability=(9, 0)))
        smoke_test = Mock()
        resolver = module.DeviceResolver(torch_loader=lambda: torch_module, smoke_test=smoke_test)

        result = resolver.resolve(requested="auto", gpu_device=1, allow_cpu_fallback=True)

        self.assertEqual(result.effective, "cuda:1")
        self.assertEqual(result.gpu_name, "Any NVIDIA GPU")
        self.assertEqual(result.compute_capability, "9.0")
        self.assertIsNone(result.fallback_reason)
        smoke_test.assert_called_once_with("cuda:1")

    def test_auto_falls_back_to_cpu_when_cuda_is_hidden(self):
        module = load_device_module()
        resolver = module.DeviceResolver(torch_loader=lambda: FakeTorch(FakeCuda(available=False)))

        result = resolver.resolve(requested="auto", allow_cpu_fallback=True)

        self.assertEqual(result.effective, "cpu")
        self.assertEqual(result.fallback_reason, "GPU_NOT_VISIBLE")
        self.assertTrue(result.warnings)

    def test_cpu_only_torch_reports_gpu_runtime_unavailable(self):
        module = load_device_module()
        torch_module = FakeTorch(FakeCuda(available=False))
        torch_module.version = type("Version", (), {"cuda": None})()
        resolver = module.DeviceResolver(torch_loader=lambda: torch_module)

        result = resolver.resolve(requested="auto", allow_cpu_fallback=True)

        self.assertEqual(result.effective, "cpu")
        self.assertEqual(result.fallback_reason, "GPU_RUNTIME_UNAVAILABLE")

    def test_auto_falls_back_when_mmcv_op_fails(self):
        module = load_device_module()
        resolver = module.DeviceResolver(
            torch_loader=lambda: FakeTorch(FakeCuda()),
            smoke_test=Mock(side_effect=RuntimeError("MMCV CUDA ops are not compiled")),
        )

        result = resolver.resolve(requested="auto", allow_cpu_fallback=True)

        self.assertEqual(result.effective, "cpu")
        self.assertEqual(result.fallback_reason, "MMCV_CUDA_OP_UNAVAILABLE")

    def test_auto_falls_back_when_model_gpu_load_fails(self):
        module = load_device_module()
        resolver = module.DeviceResolver(
            torch_loader=lambda: FakeTorch(FakeCuda()),
            smoke_test=Mock(side_effect=RuntimeError("model GPU load failed")),
        )

        result = resolver.resolve(requested="auto", allow_cpu_fallback=True)

        self.assertEqual(result.effective, "cpu")
        self.assertEqual(result.fallback_reason, "MODEL_GPU_LOAD_FAILED")

    def test_force_cpu_does_not_probe_cuda(self):
        module = load_device_module()
        torch_loader = Mock(side_effect=AssertionError("must not load torch"))
        resolver = module.DeviceResolver(torch_loader=torch_loader)

        result = resolver.resolve(requested="cpu", allow_cpu_fallback=True)

        self.assertEqual(result.effective, "cpu")
        self.assertIsNone(result.fallback_reason)
        torch_loader.assert_not_called()

    def test_fallback_disabled_raises_device_error(self):
        module = load_device_module()
        resolver = module.DeviceResolver(torch_loader=lambda: FakeTorch(FakeCuda(available=False)))

        with self.assertRaises(module.DeviceResolutionError) as context:
            resolver.resolve(requested="auto", allow_cpu_fallback=False)

        self.assertEqual(context.exception.code, "GPU_NOT_VISIBLE")

    def test_inference_environment_configuration_is_parsed(self):
        values = {
            "INFERENCE_ACCELERATOR": "cuda",
            "INFERENCE_CPU_FALLBACK": "false",
            "INFERENCE_GPU_DEVICE": "2",
            "INFERENCE_MAX_CONCURRENCY": "3",
            "INFERENCE_JOB_TIMEOUT_SECONDS": "90",
            "INFERENCE_KEEP_FAILED_WORKDIR": "off",
        }
        with patch.dict(os.environ, values, clear=False):
            config = load_config_module().BaseConfig

        self.assertEqual(config.INFERENCE_ACCELERATOR, "cuda")
        self.assertFalse(config.INFERENCE_CPU_FALLBACK)
        self.assertEqual(config.INFERENCE_GPU_DEVICE, 2)
        self.assertEqual(config.INFERENCE_MAX_CONCURRENCY, 3)
        self.assertEqual(config.INFERENCE_JOB_TIMEOUT_SECONDS, 90)
        self.assertFalse(config.INFERENCE_KEEP_FAILED_WORKDIR)


if __name__ == "__main__":
    unittest.main()
