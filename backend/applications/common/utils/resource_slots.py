# -*- coding: utf-8 -*-
"""容器级资源槽位信号量（2026-11-15 问题17 从影像业务模块迁入的公共设施）。

跨进程文件锁实现的多槽上限：满载排队等待、超时拒绝；锁挂在 fd 上
（open file description，线程间亦互斥），进程崩溃由内核释放。三池策略：
- 重计算池（clip/slice/统计栅格化/上传预处理）：768MB/路口径；
- 预览池（preview 满幅降采样，实测 ~483MB/路）；
- 分析池（光谱指数计算，384MB 分块预算、分钟级长跑、900s 超时）。
池规模经 2026-11-15 负责人决策收紧为各 1 槽（单请求有界 + 并发总量可控
先验证；吞吐不足再评估恢复重计算第 2 槽）。

本模块不依赖任何业务模块；超时用 SlotTimeoutError 表达，由业务侧包装层
翻译为各自的 400 语义异常。"""
import contextlib
import os
import tempfile
import time
from pathlib import Path

from applications.common.utils.upload_sessions import _try_lock_fd, _unlock_fd


class SlotTimeoutError(RuntimeError):
    """槽位排队超时。"""


_RECOMPUTE_TOTAL_SLOTS = 1
_RECOMPUTE_QUEUE_TIMEOUT_SECONDS = 300.0
_RECOMPUTE_SLOT_DIR = Path(tempfile.gettempdir()) / "yunnan-recompute-slots"
# 池规模（2026-11-15 负责人决策②）：重计算/预览/分析三池各 1 槽——单请求
# 有界（审校矩阵分块累积）+ 并发总量可控先验证成立；吞吐确实不足再评估恢复
# 重计算第 2 槽，不以双池 1.73GB 实测为恢复依据（该测量未覆盖分析与超大
# 矩阵）。注意：项目行锁只串行同项目，多槽下两个大项目可各占一槽，故槽数
# 是容器级并发的真实上界。

_PREVIEW_TOTAL_SLOTS = 1
_PREVIEW_QUEUE_TIMEOUT_SECONDS = 60
_PREVIEW_SLOT_DIR = Path(tempfile.gettempdir()) / "yunnan-preview-slots"

_ANALYSIS_TOTAL_SLOTS = 1
_ANALYSIS_QUEUE_TIMEOUT_SECONDS = 900
_ANALYSIS_SLOT_DIR = Path(tempfile.gettempdir()) / "yunnan-analysis-slots"


@contextlib.contextmanager
def _slot_semaphore(slot_dir, total_slots, timeout):
    """文件锁槽位信号量（F6 架构共用）：跨进程文件锁实现的多槽上限。

    满载时排队等待而非立即拒绝；等待超时抛 SlotTimeoutError（业务包装层
    翻译为各自的 400 语义错误）。锁挂在 fd 上（open file description，
    线程间亦互斥），进程崩溃由内核释放，不留死锁；槽位文件无状态内容。"""
    slot_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    held = None
    while held is None:
        for index in range(total_slots):
            fd = os.open(
                str(slot_dir / f"slot-{index}.lock"),
                os.O_CREAT | os.O_RDWR,
            )
            try:
                _try_lock_fd(fd)
            except OSError:
                os.close(fd)
                continue
            held = fd
            break
        if held is not None:
            break
        if time.monotonic() >= deadline:
            raise SlotTimeoutError(f"并发已满（已等待 {int(timeout)} 秒），请稍后重试")
        time.sleep(0.2)
    try:
        yield
    finally:
        try:
            _unlock_fd(held)
        finally:
            os.close(held)


@contextlib.contextmanager
def recompute_budget_slot(timeout=_RECOMPUTE_QUEUE_TIMEOUT_SECONDS):
    """重计算池：clip/slice/审校矩阵栅格化/上传预处理共用。"""
    with _slot_semaphore(_RECOMPUTE_SLOT_DIR, _RECOMPUTE_TOTAL_SLOTS, timeout):
        yield


@contextlib.contextmanager
def preview_budget_slot(timeout=_PREVIEW_QUEUE_TIMEOUT_SECONDS):
    """预览池：满幅降采样预览（实测 ~483MB/路），满载排队 60s 后拒绝。"""
    with _slot_semaphore(_PREVIEW_SLOT_DIR, _PREVIEW_TOTAL_SLOTS, timeout):
        yield


@contextlib.contextmanager
def analysis_budget_slot(timeout=_ANALYSIS_QUEUE_TIMEOUT_SECONDS):
    """分析池：光谱指数计算（分钟级长跑），超时 900s 与任务时长匹配。"""
    with _slot_semaphore(_ANALYSIS_SLOT_DIR, _ANALYSIS_TOTAL_SLOTS, timeout):
        yield
