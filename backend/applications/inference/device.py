"""与 NVIDIA 具体型号无关的 CUDA 能力探测和 CPU 降级。"""

import errno
import logging
from dataclasses import dataclass
from typing import Callable, Optional, Tuple


logger = logging.getLogger(__name__)

GPU_ERROR_MESSAGES = {
    "GPU_NOT_VISIBLE": "未检测到容器可见的 NVIDIA CUDA GPU",
    "GPU_RUNTIME_UNAVAILABLE": "PyTorch CUDA 运行时不可用",
    "GPU_ARCH_UNSUPPORTED": "当前 PyTorch/CUDA 构建不支持该 GPU 计算架构",
    "MMCV_CUDA_OP_UNAVAILABLE": "MMCV CUDA 算子不可用",
    "MODEL_GPU_LOAD_FAILED": "模型无法在 GPU 加载",
    "HOST_MEMORY_EXHAUSTED": "宿主内存不足，无法分配模型加载所需内存",
    "CUDA_OUT_OF_MEMORY": "CUDA 显存不足",
    "GPU_INFERENCE_FAILED": "GPU 能力检查失败",
}

# CPU 模型常驻加载的宿主内存前置检查（R1 加固续篇）：fp32 checkpoint
# 加载峰值约 2.2× 体积（反序列化副本 + 运行时结构）。8GB 级宿主在 GPU
# 模型常驻（~2.6GiB）后，强制 device=cpu 曾 100% 以深处 Errno 12 失败、
# 浪费整个冷加载时长——低于阈值直接前置拒绝（2026-10-27 实测驱动）
CPU_MODEL_MEMORY_MULTIPLIER = 2.2
INSUFFICIENT_HOST_MEMORY_FOR_CPU = "INSUFFICIENT_HOST_MEMORY_FOR_CPU"


def read_host_memory_available_bytes() -> Optional[int]:
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def _default_checkpoint_size_bytes() -> Optional[int]:
    import os

    try:
        from applications.interface.mmseg_inference_caller import get_model_paths

        _, checkpoint_path = get_model_paths("cc-ln/CUGRS", require_inference_checkpoint=True)
        return os.path.getsize(checkpoint_path)
    except Exception:
        return None


def ensure_host_memory_for_cpu_model(
    checkpoint_bytes: Optional[int] = None,
    *,
    available_bytes: Optional[int] = None,
    checkpoint_size_resolver=None,
) -> Optional[int]:
    """加载 CPU 模型前校验宿主可用内存；不足抛 DeviceResolutionError
    （code=INSUFFICIENT_HOST_MEMORY_FOR_CPU）。读取失败或体积未知时放行
    （保持旧行为），返回读取到的可用字节数。"""
    if available_bytes is None:
        available_bytes = read_host_memory_available_bytes()
    if available_bytes is None:
        return None
    if checkpoint_bytes is None:
        resolver = checkpoint_size_resolver or _default_checkpoint_size_bytes
        checkpoint_bytes = resolver()
    if not checkpoint_bytes:
        return available_bytes
    required = int(checkpoint_bytes * CPU_MODEL_MEMORY_MULTIPLIER)
    if available_bytes < required:
        raise DeviceResolutionError(
            INSUFFICIENT_HOST_MEMORY_FOR_CPU,
            "宿主可用内存 "
            f"{available_bytes / 2**30:.2f}GiB 低于 CPU 模型加载所需约 "
            f"{required / 2**30:.2f}GiB（checkpoint {checkpoint_bytes / 2**30:.2f}GiB × "
            f"{CPU_MODEL_MEMORY_MULTIPLIER}）；GPU 模型常驻时 CPU 兜底尤为紧张，"
            "请释放宿主内存后重试或扩充内存",
        )
    return available_bytes


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


def _host_memory_signature(message: str) -> bool:
    """宿主内存耗尽的报错特征。torch DefaultCPUAllocator 的真实文案是
    "can't allocate memory" / "couldn't allocate memory"（撇号形态随版本），
    与 glibc 的 "cannot allocate memory" 统一去撇号匹配。"""
    normalized = message.replace("'", "")
    return (
        "cannot allocate memory" in normalized
        or "cant allocate memory" in normalized
        or "couldnt allocate memory" in normalized
        or "[errno 12]" in message
    )


def classify_runtime_error(error: Exception) -> Optional[str]:
    """运行期错误分类（供 worker 在任务执行 except 路径复用）。

    返回 "HOST_MEMORY_EXHAUSTED" 表示宿主内存耗尽；None 表示无宿主内存
    特征，由调用方按设备归类。errno=ENOMEM（12）的 OSError 即使报错文案
    未命中串特征也判定为宿主内存耗尽。"""
    if _host_memory_signature(str(error).lower()) or getattr(error, "errno", None) == errno.ENOMEM:
        return "HOST_MEMORY_EXHAUSTED"
    return None


def _classify_gpu_error(error: Exception) -> str:
    message = str(error).lower()
    # 宿主 RAM 不足（torch.load 分配失败）先于显存判断：误归为 GPU 问题会
    # 掩盖"加内存即可恢复 GPU"的真相（2026-11 验收 R1 根因）
    if _host_memory_signature(message) or getattr(error, "errno", None) == errno.ENOMEM:
        return "HOST_MEMORY_EXHAUSTED"
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
            # 原始异常与堆栈必须落日志：真实根因（如宿主内存耗尽）不能被
            # 分类码兜底吞掉，否则运维只能看到"GPU 能力检查失败"
            logger.exception("GPU 设备探测或加载失败，分类码 %s（回退 CPU=%s）", code, allow_cpu_fallback)
            return self._fallback_or_raise(normalized, code, allow_cpu_fallback)

        return DeviceResolution(
            requested=normalized,
            effective=device,
            fallback_reason=None,
            warnings=(),
            gpu_name=gpu_name,
            compute_capability=compute_capability,
        )
