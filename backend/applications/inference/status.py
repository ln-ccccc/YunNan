"""推理结果状态规则。"""


JOB_TRANSITIONS = {
    "queued": frozenset({"running", "cancelled"}),
    "running": frozenset({"succeeded", "succeeded_with_fallback", "partial_failed", "failed", "cancelled"}),
}
TERMINAL_STATUSES = frozenset({"succeeded", "succeeded_with_fallback", "partial_failed", "failed", "cancelled"})


def derive_outcome_status(failed_tiles, written_fids):
    if failed_tiles:
        return "partial_failed" if written_fids else "failed"
    return "succeeded"


def validate_job_transition(current_status, target_status):
    if target_status not in JOB_TRANSITIONS.get(current_status, frozenset()):
        raise ValueError(f"非法任务状态转换: {current_status} -> {target_status}")


def is_terminal_status(status):
    return status in TERMINAL_STATUSES
