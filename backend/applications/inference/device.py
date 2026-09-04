"""与 NVIDIA 具体型号无关的 CUDA 能力探测和 CPU 降级。"""

from dataclasses import dataclass
from typing import Callable, Optional, Tuple


GPU_ERROR_MESSAGES = {
    "GPU_NOT_VISIBLE": "未检测到容器可见的 NVIDIA CUDA GPU",
    "GPU_RUNTIME_UNAVAILABLE": "PyTorch CUDA 运行时不可用",
    "GPU_ARCH_UNSUPPORTED": "当前 PyTorch/CUDA 构建不支持该 GPU 计算架构",
    "MMCV_CUDA_OP_UNAVAILABLE": "MMCV CUDA 算子不可用",
    "MODEL_GPU_LOAD_FAILED": "模型无法在 GPU 加载",
    "CUDA_OUT_OF_MEMORY": "CUDA 显存不足",
    "GPU_INFERENCE_FAILED": "GPU 能力检查失败",
}


@dataclass(frozen=True)
class DeviceResolution:
    requested: str
    effective: str
    fallback_reason: Optional[str]
    warnings: Tuple[str, ...]
    gpu_name: Optional[str] = None
    compute_capability: Optional[str] = None


class DeviceResolutionError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _default_torch_loader():
    import torch

    return torch


def _run_default_mmcv_cuda_op(device: str) -> None:
    import torch
    from mmcv.ops import roi_align

    features = torch.ones((1, 1, 4, 4), dtype=torch.float32, device=device)
    rois = torch.tensor([[0, 0, 0, 3, 3]], dtype=torch.float32, device=device)
    output = roi_align(features, rois, 2, 1.0, 0, "avg", True)
    if tuple(output.shape) != (1, 1, 2, 2):
        raise RuntimeError(f"MMCV RoIAlign 返回异常形状: {tuple(output.shape)}")
    torch.cuda.synchronize(int(device.split(":", 1)[1]))


def run_mmcv_cuda_smoke_test(device: str, *, op_runner=None) -> None:
    """执行一个真实 MMCV CUDA 扩展算子，排除仅能导入但无法运行的构建。"""
    if not str(device).startswith("cuda:"):
        return
    runner = op_runner or _run_default_mmcv_cuda_op
    try:
        runner(device)
    except Exception as error:
        raise RuntimeError(f"MMCV CUDA op smoke failed: {error}") from error


def _classify_gpu_error(error: Exception) -> str:
    message = str(error).lower()
    if "out of memory" in message:
        return "CUDA_OUT_OF_MEMORY"
    if "no kernel image" in message or "invalid device function" in message or "compute capability" in message:
        return "GPU_ARCH_UNSUPPORTED"
    if "mmcv" in message and ("cuda" in message or "not compiled" in message or "op" in message):
        return "MMCV_CUDA_OP_UNAVAILABLE"
    if "model" in message and ("load" in message or "init" in message):
        return "MODEL_GPU_LOAD_FAILED"
    return "GPU_INFERENCE_FAILED"


class DeviceResolver:
    def __init__(
        self,
        *,
        torch_loader: Callable = _default_torch_loader,
        smoke_test: Optional[Callable[[str], None]] = None,
    ):
        self._torch_loader = torch_loader
        self._smoke_test = smoke_test

    @staticmethod
    def _fallback_or_raise(requested: str, code: str, allow_cpu_fallback: bool) -> DeviceResolution:
        message = GPU_ERROR_MESSAGES[code]
        if not allow_cpu_fallback:
            raise DeviceResolutionError(code, message)
        return DeviceResolution(
            requested=requested,
            effective="cpu",
            fallback_reason=code,
            warnings=(f"{message}，已回退 CPU",),
        )

    def resolve(
        self,
        *,
        requested: str = "auto",
        gpu_device: int = 0,
        allow_cpu_fallback: bool = True,
    ) -> DeviceResolution:
        normalized = (requested or "auto").strip().lower()
        if normalized == "cpu":
            return DeviceResolution("cpu", "cpu", None, ())

        device_index = int(gpu_device)
        if normalized.startswith("cuda:"):
            try:
                device_index = int(normalized.split(":", 1)[1])
            except ValueError as error:
                raise DeviceResolutionError("GPU_RUNTIME_UNAVAILABLE", f"非法 CUDA 设备: {requested}") from error
        elif normalized not in {"auto", "cuda"}:
            raise DeviceResolutionError("GPU_RUNTIME_UNAVAILABLE", f"不支持的推理设备: {requested}")

        try:
            torch_module = self._torch_loader()
        except Exception:
            return self._fallback_or_raise(normalized, "GPU_RUNTIME_UNAVAILABLE", allow_cpu_fallback)

        try:
            torch_version = getattr(torch_module, "version", None)
            if torch_version is not None and getattr(torch_version, "cuda", None) is None:
                return self._fallback_or_raise(normalized, "GPU_RUNTIME_UNAVAILABLE", allow_cpu_fallback)
            if not torch_module.cuda.is_available() or device_index < 0:
                return self._fallback_or_raise(normalized, "GPU_NOT_VISIBLE", allow_cpu_fallback)
            if device_index >= torch_module.cuda.device_count():
                return self._fallback_or_raise(normalized, "GPU_NOT_VISIBLE", allow_cpu_fallback)

            device = f"cuda:{device_index}"
            torch_module.empty(1, device=device)
            gpu_name = torch_module.cuda.get_device_name(device_index)
            capability = torch_module.cuda.get_device_capability(device_index)
            compute_capability = ".".join(str(part) for part in capability)
            if self._smoke_test is not None:
                self._smoke_test(device)
        except DeviceResolutionError:
            raise
        except Exception as error:
            code = _classify_gpu_error(error)
            return self._fallback_or_raise(normalized, code, allow_cpu_fallback)

        return DeviceResolution(
            requested=normalized,
            effective=device,
            fallback_reason=None,
            warnings=(),
            gpu_name=gpu_name,
            compute_capability=compute_capability,
        )
