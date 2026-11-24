"""项目派生缓存：按"源是否变过"的签名，记住从大 JSON 派生出来的值（2026-11-24）。

概览的地类面积、台账的推理计时与图斑计数，都是从成果/任务的大 JSON 派生的纯函数值。重算一次
要解析上 GB 文本（概览约 9 秒；台账让 MySQL 四个核满载约 10 秒，期间拖慢所有走数据库的请求），
而源数据几乎不变。于是把它们记在项目自己的派生目录 projects/{project_id}/outputs/stats/ 下，
一条一个签名。签名没变的直接取用，只重算新增或有变化的那些。

约定：
- 一个缓存一个文件，带版本号。读不回来、格式不对、版本不同一律当空——只影响本次耗时，不影响结果。
- 写入先写同目录的临时文件再原子替换；写失败只记告警，调用方手里已有算好的值。
- 签名只决定"要不要重算"，所以宁可多算、不能漏算：参与签名的列必须是源数据所有写入路径都会
  改动的列（见 result_signature / job_signature）。
- 缓存是派生数据：删掉任何一个文件，下一次读取只是慢一次。
"""
import json
import logging
import os
from pathlib import Path

LOGGER = logging.getLogger(__name__)

CACHE_VERSION = 1
CACHE_DIRECTORY = ("outputs", "stats")


def cache_path(project_root, filename):
    return Path(project_root).joinpath(*CACHE_DIRECTORY) / filename


def load_cache(path):
    """{键: 条目}。条目必须是带字符串 signature 的对象，其余字段由调用方解释。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    entries = data.get("entries") if isinstance(data, dict) and data.get("version") == CACHE_VERSION else None
    cache = {}
    for key, entry in (entries or {}).items():
        if isinstance(entry, dict) and isinstance(entry.get("signature"), str):
            cache[str(key)] = entry
    return cache


def save_cache(path, entries):
    """只写入调用方这次仍然关心的条目，缓存不随历史数据增长。"""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps({"version": CACHE_VERSION, "entries": entries}, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except OSError as exc:
        LOGGER.warning("派生缓存写入失败 path=%s: %s", path, exc)


def _moment(value):
    return value.isoformat(sep=" ") if value else ""


def result_signature(result):
    """成果是否变过：修订号 + 图斑数 + 矢量状态 + 行更新时间。

    发布、保存修订、标记失败三条写入路径都至少改动其中一项。绕过这些路径直接改库且四项都不变
    （同一秒、同图斑数、不升修订号）不会被察觉——生产没有这样的写入路径。"""
    return "|".join(str(part) for part in (
        result.current_revision_no,
        result.feature_count,
        result.vector_status,
        _moment(getattr(result, "update_time", None)),
    ))


def job_signature(job):
    """推理任务是否变过：状态 + 完成时间 + 行更新时间。result_json 只在 finish_job 里与这三项
    一同写入。"""
    return "|".join(str(part) for part in (
        job.status,
        _moment(getattr(job, "finished_at", None)),
        _moment(getattr(job, "update_time", None)),
    ))
