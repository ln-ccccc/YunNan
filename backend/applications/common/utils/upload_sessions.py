"""分片续传会话（100GB 级影像上传，2026-09-22）。

会话状态与分块都落盘在 UPLOADED_PHOTOS_DEST/.chunks/<session_id>/：
- session.json：{upload_key, filename, mime, total_size, chunk_size, total_chunks}
- chunk_<idx>.bin

设计要点：
- init 幂等：同 upload_key 且同 filename/total_size 复用既有会话并返回已收
  分块索引 → 页面重开/失败重试自动断点续传（前端跳过 received 分块）。
- session_id 即客户端 upload_key（名字:大小:修改时间 的摘要，hex 校验），
  不引入新表；写入时点全部做路径与数值校验。
- complete 在目标卷内就地合并（临时文件 + os.replace），避免
  "分块 + 组装副本 + 最终副本" 的三倍磁盘峰值。
- 每次 init 顺带清扫超过 STALE_DAYS 的陈旧会话，防暂存目录无界增长。
"""
import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from flask import current_app

from applications.common.utils.tiff_processor import UPLOAD_SESSION_MAX_TOTAL_MB


SESSION_STAGING_DIRNAME = ".chunks"
UPLOAD_SESSION_MAX_CHUNK_BYTES = 512 * 1024 * 1024
UPLOAD_SESSION_MIN_CHUNK_BYTES = 1024 * 1024
UPLOAD_SESSION_STALE_DAYS = 7
SESSION_ID_RE = re.compile(r"^[a-f0-9]{16,64}$")
_MAX_FILENAME_LENGTH = 200


class UploadSessionError(ValueError):
    """面向客户端的分片会话错误（400 语义）。"""


def _staging_root() -> Path:
    dest = current_app.config.get("UPLOADED_PHOTOS_DEST")
    if not dest:
        raise UploadSessionError("上传目录未配置")
    return Path(dest) / SESSION_STAGING_DIRNAME


def _session_dir(session_id: str) -> Path:
    validate_session_id(session_id)
    return _staging_root() / session_id


def validate_session_id(session_id) -> str:
    text = str(session_id or "")
    if not SESSION_ID_RE.match(text):
        raise UploadSessionError("非法的上传会话标识")
    return text


def sanitize_filename(name) -> str:
    text = str(name or "").strip()
    if not text or len(text) > _MAX_FILENAME_LENGTH:
        raise UploadSessionError("文件名不合法")
    if "/" in text or "\\" in text or "\x00" in text:
        raise UploadSessionError("文件名不合法")
    return text


def _expected_chunk_size(state: dict, index: int) -> int:
    if index < 0 or index >= state["total_chunks"]:
        raise UploadSessionError("分块序号越界")
    full = state["chunk_size"]
    remainder = state["total_size"] - full * index
    return full if remainder >= full else remainder


