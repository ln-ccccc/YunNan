#!/usr/bin/env python3
"""发布目录工具：写清单、核对发布目录、把版本钉进环境文件、核对运行中的栈。

发布目录由 deploy/make_release.sh 从一个固定的 Git 提交导出。三样东西要分清：

  代码        容器挂载的源码，全部来自那一个提交。逐文件记进 RELEASE.json 的 trees。
  运行时资产  不入库的推理权重和随附的第三方源码（见 ASSETS），共约 9.7GB。它们不属于任何提交，
              由 YUNNAN_ASSET_ROOT 指向的目录提供（不设置即开发树，与改造前相同），compose 单独挂进容器。
              发布目录里对应的路径只是空挂载点；清单里记下每个资产的大小、文件数和 SHA256。
  挂载点      compose 在只读的 backend 之上还挂了命名卷（见 MOUNTPOINTS）。挂载点目录必须预先存在，
              否则容器起不来——只读绑定里建不了目录（2026-11-25 演练第一次启动就撞在这里）。

    python deploy/release_tool.py trees              列出发布目录入账的路径
    python deploy/release_tool.py untracked-check --repo <仓库>
                                                     开发树的发布路径下，未提交的条目必须都能归类
    python deploy/release_tool.py placeholders <发布目录>       建空挂载点
    python deploy/release_tool.py manifest     <发布目录> --commit <完整提交> --repo <仓库> [--asset-root 目录]
    python deploy/release_tool.py check        <发布目录> [--asset-root 目录]        发布目录自身
    python deploy/release_tool.py pin          <环境文件> 键=值 [键=值 ...]
    python deploy/release_tool.py pinned       <环境文件> 键 [键 ...]
    python deploy/release_tool.py render-check <发布目录> [--config-override 文件] [--env-file 文件]
    python deploy/release_tool.py verify       <发布目录> [--project yunnan] [--skip 服务,...] [--env-file 文件]
    python deploy/release_tool.py rehearsal-config <发布目录> <输出文件>            演练用的 config.yaml

所有子命令都只读，只有 placeholders 建空目录和空文件、manifest 写 RELEASE.json、pin 改环境文件里点名的两个键。
容器名约定与 docker-compose.prod.yml 一致：<项目名>-<服务名>。
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tree_hash  # noqa: E402

MANIFEST_NAME = "RELEASE.json"
# 发布目录里记账的代码路径：恰好是容器挂载的源码（frontend/node_modules 除外，它由锁文件决定，另行核对）
TREES = (
    "backend",
    "docker",
    "config.yaml",
    "frontend/src",
    "frontend/package.json",
    "frontend/package-lock.json",
    "miner/server.js",
    "miner/routes",
    "miner/services",
    "miner/src",
    "miner/vite.config.js",
)
# 运行时资产：(发布目录内的相对路径, 文件还是目录, 是什么)。compose 把它们从 YUNNAN_ASSET_ROOT 只读挂到 /app/ 下同名位置
ASSETS = (
    ("backend/model/mmseg_config/model.inference.pth", "file", "推理权重"),
    ("backend/model/mmseg_config/model.pth", "file", "训练权重，没有推理权重时的回退"),
    ("backend/model/mmseg_config/dinov3_swinV1", "dir", "预训练骨干权重，以及启动时加进 PYTHONPATH 的 mmseg、dinov3 源码"),
    ("backend/model/change_detection", "dir", "旧同步推理接口的模型"),
    ("backend/model/classification", "dir", "旧同步推理接口的模型"),
    ("backend/model/object_detection", "dir", "旧同步推理接口的模型"),
    ("backend/model/semantic_segmentation", "dir", "旧同步推理接口的模型"),
)
ASSET_PATHS = tuple(path for path, _, _ in ASSETS)
ASSET_KIND = {path: kind for path, kind, _ in ASSETS}
# 命名卷的挂载点
MOUNTPOINTS = ("backend/static", "backend/runtime/inference_jobs")
# backend 的指纹不计这些路径：发布目录里是空挂载点，容器里被卷或真资产盖住
EXCLUDED = MOUNTPOINTS + ASSET_PATHS
# 开发树的发布路径下，未提交但不属于运行时资产的东西。清单之外的一律拦下：靠"整个开发树一起挂上去"
# 碰巧带进容器的隐含依赖，就是从每一个没被归类的条目开始的
JUNK = (
    (r"(.*/)?__pycache__", "Python 字节码缓存"),
    (r".*\.pyc", "Python 字节码缓存"),
    (r".*Zone\.Identifier", "从网上下载时带的标记文件"),
    (r"backend/\.venv[^/]*", "本地虚拟环境"),
    (r"backend/\.hypothesis", "测试工具的缓存"),
    (r"backend/\.tmp_[^/]*", "临时文件"),
    (r"backend/\.coverage", "测试覆盖率数据"),
    (r"backend/\.flaskenv", "开发环境文件（里面有一套库口令，不能进发布目录；生产的口令与密钥由 compose 给）"),
    (r"backend/model/mmseg_config/model\.inference\.pth\.sha256", "权重旁的校验记录（运行时不读；发布工具会把权重的 SHA256 记进清单）"),
    (r"backend/model/\.gitkeep", "空占位文件"),
    (r"backend/test\.tif", "开发用的样例影像"),
    (r"backend/aircraft_4\.jpg", "开发用的样例影像"),
)
COMMON = (("/app/config.yaml", "config.yaml", True), ("/app/docker", "docker", True))
ASSET_MOUNTS = tuple(("/app/" + path, path, True) for path in ASSET_PATHS)
BACKEND_CODE = COMMON + (("/app/backend", "backend", True),) + ASSET_MOUNTS
# 每个应用容器里应当来自发布目录或资产目录的挂载：(容器路径, 相对路径, 是否只读)
CODE_MOUNTS = {
    "backend": BACKEND_CODE,
    "spatial-worker": BACKEND_CODE,
    "inference-worker": BACKEND_CODE,
    "frontend": COMMON + (
        ("/app/frontend/src", "frontend/src", True),
        ("/app/frontend/package.json", "frontend/package.json", True),
        ("/app/frontend/node_modules", "frontend/node_modules", False),
    ),
    "miner-api": COMMON + (
        ("/app/miner/server.js", "miner/server.js", True),
        ("/app/miner/routes", "miner/routes", True),
        ("/app/miner/services", "miner/services", True),
    ),
    "miner-web": COMMON + (
        ("/app/miner/src", "miner/src", True),
        ("/app/miner/vite.config.js", "miner/vite.config.js", True),
    ),
}
# 各服务用的是哪个镜像（核对"发布时记的镜像"与"实际运行的镜像"是否同一个）
SERVICE_IMAGE = {
    "backend": "app", "frontend": "app", "miner-api": "app", "miner-web": "app", "spatial-worker": "app",
    "inference-worker": "inference", "mysql": "mysql",
}
IMAGES = (
    ("app", "APP_IMAGE", "yunnan-runtime:current"),
    ("inference", "INFERENCE_IMAGE", "yunnan-inference-worker:current"),
    ("mysql", "MYSQL_IMAGE", "registry.openanolis.cn/openanolis/mysql:8.0.30-8.6"),
)
# 不属于任何提交的数据文件：不进发布目录，只记指纹备查
DATA_FILES = (
    "miner/yunnan.kml",
    "miner/NDVI_2year.xlsx",
    "miner/NDBI_by_fid_2year_avg.xlsx",
    "miner/NDWI_by_fid_2year_avg.xlsx",
    "miner/NDSI_by_fid_2year_avg.xlsx",
)
PIN_KEYS = ("YUNNAN_SOURCE_ROOT", "YUNNAN_GIT_SHA")
# 演练 config.yaml 相对发布目录那一份只改这 4 个端口值（浏览器直连后端、GeoView 与 Miner 互相跳转的地址
# 是构建时从 config.yaml 写进产物的，演练栈不换地址就会连到生产端口）
REHEARSAL_PORTS = (
    ("port", "backend", "backend-port"),
    ("port", "frontend", "frontend-port"),
    ("miner", "frontend_port", "miner-web-port"),
    ("miner", "backend_port", "miner-api-port"),
)
# GeoView 新构建的标志串：编辑期校验走这个接口，旧构建里没有
GEOVIEW_MARKER = "geometry/validate"
HTTP_TIMEOUT = 10


# ---------- 通用 ----------

DOCKER = shlex.split(os.environ.get("RELEASE_TOOL_DOCKER", "docker"))   # 测试里换成假的 docker
EXECUTE_TIMEOUT = 300          # 一次 docker 调用的上限（秒）
ASSET_HASH_TIMEOUT = 3600      # 容器内重算运行时资产的 SHA256 要读 10GB，单独放宽


def docker(*arguments, stdin=None, timeout=EXECUTE_TIMEOUT):
    """调 docker。默认有上限：切换脚本靠它查状态、取指纹，不能被一个挂住的调用拖死。"""
    return run(DOCKER + list(arguments), stdin=stdin, timeout=timeout)


def run(arguments, stdin=None, timeout=EXECUTE_TIMEOUT):
    try:
        return subprocess.run(arguments, input=stdin, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        finished = subprocess.CompletedProcess(arguments, 124, expired.stdout or b"", expired.stderr or b"")
        return finished


def text(process):
    return process.stdout.decode("utf-8", "replace").strip()


def parse_time(value):
    """'2026-11-24T09:41:07.556368353Z' 与 '2026-11-25T03:00:00+00:00' 都按 UTC 取到秒。"""
    return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def host_path(source):
    """宿主路径归一化：Docker Desktop 把宿主路径报成 /run/desktop/mnt/host/d/rest，统一成 d:/rest。"""
    value = source.replace("\\", "/")
    prefix = "/run/desktop/mnt/host/"
    if value.startswith(prefix):
        drive, _, rest = value[len(prefix):].partition("/")
        value = drive + ":/" + rest
    windows = bool(re.match(r"^[A-Za-z]:/", value))
    joined = "/".join(part for part in value.split("/") if part not in ("", "."))
    return joined.lower() if windows else joined


def release_path(release):
    return host_path(str(Path(release).resolve()))


def load_manifest(release):
    with open(Path(release) / MANIFEST_NAME, encoding="utf-8") as handle:
        return json.load(handle)


def file_record(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return {"bytes": os.path.getsize(path), "sha256": digest.hexdigest()}


def rewrite_config_ports(source, ports=None):
    """读出 config.yaml 里这 4 个端口的现值；给了 ports 就原位改成新值。结构对不上就报错，不猜。

    返回 (内容, {角色: 原值})。同一个函数既用来生成演练 config，也反过来核对它只差这 4 行。
    """
    lines = source.decode("utf-8").splitlines(keepends=True)
    section, found = None, {}
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        if body and not body[0].isspace() and body.endswith(":"):
            section = body[:-1]
            continue
        match = re.match(r"^  ([A-Za-z_]+): (\S+)$", body)
        for wanted, key, role in REHEARSAL_PORTS:
            if match and section == wanted and match.group(1) == key:
                found[role] = match.group(2)
                if ports and role in ports:
                    lines[index] = "  %s: %s%s" % (key, ports[role], ending)
    if set(found) != {role for _, _, role in REHEARSAL_PORTS}:
        raise ValueError("config.yaml 的结构与预期不同，4 个端口没有找全：%s" % sorted(found))
    return "".join(lines).encode("utf-8"), found


def rehearsal_config_problem(release, override):
    """演练用的 config.yaml 必须恰好是发布目录那一份只改了 4 个端口的结果。"""
    try:
        source = (Path(release) / "config.yaml").read_bytes()
        actual = Path(override).read_bytes()
        _, ports = rewrite_config_ports(actual)
        expected, _ = rewrite_config_ports(source, ports)
    except (OSError, ValueError) as error:
        return str(error)
    return None if expected == actual else "它与发布目录的 config.yaml 的差异不止 4 个端口"


def env_values(path, keys):
    values = {}
    with open(path, "rb") as handle:
        for line in handle:
            match = re.match(rb"[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)", line.rstrip(b"\r\n"))
            if not match:
                continue
            key = match.group(1).decode("ascii")
            if key not in keys:
                continue
            value = match.group(2).decode("utf-8").strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            values.setdefault(key, value)
    return {key: values.get(key) for key in keys}


def asset_root_of(args):
    """资产目录：--asset-root 优先，其次是环境文件里的 YUNNAN_ASSET_ROOT，都没有就是环境文件所在的目录（compose 的默认值 "."）。"""
    if getattr(args, "asset_root", None):
        return host_path(args.asset_root)
    env_file = getattr(args, "env_file", None)
    if env_file:
        value = env_values(env_file, ["YUNNAN_ASSET_ROOT"]).get("YUNNAN_ASSET_ROOT")
        if value:
            if not os.path.isabs(value):
                value = os.path.join(os.path.dirname(os.path.abspath(env_file)), value)
            return host_path(value)
        return host_path(str(Path(env_file).resolve().parent))
    return host_path(os.getcwd())


def under(root, relative):
    """根路径下面的一个相对路径。根是 Windows 盘符路径时相对部分跟着不区分大小写，
    与 host_path 对完整路径的规则一致——否则 dinov3_swinV1 这类带大写字母的路径会被误判成位置不对。"""
    part = host_path(relative)
    return root + "/" + (part.lower() if re.match(r"^[a-z]:/", root) else part)


def expected_sources(release, args):
    """每个源码挂载与资产挂载在宿主机上应当被挂载的位置。"""
    root = asset_root_of(args)
    sources = {}
    for mounts in CODE_MOUNTS.values():
        for _, relative, _ in mounts:
            if relative in ASSET_PATHS:
                sources[relative] = under(root, relative)
            elif relative == "config.yaml" and getattr(args, "config_override", None):
                sources[relative] = host_path(str(Path(args.config_override).resolve()))
            else:
                sources[relative] = under(release, relative)
    return sources


def asset_stat(asset_root, relative):
    """资产的大小与文件数。文件型资产以它所在的目录为根来量，目录型资产以资产目录为根。"""
    if ASSET_KIND[relative] == "file":
        path = Path(asset_root) / relative
        return tree_hash.stat_path(str(path.parent), path.name)
    return tree_hash.stat_path(str(Path(asset_root)), relative)


def tree_problem(release, relative, record):
    """发布目录里的一个代码路径与清单比指纹。"""
    try:
        current = tree_hash.tree_fingerprint(str(release), relative, frozenset(EXCLUDED))
    except (OSError, ValueError) as error:
        return "%s: %s" % (relative, error)
    return None if current == record else "%s 与清单不符: 清单 %s，现状 %s" % (relative, record, current)


def asset_problems(release, asset_root):
    """资产目录里的每个运行时资产都要在，类型、文件数、字节数与清单一致（不重算哈希，9.7GB 太慢）。"""
    problems = []
    for relative, record in load_manifest(release).get("assets", {}).items():
        path = Path(asset_root) / relative
        if not path.exists():
            problems.append("资产目录里没有 %s（看的是 %s）" % (relative, asset_root))
            continue
        current = asset_stat(asset_root, relative)
        if (current["kind"], current["files"], current["bytes"]) != (record["kind"], record["files"], record["bytes"]):
            problems.append("资产 %s 与清单不符: 清单 %s 个文件 %s 字节，现状 %s 个文件 %s 字节" % (
                relative, record["files"], record["bytes"], current["files"], current["bytes"]))
    return problems


def placeholder_problems(release):
    """空挂载点：目录都在、都是空的；两个权重文件都在、都是 0 字节。"""
    problems = []
    for relative in MOUNTPOINTS + ASSET_PATHS:
        path = Path(release) / relative
        if ASSET_KIND.get(relative, "dir") == "file":
            if not path.is_file() or path.stat().st_size != 0:
                problems.append("挂载点 %s 应当是 0 字节的空文件" % relative)
        elif not path.is_dir() or any(path.iterdir()):
            problems.append("挂载点 %s 应当是空目录" % relative)
    return problems


def is_link(path):
    import stat
    if os.path.islink(path):
        return True
    return bool(getattr(os.lstat(path), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def node_modules_problems(frontend):
    """GeoView 依赖必须恰好是 npm ci 按锁文件装出来的那一份。"""
    problems = []
    modules = Path(frontend) / "node_modules"
    if not modules.is_dir():
        return ["frontend/node_modules 不存在"]
    if is_link(modules):
        problems.append("frontend/node_modules 是链接（联接 / 符号链接）：发布目录必须自带依赖，不能指向别的目录")
    with open(Path(frontend) / "package-lock.json", encoding="utf-8") as handle:
        expected = {key: info for key, info in json.load(handle).get("packages", {}).items() if key}
    hidden = modules / ".package-lock.json"
    if not hidden.is_file():
        return problems + ["frontend/node_modules/.package-lock.json 不存在：依赖不是 npm ci 装的，或装完后被动过"]
    with open(hidden, encoding="utf-8") as handle:
        actual = {key: info for key, info in json.load(handle).get("packages", {}).items() if key}   # 去掉根条目
    for key, info in sorted(expected.items()):
        got = actual.get(key)
        if got is None:
            if info.get("optional"):
                continue   # 可选包按平台决定装不装（例如只在 macOS 上装的 fsevents）
            problems.append("依赖目录缺包: %s" % key)
        elif got.get("version") != info.get("version"):
            problems.append("%s 版本不符: 锁文件 %s，目录 %s" % (key, info.get("version"), got.get("version")))
    for key in sorted(set(actual) - set(expected)):
        problems.append("依赖目录多出锁文件里没有的包: %s" % key)
    return problems


def release_problems(release, asset_root):
    """发布目录自身：代码指纹、依赖、空挂载点、资产目录。"""
    release = Path(release)
    try:
        manifest = load_manifest(release)
    except (OSError, ValueError) as error:
        return ["读不到 %s: %s" % (MANIFEST_NAME, error)]
    problems = []
    for relative, record in manifest.get("trees", {}).items():
        problems.extend([problem for problem in [tree_problem(release, relative, record)] if problem])
    problems.extend(node_modules_problems(release / "frontend"))
    problems.extend(placeholder_problems(release))
    problems.extend(asset_problems(release, asset_root))
    return problems


# ---------- 子命令 ----------

def cmd_trees(args):
    for relative in TREES:
        print(relative)
    return 0


def untracked_entries(directory):
    """一个 git 工作树的发布路径下被忽略的条目；不是 git 工作树就返回 None。"""
    if run(["git", "-C", directory, "rev-parse", "--is-inside-work-tree"]).returncode != 0:
        return None
    process = run(["git", "-C", directory, "-c", "core.quotepath=false", "status", "--porcelain", "--ignored", "-z", "--"] + list(TREES))
    if process.returncode != 0:
        sys.exit("git status 失败: %s" % process.stderr.decode("utf-8", "replace").strip())
    return [entry[3:].rstrip("/") for entry in process.stdout.decode("utf-8", "replace").split("\0") if entry.startswith("!! ")]


def asset_present(root, relative):
    """资产真地在：目录里至少有一个文件，权重文件不是 0 字节。空目录等于没有。"""
    path = Path(root) / relative
    if not path.exists():
        return False
    if ASSET_KIND[relative] == "dir":
        return any(item.is_file() for item in path.rglob("*"))
    return path.is_file() and path.stat().st_size > 0


def classify_untracked(entries):
    """被忽略的条目分成三类：运行时资产、确认没用的、归不了类的。"""
    assets, junk, unknown = [], {}, []
    for path in entries:
        if path in ASSET_PATHS:
            assets.append(path)
        elif path in MOUNTPOINTS:
            continue   # 空挂载点由发布目录自建
        else:
            for pattern, label in JUNK:
                if re.match(pattern + "$", path):
                    junk.setdefault(label, []).append(path)
                    break
            else:
                unknown.append(path)
    return assets, junk, unknown


def cmd_untracked_check(args):
    """发布路径下未提交的条目逐个归类；归不了类的不让生成发布目录。

    看两个角色：仓库（提交所在的地方，代码从这里导出）和资产目录（权重所在的地方）。两者常常是同一个目录。
    两边都可能有靠"整个开发树一起挂上去"碰巧带进容器的隐含依赖，所以都要求未提交的条目能归类；
    运行时资产只要求资产目录里齐全。
    """
    failures, scanned, described = 0, {}, set()
    for role, directory in (("仓库", args.repo), ("资产目录", args.asset_root)):
        key = host_path(directory)
        if key not in scanned:
            print("[%s] %s" % (role, directory))
            scanned[key] = untracked_entries(directory)
        entries = scanned[key]
        assets, junk, unknown = classify_untracked(entries) if entries is not None else ([], {}, [])
        missing = [relative for relative in ASSET_PATHS
                   if (entries is not None and relative not in assets) or not asset_present(Path(directory), relative)]
        if key not in described:
            described.add(key)
            if entries is None:
                print("  不是 git 工作树，只看资产文件在不在")
            else:
                for relative, _, note in ASSETS:
                    present = relative in assets if entries is not None else asset_present(Path(directory), relative)
                    print("  %s 运行时资产 %s（%s）" % ("有  " if present else "缺少", relative, note))
                for label, paths in sorted(junk.items()):
                    print("  不进发布目录：%s %d 项%s" % (
                        label, len(paths), "（" + "、".join(paths[:3]) + (" 等" if len(paths) > 3 else "") + "）"))
                if unknown:
                    print("  发布路径下有未提交、又没归类的条目，不能生成发布目录：" + "".join("\n    " + item for item in unknown))
                    print("  运行时要用的：加进 deploy/release_tool.py 的 ASSETS；确认没用的：加进 JUNK 并写明是什么。")
        if unknown:
            failures += 1
        if role == "资产目录" and missing:
            failures += 1
            if len(scanned) < 2:
                print("  资产目录里缺少 %d 个运行时资产" % len(missing))
    return 1 if failures else 0


def cmd_placeholders(args):
    release = Path(args.release)
    for relative in MOUNTPOINTS:
        (release / relative).mkdir(parents=True, exist_ok=True)
    for relative in ASSET_PATHS:
        target = release / relative
        if ASSET_KIND[relative] == "dir":
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
    print("已建 %d 个空挂载点" % (len(MOUNTPOINTS) + len(ASSETS)))


def asset_signature(kind, status):
    """判断资产变没变的依据：文件看大小和修改时间，目录看大小和文件数。"""
    if kind == "file":
        return {"bytes": status["bytes"], "mtime": status["mtime"]}
    return {"bytes": status["bytes"], "files": status["files"]}


def asset_records(asset_root, cache):
    """每个资产的：类型、文件数、字节数、SHA256。算过的记在缓存里，依据没变就不重算（两个权重共 7.5GB）。"""
    records = {}
    for relative, kind, note in ASSETS:
        path = Path(asset_root) / relative
        if not path.exists():
            sys.exit("资产目录里没有 %s（看的是 %s）" % (relative, asset_root))
        status = asset_stat(asset_root, relative)
        signature = asset_signature(kind, status)
        key = host_path(str(path))
        entry = cache.get(key)
        if not entry or any(entry.get(name) != value for name, value in signature.items()):
            digest = file_record(path)["sha256"] if kind == "file" else tree_hash.tree_fingerprint(str(Path(asset_root)), relative)["sha256"]
            entry = dict(signature, sha256=digest)
            cache[key] = entry
        records[relative] = {"kind": kind, "files": status["files"], "bytes": status["bytes"], "sha256": entry["sha256"], "note": note}
    return records


def cmd_manifest(args):
    release = Path(args.release).resolve()
    trees = {relative: tree_hash.tree_fingerprint(str(release), relative, frozenset(EXCLUDED)) for relative in TREES}
    problems = node_modules_problems(release / "frontend") + placeholder_problems(release)
    if problems:
        sys.exit("发布目录不合格，不生成清单：\n  " + "\n  ".join(problems))

    def git(*arguments):
        process = run(["git", "-C", args.repo] + list(arguments))
        if process.returncode != 0:
            sys.exit("git %s 失败: %s" % (" ".join(arguments), text(process)))
        return text(process)

    commit = git("rev-parse", "--verify", args.commit + "^{commit}")
    committed_at, subject = git("log", "-1", "--format=%cI%n%s", commit).split("\n")[:2]
    images = {}
    for key, variable, default in IMAGES:
        reference = os.environ.get(variable, default)
        process = docker("image", "inspect", reference, "--format", "{{.Id}}")
        images[key] = {"reference": reference, "id": text(process) if process.returncode == 0 else None}
    data_files = {}
    for relative in DATA_FILES:
        path = Path(args.data_dir) / relative
        data_files[relative] = file_record(path) if path.is_file() else None
    cache_path = Path(args.asset_cache) if args.asset_cache else None
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path and cache_path.is_file() else {}
    assets = asset_records(args.asset_root, cache)
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    dependency_files = [item for item in (release / "frontend" / "node_modules").rglob("*") if item.is_file()]
    with open(release / "frontend" / "package-lock.json", encoding="utf-8") as handle:
        lockfile = json.load(handle)

    manifest = {
        "format": 2,
        "name": args.name or release.name,
        "commit": commit,
        "commit_short": commit[:7],
        "committed_at": committed_at,
        "commit_subject": subject,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trees": trees,
        "assets": assets,
        "mountpoints": list(MOUNTPOINTS),
        "node_modules": {
            "files": len(dependency_files),
            "bytes": sum(item.stat().st_size for item in dependency_files),
            "lockfile_version": lockfile.get("lockfileVersion"),
        },
        "toolchain": {"node": os.environ.get("RELEASE_NODE_VERSION"), "npm": os.environ.get("RELEASE_NPM_VERSION")},
        "images": images,
        "data_files": data_files,
        "tool_commit": git("rev-parse", "HEAD"),
        "repo_dirty_tracked_files": len([line for line in git("status", "--porcelain", "--untracked-files=no").split("\n") if line.strip()]),
    }
    with open(release / MANIFEST_NAME, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    print("已写入 %s：%s %s，%d 个代码路径、%d 个运行时资产入账" % (
        release / MANIFEST_NAME, manifest["commit_short"], subject, len(trees), len(assets)))
    missing = [key for key, record in images.items() if not record["id"]]
    if missing:
        print("警告：本机找不到这些镜像，清单里记为空：%s" % "、".join(missing), file=sys.stderr)
    return 0


def cmd_check(args):
    problems = release_problems(args.release, asset_root_of(args))
    for problem in problems:
        print("不符: " + problem)
    if problems:
        return 1
    manifest = load_manifest(args.release)
    print("发布目录完好：%s %s《%s》，%d 个代码路径指纹与清单一致，依赖与锁文件一致，%d 个空挂载点，%d 个运行时资产与清单一致" % (
        manifest["name"], manifest["commit_short"], manifest["commit_subject"], len(manifest.get("trees", {})),
        len(MOUNTPOINTS) + len(ASSETS), len(manifest.get("assets", {}))))
    return 0


def cmd_pinned(args):
    print(json.dumps(env_values(args.env_file, args.keys), ensure_ascii=False))
    return 0


def cmd_pin(args):
    """只动点名的键：已有的行原位替换（保留它自己的行尾），没有的追加到末尾；其余字节一律不动。"""
    pairs = {}
    for item in args.pairs:
        key, _, value = item.partition("=")
        if key not in PIN_KEYS or not value:
            sys.exit("只能改这两个键，且值不能为空：%s" % "、".join(PIN_KEYS))
        if any(character.isspace() for character in value) or "'" in value or '"' in value or "$" in value:
            sys.exit("值里有空白、引号或 $，compose 的环境文件不好原样表达: %s" % item)
        pairs[key] = value
    with open(args.env_file, "rb") as handle:
        raw = handle.read()
    newline = b"\r\n" if raw.count(b"\r\n") * 2 > raw.count(b"\n") else b"\n"
    output, changes, present = [], [], set()
    for line in raw.splitlines(keepends=True):
        match = re.match(rb"([ \t]*(?:export[ \t]+)?)([A-Za-z_][A-Za-z0-9_]*)[ \t]*=([^\r\n]*)(\r?\n?)$", line)
        if match and match.group(2).decode("ascii") in pairs:
            key = match.group(2).decode("ascii")
            present.add(key)
            replacement = match.group(1) + key.encode("ascii") + b"=" + pairs[key].encode("utf-8") + match.group(4)
            if replacement != line:
                changes.append({"key": key, "old": match.group(3).decode("utf-8"), "new": pairs[key]})
            output.append(replacement)
        else:
            output.append(line)
    if output and not output[-1].endswith(b"\n"):
        output[-1] += newline
    for key in pairs:
        if key not in present:
            output.append(key.encode("ascii") + b"=" + pairs[key].encode("utf-8") + newline)
            changes.append({"key": key, "old": None, "new": pairs[key]})
    if changes:
        temporary = args.env_file + ".pin-tmp"
        with open(temporary, "wb") as handle:
            handle.write(b"".join(output))
        os.replace(temporary, args.env_file)
    print(json.dumps(changes, ensure_ascii=False))
    return 0


def cmd_unpin(args):
    """把版本从环境文件里拿掉：只删那两个键所在的行，其余字节一律不动。回到"没钉住"的状态。"""
    raw = open(args.env_file, "rb").read()
    output, removed = [], []
    for line in raw.splitlines(keepends=True):
        match = re.match(rb"[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=", line)
        if match and match.group(1).decode("ascii") in PIN_KEYS:
            removed.append(match.group(1).decode("ascii"))
            continue
        output.append(line)
    if removed:
        temporary = args.env_file + ".pin-tmp"
        with open(temporary, "wb") as handle:
            handle.write(b"".join(output))
        os.replace(temporary, args.env_file)
    print(json.dumps({"removed": removed}))
    return 0


JOBS_SQL = ("SELECT (SELECT COUNT(*) FROM inference_jobs WHERE status IN ('queued', 'running')), "
            "(SELECT COUNT(*) FROM project_spatial_job WHERE status IN ('pending', 'queued', 'running'));")


def cmd_jobs(args):
    """排队或运行中的推理 / 切片任务数。查询限时；查不到就按"不能切"处理（返回 2），有任务返回 1。

    口令不出数据库容器：客户端从容器的环境变量里取，SQL 走标准输入。"""
    command = DOCKER + ["exec", "-i", "%s-mysql" % args.project, "sh", "-c",
                        'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql -u"$MYSQL_USER" -N -B "$MYSQL_DATABASE"']
    try:
        process = subprocess.run(command, input=JOBS_SQL.encode("utf-8"), capture_output=True, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        process = subprocess.CompletedProcess(command, 124, b"", b"timeout")
    counts = text(process).split()
    if process.returncode != 0 or len(counts) != 2 or not all(item.isdigit() for item in counts):
        print("查不到进行中的任务数（%s），不敢切。确认没有任务在跑可用 --allow-running-jobs。" % (
            "查询超时" if process.returncode == 124 else "数据库返回了意料之外的结果"))
        return 2
    inference, spatial = int(counts[0]), int(counts[1])
    print(json.dumps({"inference": inference, "spatial": spatial}))
    if inference or spatial:
        print("有任务在跑（推理 %d 个，切片 %d 个），现在切换会中断它们。等它们结束再切；"
              "确认可以中断（例如残留的僵尸任务）用 --allow-running-jobs。" % (inference, spatial))
        return 1
    return 0


def cmd_state(args):
    """切换状态：{current, previous, last_attempt, last_attempt_status, updated_at}。
    --rollback 据此决定回到哪一份：上一次切换成功 → 回 previous；失败或没切完 → 回 current。"""
    path = Path(args.path)
    state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if args.action == "show":
        print(json.dumps(state, ensure_ascii=False))
        return 0
    state.update(json.loads(args.patch))
    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    print(json.dumps(state, ensure_ascii=False))
    return 0


def cmd_rehearsal_config(args):
    source = (Path(args.release) / "config.yaml").read_bytes()
    ports = {"backend-port": args.backend_port, "frontend-port": args.frontend_port,
             "miner-api-port": args.miner_api_port, "miner-web-port": args.miner_web_port}
    if not all(value.isdigit() for value in ports.values()):
        sys.exit("端口必须是数字：%s" % ports)
    output, before = rewrite_config_ports(source, ports)
    for role, old in before.items():
        print("  %s: %s → %s" % (role, old, ports[role]))
    with open(args.output, "wb") as handle:
        handle.write(output)
    return 0


def same_data_mount(mount, volume, volume_names):
    """现有容器的一个数据挂载，与渲染结果里同一位置的挂载，是不是同一个东西。
    命名卷在 docker inspect 里是卷名（yunnan-rehearsal_backend_static），在渲染结果里是 compose 里的键
    （backend-static），要经 volume_names 换算后再比。2026-11-25 演练的第二次切换就是在这里被误报拦下的：
    首次上线没有现有容器可比，这条路径到第二次切换才第一次走到。"""
    if volume["type"] == "bind":
        return host_path(mount.get("Source", "")) == host_path(volume["source"])
    return mount.get("Name") == volume_names.get(volume["source"], volume["source"])


def cmd_render_check(args):
    """渲染后的 compose 配对账：源码与资产挂载各在其位，数据挂载与现有容器完全相同。"""
    release = release_path(args.release)
    expected = expected_sources(release, args)
    config = json.load(sys.stdin)
    problems = []
    for service, mounts in CODE_MOUNTS.items():
        if service not in config["services"]:
            continue
        volumes = config["services"][service].get("volumes", [])
        by_target = {volume["target"]: volume for volume in volumes}
        for target, relative, read_only in mounts:
            volume = by_target.get(target)
            if volume is None:
                problems.append("%s 缺挂载 %s" % (service, target))
                continue
            if volume["type"] != "bind" or host_path(volume["source"]) != expected[relative]:
                problems.append("%s 的 %s 指向 %r，应当在 %s" % (service, target, volume["source"], expected[relative]))
            if bool(volume.get("read_only")) != read_only:
                problems.append("%s 的 %s 读写属性变了（应为%s）" % (service, target, "只读" if read_only else "可写"))
        container = config["services"][service].get("container_name")
        process = docker("inspect", container)
        if process.returncode != 0:
            continue   # 这个容器还不存在（首次上线），没有可比的现有挂载
        existing = {mount["Destination"]: mount for mount in json.loads(text(process))[0].get("Mounts", [])}
        code_targets = {target for target, _, _ in mounts}
        volume_names = {key: definition.get("name", key) for key, definition in config.get("volumes", {}).items()}
        for target, volume in by_target.items():
            if target in code_targets:
                continue
            mount = existing.get(target)
            if mount is None:
                continue   # 演练栈新增的数据挂载
            if not same_data_mount(mount, volume, volume_names) or bool(mount["RW"]) == bool(volume.get("read_only")):
                problems.append("%s 的数据挂载 %s 变了：现有 %r → 将变成 %r" % (
                    service, target, mount.get("Source") or mount.get("Name"), volume["source"]))
    if getattr(args, "config_override", None):
        problem = rehearsal_config_problem(args.release, args.config_override)
        if problem:
            problems.append("演练 config.yaml：" + problem)
    for problem in problems:
        print("不符: " + problem)
    if not problems:
        print("渲染结果对账通过：源码挂载全部指向发布目录，资产挂载指向资产目录，数据挂载不变")
    return 1 if problems else 0


def release_capabilities(release):
    """这个发布自带哪些能力：(后端有没有几何接口, GeoView 是不是走后端校验)。按发布目录自己的源码判断，
    所以对回滚到的旧发布同样成立——它本来就没有这些，运行时也应当没有。"""
    release = Path(release)
    backend_file = release / "backend" / "applications" / "api" / "project.py"
    backend_source = backend_file.read_text(encoding="utf-8", errors="replace") if backend_file.is_file() else ""
    frontend_source = "".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (release / "frontend" / "src").rglob("*") if path.is_file() and path.suffix in (".js", ".mjs", ".vue"))
    return GEOVIEW_MARKER in backend_source, GEOVIEW_MARKER in frontend_source


def http(url, method="GET"):
    """返回 (状态码, 正文)。连不上时状态码为 None。"""
    request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, OSError):
        return None, b""


def cmd_verify(args):
    release = Path(args.release).resolve()
    rows = []

    def record(ok, title, detail=""):
        rows.append(bool(ok))
        print("[%s] %s%s" % ("通过" if ok else "失败", title, (" —— " + detail) if detail else ""))

    # ---- 发布目录自身 ----
    asset_root = asset_root_of(args)
    problems = release_problems(release, asset_root)
    for problem in problems:
        record(False, "发布目录", problem)
    if problems:
        print("\n发布目录与清单不符，后面的核对没有意义。")
        return 1
    manifest = load_manifest(release)
    record(True, "发布目录", "%s %s《%s》，%d 个代码路径指纹与清单一致，依赖与锁文件一致" % (
        manifest["name"], manifest["commit_short"], manifest["commit_subject"], len(manifest["trees"])))
    expected = expected_sources(release_path(release), args)
    created_at = parse_time(manifest["created_at"])
    if getattr(args, "config_override", None):
        problem = rehearsal_config_problem(release, args.config_override)
        record(problem is None, "演练 config.yaml", problem or "与发布目录那一份只差 4 个端口")

    # ---- 运行中的容器 ----
    skipped = {item for item in args.skip.split(",") if item}
    fingerprint_script = Path(__file__).resolve().with_name("tree_hash.py").read_bytes()
    exclude_arguments = [item for relative in EXCLUDED for item in ("--exclude", relative)]
    recorded_assets = manifest.get("assets", {})
    for service, mounts in CODE_MOUNTS.items():
        if service in skipped:
            print("[跳过] %s（--skip）" % service)
            continue
        container = "%s-%s" % (args.project, service)
        process = docker("inspect", container)
        if process.returncode != 0:
            record(False, service, "容器 %s 不存在" % container)
            continue
        state = json.loads(text(process))[0]
        if not state["State"]["Running"]:
            record(False, service, "容器 %s 没在运行：%s" % (container, state["State"].get("Status")))

        wrong = []
        existing = {mount["Destination"]: mount for mount in state.get("Mounts", [])}
        for target, relative, read_only in mounts:
            mount = existing.get(target)
            if mount is None or mount["Type"] != "bind":
                wrong.append("%s 没有绑定挂载" % target)
            elif host_path(mount["Source"]) != expected[relative]:
                wrong.append("%s 挂在 %s" % (target, mount["Source"]))
            elif bool(mount["RW"]) == read_only:
                wrong.append("%s 的读写属性不对" % target)
        record(not wrong, "%s 挂载来源" % service,
               "；".join(wrong) or "代码指向 %s，资产指向 %s" % (manifest["name"], asset_root))

        started_at = parse_time(state["State"]["StartedAt"])
        record(started_at >= created_at, "%s 启动时间" % service, "%s（发布目录生成于 %s）%s" % (
            started_at.strftime("%Y-%m-%d %H:%M:%S"), manifest["created_at"],
            "" if started_at >= created_at else " —— 容器早于发布目录启动，内存里的不是这份代码"))

        relatives = [relative for _, relative, _ in mounts
                     if relative in manifest["trees"] and not (args.config_override and relative == "config.yaml")]
        process = docker("exec", "-i", container, "python", "-", *exclude_arguments, "/app", *relatives,
                      stdin=fingerprint_script)
        if process.returncode != 0:
            record(False, "%s 容器内代码指纹" % service, text(process)[-300:] or "docker exec 失败")
        else:
            inside = json.loads(text(process))
            different = [relative for relative in relatives if inside.get(relative) != manifest["trees"][relative]]
            record(not different, "%s 容器内代码指纹" % service,
                   "与清单不同: %s" % "、".join(different) if different else "%d 个路径与清单一致" % len(relatives))

        asset_paths = [relative for _, relative, _ in mounts if relative in recorded_assets]
        if asset_paths:
            process = docker("exec", "-i", container, "python", "-", "--stat", "/app", *asset_paths, stdin=fingerprint_script)
            if process.returncode != 0:
                record(False, "%s 运行时资产" % service, text(process)[-300:] or "docker exec 失败")
            else:
                inside = json.loads(text(process))
                different = [relative for relative in asset_paths
                             if (inside[relative]["kind"], inside[relative]["files"], inside[relative]["bytes"])
                             != (recorded_assets[relative]["kind"], recorded_assets[relative]["files"], recorded_assets[relative]["bytes"])]
                record(not different, "%s 运行时资产" % service,
                       "与清单不同: %s" % "、".join(different) if different else "%d 个资产的类型、文件数、字节数与清单一致" % len(asset_paths))
            if args.hash_assets:
                for relative in asset_paths:
                    if ASSET_KIND[relative] == "file":
                        process = docker("exec", container, "sha256sum", "/app/" + relative, timeout=ASSET_HASH_TIMEOUT)
                        digest = text(process).split()[0] if process.returncode == 0 else None
                    else:
                        process = docker("exec", "-i", container, "python", "-", "/app", relative, stdin=fingerprint_script, timeout=ASSET_HASH_TIMEOUT)
                        digest = json.loads(text(process)).get(relative, {}).get("sha256") if process.returncode == 0 else None
                    record(digest == recorded_assets[relative]["sha256"], "%s 资产 %s 的 SHA256" % (service, Path(relative).name))

        health = state["State"].get("Health", {}).get("Status")
        record(health == "healthy", "%s 健康检查" % service, health or "没有健康检查")
        image_key = SERVICE_IMAGE.get(service)
        if image_key and manifest["images"].get(image_key, {}).get("id"):
            record(state["Image"] == manifest["images"][image_key]["id"], "%s 镜像" % service,
                   "" if state["Image"] == manifest["images"][image_key]["id"] else "运行的是 %s，清单记的是 %s" % (
                       state["Image"][:19], manifest["images"][image_key]["id"][:19]))

    # ---- 接口 ----
    status, _ = http(args.backend_url + "/api/health")
    record(status == 200, "后端健康接口", str(status))
    status, body = http(args.backend_url + "/api/system/version")
    reported = json.loads(body).get("data", {}).get("git_sha") if status == 200 else None
    record(reported == manifest["commit_short"], "后端版本自述", "git_sha=%s，发布目录 %s" % (reported, manifest["commit_short"]))
    # 期望什么由发布目录自己的代码决定：回滚到的旧发布本来就没有几何接口，运行时也应当没有
    geometry_routes, geoview_validation = release_capabilities(release)
    for name in ("validate", "simplify", "union"):
        status, _ = http("%s/api/projects/1/classification-results/1/geometry/%s" % (args.backend_url, name), "POST")
        if geometry_routes:
            record(status == 401, "几何接口 %s 存在且要求登录" % name,
                   "未登录返回 %s" % status + ("" if status == 401 else "（404 = 内存里是没有几何引擎的旧后端）"))
        else:
            record(status == 404, "几何接口 %s 在这个发布里不存在，运行时也没有" % name, "未登录返回 %s" % status)
    status, _ = http(args.miner_api_url + "/api/auth/session")
    record(status == 200, "Miner 后端适配层", str(status))

    status, body = http(args.frontend_url + "/")
    record(status == 200, "GeoView 首页", str(status))
    if status == 200:
        scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', body.decode("utf-8", "replace"))
        app = [item for item in scripts if "/js/app." in item]
        if not app:
            record(False, "GeoView 入口脚本", "首页没有引用 /js/app.*.js（引用的是 %s）" % ("、".join(scripts) or "无"))
        else:
            status, _ = http(urllib.parse.urljoin(args.frontend_url + "/", app[0]))
            record(status == 200, "GeoView 入口脚本", "%s 返回 %s" % (app[0], status))
    process = docker("exec", "%s-frontend" % args.project, "grep", "-rl", GEOVIEW_MARKER, "/app/frontend/dist/js")
    found = text(process).split()
    if geoview_validation:
        record(bool(found), "GeoView 已是带后端校验的构建",
               "标记串 %s 出现在 %d 个文件里" % (GEOVIEW_MARKER, len(found)) if found else "产物里找不到 " + GEOVIEW_MARKER)
    else:
        record(not found, "GeoView 产物与这个发布的源码一致（这个发布不带后端校验）",
               "" if not found else "产物里不该出现标记串 %s，却有 %d 个文件带着" % (GEOVIEW_MARKER, len(found)))
    status, _ = http(args.miner_web_url + "/")
    record(status == 200, "Miner 首页", str(status))

    # ---- 环境文件里钉住的版本 ----
    if args.env_file:
        pinned = env_values(args.env_file, PIN_KEYS)
        record(bool(pinned["YUNNAN_SOURCE_ROOT"]) and host_path(pinned["YUNNAN_SOURCE_ROOT"]) == release_path(release),
               ".env 钉住的源码目录", pinned["YUNNAN_SOURCE_ROOT"] or "没有 YUNNAN_SOURCE_ROOT")
        record(pinned["YUNNAN_GIT_SHA"] == manifest["commit_short"], ".env 钉住的版本", pinned["YUNNAN_GIT_SHA"] or "没有 YUNNAN_GIT_SHA")

    failed = rows.count(False)
    print("\n核对 %d 项，%d 项不符。" % (len(rows), failed))
    return 1 if failed else 0


def main(argv):
    parser = argparse.ArgumentParser(description="发布目录工具")
    commands = parser.add_subparsers(dest="command", required=True)

    trees = commands.add_parser("trees", help="列出发布目录入账的路径")
    trees.set_defaults(handler=cmd_trees)

    untracked = commands.add_parser("untracked-check", help="发布路径下未提交的条目逐个归类")
    untracked.add_argument("--repo", required=True, help="提交所在的仓库目录")
    untracked.add_argument("--asset-root", required=True, help="运行时资产所在的目录")
    untracked.set_defaults(handler=cmd_untracked_check)

    placeholders = commands.add_parser("placeholders", help="建空挂载点")
    placeholders.add_argument("release")
    placeholders.set_defaults(handler=cmd_placeholders)

    manifest = commands.add_parser("manifest", help="写 RELEASE.json")
    manifest.add_argument("release")
    manifest.add_argument("--commit", required=True)
    manifest.add_argument("--repo", required=True, help="提交所在的仓库目录")
    manifest.add_argument("--name")
    manifest.add_argument("--data-dir", default=".", help="compose 运行的项目目录，用来给 KML、xlsx 记指纹")
    manifest.add_argument("--asset-root", required=True, help="运行时资产所在的目录")
    manifest.add_argument("--asset-cache", help="资产指纹缓存，大文件没变就不重算")
    manifest.set_defaults(handler=cmd_manifest)

    check = commands.add_parser("check", help="核对发布目录自身")
    check.add_argument("release")
    check.add_argument("--asset-root", help="运行时资产所在的目录")
    check.add_argument("--env-file", help="compose 用的环境文件，从中读 YUNNAN_ASSET_ROOT")
    check.set_defaults(handler=cmd_check)

    pin = commands.add_parser("pin", help="把版本钉进环境文件")
    pin.add_argument("env_file")
    pin.add_argument("pairs", nargs="+")
    pin.set_defaults(handler=cmd_pin)

    pinned = commands.add_parser("pinned", help="读出环境文件里钉住的版本")
    pinned.add_argument("env_file")
    pinned.add_argument("keys", nargs="+")
    pinned.set_defaults(handler=cmd_pinned)

    unpin = commands.add_parser("unpin", help="把版本从环境文件里拿掉")
    unpin.add_argument("env_file")
    unpin.set_defaults(handler=cmd_unpin)

    jobs = commands.add_parser("jobs", help="排队或运行中的推理 / 切片任务数（0 = 可以切，1 = 有任务，2 = 查不到）")
    jobs.add_argument("--project", default="yunnan")
    jobs.add_argument("--timeout", type=float, default=30.0)
    jobs.set_defaults(handler=cmd_jobs)

    state = commands.add_parser("state", help="读或更新切换状态")
    state.add_argument("action", choices=["show", "update"])
    state.add_argument("path")
    state.add_argument("patch", nargs="?", default="{}")
    state.set_defaults(handler=cmd_state)

    config = commands.add_parser("rehearsal-config", help="由发布目录的 config.yaml 生成演练用的那一份（只改 4 个端口）")
    config.add_argument("release")
    config.add_argument("output")
    config.add_argument("--backend-port", default="25008")
    config.add_argument("--frontend-port", default="23000")
    config.add_argument("--miner-api-port", default="28000")
    config.add_argument("--miner-web-port", default="24000")
    config.set_defaults(handler=cmd_rehearsal_config)

    render = commands.add_parser("render-check", help="渲染后的 compose 配置对账（从标准输入读）")
    render.add_argument("release")
    render.add_argument("--config-override", help="演练栈挂的另一份 config.yaml")
    render.add_argument("--env-file", help="compose 用的环境文件，从中读 YUNNAN_ASSET_ROOT")
    render.add_argument("--asset-root", help="运行时资产所在的目录")
    render.set_defaults(handler=cmd_render_check)

    verify = commands.add_parser("verify", help="核对运行中的栈")
    verify.add_argument("release")
    verify.add_argument("--project", default="yunnan")
    verify.add_argument("--skip", default="", help="不核对的服务，逗号分隔（例如隔离演练栈没有的 inference-worker）")
    verify.add_argument("--env-file", help="compose 用的第一个环境文件（默认 .env），核对里面钉住的版本")
    verify.add_argument("--config-override", help="演练栈挂的另一份 config.yaml")
    verify.add_argument("--asset-root", help="运行时资产所在的目录")
    verify.add_argument("--hash-assets", action="store_true", help="容器内重算每个资产的 SHA256（权重 7.5GB，要几分钟）")
    verify.add_argument("--backend-url", default=os.environ.get("VERIFY_BACKEND_URL", "http://127.0.0.1:5008"))
    verify.add_argument("--frontend-url", default=os.environ.get("VERIFY_FRONTEND_URL", "http://127.0.0.1:3000"))
    verify.add_argument("--miner-api-url", default=os.environ.get("VERIFY_MINER_API_URL", "http://127.0.0.1:8000"))
    verify.add_argument("--miner-web-url", default=os.environ.get("VERIFY_MINER_WEB_URL", "http://127.0.0.1:4000"))
    verify.set_defaults(handler=cmd_verify)

    args = parser.parse_args(argv[1:])
    # 输出固定为 UTF-8 加 LF：Windows 上默认按本地编码、CRLF 写管道，脚本读进去会带上回车
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", newline="\n")
    return args.handler(args) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
