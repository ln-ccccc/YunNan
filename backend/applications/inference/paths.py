"""推理输入和任务工作目录的路径边界。"""

from pathlib import Path


def _is_within(path_obj: Path, root_obj: Path) -> bool:
    try:
        path_obj.relative_to(root_obj)
        return True
    except ValueError:
        return False


def resolve_input_path(value, allowed_roots):
    path_obj = Path(value).expanduser().resolve()
    roots = [Path(root).expanduser().resolve() for root in allowed_roots]
    if not path_obj.exists():
        raise FileNotFoundError(f"输入文件不存在: {path_obj}")
    if not any(_is_within(path_obj, root) for root in roots):
        raise ValueError(f"输入路径不在允许目录中: {path_obj}")
    return path_obj


def create_job_workdir(runtime_root, job_id):
    root = Path(runtime_root).expanduser().resolve()
    path_obj = (root / str(job_id)).resolve()
    if not _is_within(path_obj, root) or path_obj == root:
        raise ValueError("任务编号非法")
    path_obj.mkdir(parents=True, exist_ok=False)
    return path_obj


def initialize_job_workdir(work_dir):
    path_obj = Path(work_dir).expanduser().resolve()
    if path_obj.exists() and any(path_obj.iterdir()):
        raise FileExistsError(f"任务工作目录已存在且非空: {path_obj}")
    path_obj.mkdir(parents=True, exist_ok=True)
    return path_obj


def resolve_output_file(output_root, fid, filename):
    fid_text = str(fid or "").strip()
    filename_text = str(filename or "").strip()
    if not fid_text or fid_text in {".", ".."} or "/" in fid_text or "\\" in fid_text:
        raise ValueError("FID 非法")
    if not filename_text or filename_text in {".", ".."} or "/" in filename_text or "\\" in filename_text:
        raise ValueError("结果文件名非法")
    root = Path(output_root).expanduser().resolve()
    target = (root / fid_text / filename_text).resolve()
    if not _is_within(target, root):
        raise ValueError("结果路径越界")
    return target