def _load_session(session_id: str) -> dict:
    session_file = _session_dir(session_id) / "session.json"
    if not session_file.is_file():
        raise UploadSessionError("上传会话不存在或已过期")
    try:
        state = json.loads(session_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise UploadSessionError("上传会话状态损坏，请重新发起上传")
    for key in ("upload_key", "filename", "mime", "total_size", "chunk_size", "total_chunks"):
        if key not in state:
            raise UploadSessionError("上传会话状态损坏，请重新发起上传")
    return state


def _save_session(session_dir: Path, state: dict) -> None:
    state = dict(state)
    state["updated_at"] = int(time.time())
    tmp = session_dir / "session.json.tmp"
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp.replace(session_dir / "session.json")


def sweep_stale_sessions(max_age_days: int = UPLOAD_SESSION_STALE_DAYS) -> int:
    root = _staging_root()
    if not root.is_dir():
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def init_session(*, upload_key, filename, total_size, mime, chunk_size) -> dict:
    session_id = validate_session_id(upload_key)
    filename = sanitize_filename(filename)
    try:
        total_size = int(total_size)
    except (TypeError, ValueError):
        raise UploadSessionError("total_size 必须是正整数") from None
    if total_size <= 0:
        raise UploadSessionError("total_size 必须是正整数")
    max_total = UPLOAD_SESSION_MAX_TOTAL_MB * 1024 * 1024
    if total_size > max_total:
        raise UploadSessionError(
            f"文件总大小超过分片上传上限 ({UPLOAD_SESSION_MAX_TOTAL_MB}MB)"
        )
    try:
        chunk_size = int(chunk_size)
    except (TypeError, ValueError):
        raise UploadSessionError("chunk_size 必须是正整数") from None
    if chunk_size < UPLOAD_SESSION_MIN_CHUNK_BYTES or chunk_size > UPLOAD_SESSION_MAX_CHUNK_BYTES:
        raise UploadSessionError(
            f"分块大小须在 {UPLOAD_SESSION_MIN_CHUNK_BYTES // (1024 * 1024)}MB ~ "
            f"{UPLOAD_SESSION_MAX_CHUNK_BYTES // (1024 * 1024)}MB 之间"
        )
    total_chunks = -(-total_size // chunk_size)  # ceil

    session_dir = _staging_root() / session_id
    if session_dir.is_dir():
        existing = _load_session(session_id)
        if (
            int(existing.get("total_size")) != total_size
            or existing.get("filename") != filename
        ):
            raise UploadSessionError("同名上传会话已存在但文件信息不一致（文件已变化？），请刷新页面后重试")
        return session_state(session_id)

    try:
        sweep_stale_sessions()
    except OSError:
        pass
    session_dir.mkdir(parents=True, exist_ok=True)
    _save_session(session_dir, {
        "upload_key": session_id,
        "filename": filename,
        "mime": str(mime or "application/octet-stream"),
        "total_size": total_size,
        "chunk_size": chunk_size,
        "total_chunks": total_chunks,
        "created_at": int(time.time()),
    })
    return session_state(session_id)


def session_state(session_id: str) -> dict:
    state = _load_session(session_id)
    session_dir = _session_dir(session_id)
    received = []
    for index in range(state["total_chunks"]):
        chunk_file = session_dir / f"chunk_{index}.bin"
        if chunk_file.is_file() and chunk_file.stat().st_size == _expected_chunk_size(state, index):
            received.append(index)
    return {
        "session_id": str(session_id),
        "filename": state["filename"],
        "total_size": state["total_size"],
        "chunk_size": state["chunk_size"],
        "total_chunks": state["total_chunks"],
        "received": received,
    }


def write_chunk(session_id: str, index: int, stream, declared_sha256=None) -> int:
    state = _load_session(session_id)
    index = int(index)
    expected = _expected_chunk_size(state, index)
    session_dir = _session_dir(session_id)
    tmp_path = session_dir / f".chunk_{index}.bin.tmp"
    digest = hashlib.sha256()
    written = 0
    try:
        with tmp_path.open("wb") as out:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                written += len(block)
                if written > expected:
                    raise UploadSessionError(
                        f"分块 {index} 超出预期大小（{written} > {expected} 字节），请检查文件是否已变化"
                    )
                digest.update(block)
                out.write(block)
        if written != expected:
            raise UploadSessionError(
                f"分块 {index} 大小不符（收到 {written}，预期 {expected} 字节）"
            )
        if declared_sha256 and digest.hexdigest() != str(declared_sha256).lower():
            raise UploadSessionError(f"分块 {index} 校验和不匹配，请重传该分块")
        tmp_path.replace(session_dir / f"chunk_{index}.bin")
    except UploadSessionError:
        tmp_path.unlink(missing_ok=True)
        raise
    except OSError as error:
        tmp_path.unlink(missing_ok=True)
        raise UploadSessionError(f"分块写入失败: {error}") from error
    _save_session(session_dir, state)
    return written


def assemble_session(session_id: str, dest_path: Path) -> Path:
    state = _load_session(session_id)
    session_dir = _session_dir(session_id)
    for index in range(state["total_chunks"]):
        chunk_file = session_dir / f"chunk_{index}.bin"
        expected = _expected_chunk_size(state, index)
        if not chunk_file.is_file() or chunk_file.stat().st_size != expected:
            raise UploadSessionError(f"分块 {index} 缺失或大小不符，无法完成上传")

    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_name(f".{dest_path.name}.assembling")
    total = 0
    try:
        with tmp_path.open("wb") as out:
            for index in range(state["total_chunks"]):
                with (session_dir / f"chunk_{index}.bin").open("rb") as chunk:
                    shutil.copyfileobj(chunk, out, length=8 * 1024 * 1024)
                    total += chunk.tell()
        if total != state["total_size"]:
            raise UploadSessionError(
                f"合并后大小不符（{total} != {state['total_size']}），请重新上传"
            )
        tmp_path.replace(dest_path)
    except UploadSessionError:
        tmp_path.unlink(missing_ok=True)
        raise
    except OSError as error:
        tmp_path.unlink(missing_ok=True)
        raise UploadSessionError(f"分块合并失败: {error}") from error
    return dest_path


def remove_session(session_id: str) -> None:
    shutil.rmtree(_session_dir(session_id), ignore_errors=True)
