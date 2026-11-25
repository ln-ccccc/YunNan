#!/usr/bin/env python3
"""发布工具的自测：只碰临时目录，不需要 git 仓库或网络（只有渲染对账那组要用到 docker 命令）。

    python -m unittest discover -s deploy -p "test_release_tool.py"

覆盖的都是"弄错了会伤到生产"的地方：改 .env 的字节级行为、路径归一化、演练 config 只差 4 个端口、
指纹对挂载点的排除、空挂载点、依赖与锁文件、未提交条目的归类、渲染结果的对账。
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_tool  # noqa: E402
import tree_hash  # noqa: E402

PORTS = {"backend-port": "25008", "frontend-port": "23000", "miner-api-port": "28000", "miner-web-port": "24000"}
CONFIG = (b"port:\n  backend: 5008\n  frontend: 3000\nhost:\n  backend: 0.0.0.0\ndebug: false\nminer:\n  enabled: true\n"
          b"  frontend_port: 4000\n  backend_port: 8000")
CONFIG_REHEARSAL = (b"port:\n  backend: 25008\n  frontend: 23000\nhost:\n  backend: 0.0.0.0\ndebug: false\nminer:\n  enabled: true\n"
                    b"  frontend_port: 24000\n  backend_port: 28000")


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    return path


def read(path):
    return Path(path).read_bytes()


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="release-tool-test-"))
        self.addCleanup(shutil.rmtree, self.root, True)


class HostPathTest(unittest.TestCase):
    def test_forms_of_the_same_windows_path_are_equal(self):
        forms = ["/run/desktop/mnt/host/d/项目/yunnan-releases/x/backend", "D:\\项目\\yunnan-releases\\x\\backend",
                 "d:/项目/yunnan-releases/x/./backend"]
        self.assertEqual(1, len({release_tool.host_path(form) for form in forms}))

    def test_non_windows_paths_keep_case(self):
        self.assertNotEqual(release_tool.host_path("/srv/App/x"), release_tool.host_path("/srv/app/x"))


class PinTest(TempDirTestCase):
    SECRET_LINE = b"MYSQL_PASSWORD='s3cr3t value with space'\r\n"
    BASE = b"# YUNNAN_SOURCE_ROOT=1 is a comment\r\n" + SECRET_LINE + b'OTHER="quoted \\ value"\r\nexport OLD_STYLE=1\r\n'

    def pin(self, path, *pairs):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = release_tool.cmd_pin(types.SimpleNamespace(env_file=str(path), pairs=list(pairs)))
        return code, json.loads(output.getvalue())

    def pinned(self, path, *keys):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            release_tool.cmd_pinned(types.SimpleNamespace(env_file=str(path), keys=list(keys)))
        return json.loads(output.getvalue())

    def test_crlf_file_without_the_keys_appends_and_leaves_other_bytes_alone(self):
        path = write(self.root / "a.env", self.BASE)
        code, changes = self.pin(path, "YUNNAN_SOURCE_ROOT=D:/rel/a", "YUNNAN_GIT_SHA=abc1234")
        self.assertEqual(0, code)
        self.assertEqual([
            {"key": "YUNNAN_SOURCE_ROOT", "old": None, "new": "D:/rel/a"},
            {"key": "YUNNAN_GIT_SHA", "old": None, "new": "abc1234"}], changes)
        self.assertEqual(self.BASE + b"YUNNAN_SOURCE_ROOT=D:/rel/a\r\nYUNNAN_GIT_SHA=abc1234\r\n", read(path))
        self.assertIn(self.SECRET_LINE, read(path))
        self.assertEqual({"YUNNAN_SOURCE_ROOT": "D:/rel/a", "YUNNAN_GIT_SHA": "abc1234"},
                         self.pinned(path, "YUNNAN_SOURCE_ROOT", "YUNNAN_GIT_SHA"))

    def test_pinning_the_same_values_again_does_not_write(self):
        path = write(self.root / "a.env", self.BASE)
        self.pin(path, "YUNNAN_SOURCE_ROOT=D:/rel/a", "YUNNAN_GIT_SHA=abc1234")
        before = os.stat(path).st_mtime_ns
        code, changes = self.pin(path, "YUNNAN_SOURCE_ROOT=D:/rel/a", "YUNNAN_GIT_SHA=abc1234")
        self.assertEqual((0, []), (code, changes))
        self.assertEqual(before, os.stat(path).st_mtime_ns)

    def test_rollback_replaces_both_lines_in_place(self):
        path = write(self.root / "a.env", self.BASE)
        self.pin(path, "YUNNAN_SOURCE_ROOT=D:/rel/a", "YUNNAN_GIT_SHA=abc1234")
        code, changes = self.pin(path, "YUNNAN_SOURCE_ROOT=D:/rel/b", "YUNNAN_GIT_SHA=def5678")
        self.assertEqual(0, code)
        self.assertEqual([
            {"key": "YUNNAN_SOURCE_ROOT", "old": "D:/rel/a", "new": "D:/rel/b"},
            {"key": "YUNNAN_GIT_SHA", "old": "abc1234", "new": "def5678"}], changes)
        self.assertEqual(self.BASE + b"YUNNAN_SOURCE_ROOT=D:/rel/b\r\nYUNNAN_GIT_SHA=def5678\r\n", read(path))

    def test_lf_file_without_trailing_newline_and_export_prefix(self):
        path = write(self.root / "b.env", b"A=1\nexport YUNNAN_SOURCE_ROOT=D:/old\n# YUNNAN_GIT_SHA=comment\nB=2")
        code, changes = self.pin(path, "YUNNAN_SOURCE_ROOT=D:/rel/c")
        self.assertEqual(0, code)
        self.assertEqual(b"A=1\nexport YUNNAN_SOURCE_ROOT=D:/rel/c\n# YUNNAN_GIT_SHA=comment\nB=2\n", read(path))
        self.assertEqual([{"key": "YUNNAN_SOURCE_ROOT", "old": "D:/old", "new": "D:/rel/c"}], changes)
        self.pin(path, "YUNNAN_SOURCE_ROOT=D:/rel/c", "YUNNAN_GIT_SHA=abc1234")
        self.assertEqual(b"A=1\nexport YUNNAN_SOURCE_ROOT=D:/rel/c\n# YUNNAN_GIT_SHA=comment\nB=2\nYUNNAN_GIT_SHA=abc1234\n", read(path))

    def test_refuses_other_keys_and_unwritable_values(self):
        path = write(self.root / "c.env", b"A=1\n")
        for bad in (["MYSQL_PASSWORD=x"], ["YUNNAN_GIT_SHA="], ["YUNNAN_SOURCE_ROOT=D:/a b"], ['YUNNAN_SOURCE_ROOT="$HOME"']):
            with self.subTest(bad=bad), self.assertRaises(SystemExit):
                self.pin(path, *bad)
            self.assertEqual(b"A=1\n", read(path))

    def test_quoted_values_are_replaced_and_export_prefix_kept(self):
        path = write(self.root / "d.env", b'YUNNAN_GIT_SHA="abc1234"\nexport YUNNAN_SOURCE_ROOT=\'D:/old\'\n')
        self.assertEqual({"YUNNAN_SOURCE_ROOT": "D:/old", "YUNNAN_GIT_SHA": "abc1234"},
                         self.pinned(path, "YUNNAN_SOURCE_ROOT", "YUNNAN_GIT_SHA"))
        self.pin(path, "YUNNAN_SOURCE_ROOT=D:/new")
        self.assertEqual(b'YUNNAN_GIT_SHA="abc1234"\nexport YUNNAN_SOURCE_ROOT=D:/new\n', read(path))

    def test_empty_file(self):
        path = write(self.root / "e.env", b"")
        self.pin(path, "YUNNAN_GIT_SHA=abc1234")
        self.assertEqual(b"YUNNAN_GIT_SHA=abc1234\n", read(path))


class RehearsalConfigTest(TempDirTestCase):
    def test_only_four_port_values_change(self):
        output, before = release_tool.rewrite_config_ports(CONFIG, PORTS)
        self.assertEqual(CONFIG_REHEARSAL, output)
        self.assertEqual({"backend-port": "5008", "frontend-port": "3000", "miner-web-port": "4000", "miner-api-port": "8000"}, before)

    def test_crlf_line_endings_are_kept(self):
        output, _ = release_tool.rewrite_config_ports(CONFIG.replace(b"\n", b"\r\n"), PORTS)
        self.assertEqual(CONFIG_REHEARSAL.replace(b"\n", b"\r\n"), output)

    def test_unexpected_structure_is_an_error(self):
        with self.assertRaises(ValueError):
            release_tool.rewrite_config_ports(b"port:\n  backend: 5008\n", PORTS)

    def test_problem_detection(self):
        write(self.root / "release" / "config.yaml", CONFIG)
        override = write(self.root / "config.rehearsal.yaml", CONFIG_REHEARSAL)
        self.assertIsNone(release_tool.rehearsal_config_problem(self.root / "release", override))
        tampered = write(self.root / "tampered.yaml", CONFIG_REHEARSAL.replace(b"enabled: true", b"enabled: false"))
        self.assertIn("差异不止 4 个端口", release_tool.rehearsal_config_problem(self.root / "release", tampered))
        self.assertTrue(release_tool.rehearsal_config_problem(self.root / "release", self.root / "missing.yaml"))


class TreeHashTest(TempDirTestCase):
    def test_fingerprint_changes_with_content_and_name(self):
        first = write(self.root / "a" / "x.py", "print(1)\n")
        baseline = tree_hash.tree_fingerprint(str(self.root), "a")
        write(first, "print(2)\n")
        self.assertNotEqual(baseline, tree_hash.tree_fingerprint(str(self.root), "a"))
        first.rename(first.with_name("y.py"))
        self.assertNotEqual(baseline["sha256"], tree_hash.tree_fingerprint(str(self.root), "a")["sha256"])

    def test_excluded_paths_are_skipped_including_whole_directories(self):
        weights = self.root / "backend" / "model" / "mmseg_config" / "model.pth"
        write(self.root / "backend" / "app.py", "x\n")
        write(self.root / "backend" / "static" / "upload.bin", "data")
        write(self.root / "backend" / "runtime" / "inference_jobs" / "1" / "job.json", "{}")
        write(weights, "weights")
        write(self.root / "backend" / "model" / "mmseg_config" / "dinov3_swinV1" / "backbone.pth", "weights")
        excluded = frozenset(release_tool.EXCLUDED)
        baseline = tree_hash.tree_fingerprint(str(self.root), "backend", excluded)
        self.assertEqual(1, baseline["files"])   # 只有 app.py
        # 挂载点下面多出来的东西、换了内容的权重，都不该影响代码指纹
        write(self.root / "backend" / "static" / "more.bin", "more")
        write(weights, "other weights")
        write(self.root / "backend" / "runtime" / "inference_jobs" / "2" / "job.json", "{}")
        self.assertEqual(baseline, tree_hash.tree_fingerprint(str(self.root), "backend", excluded))
        write(self.root / "backend" / "app.py", "y\n")
        self.assertNotEqual(baseline, tree_hash.tree_fingerprint(str(self.root), "backend", excluded))

    def test_pycache_is_always_skipped(self):
        write(self.root / "backend" / "app.py", "x\n")
        baseline = tree_hash.tree_fingerprint(str(self.root), "backend")
        write(self.root / "backend" / "__pycache__" / "app.cpython-310.pyc", "bytes")
        write(self.root / "backend" / "app.pyc", "bytes")
        self.assertEqual(baseline, tree_hash.tree_fingerprint(str(self.root), "backend"))

    def test_stat_mode(self):
        write(self.root / "backend" / "model" / "w.pth", "weights")
        write(self.root / "backend" / "model" / "vendor" / "a.py", "1")
        write(self.root / "backend" / "model" / "vendor" / "b.py", "22")
        write(self.root / "backend" / "model" / "vendor" / "__pycache__" / "a.pyc", "0")
        self.assertEqual({"kind": "dir", "files": 2, "bytes": 3}, tree_hash.stat_path(str(self.root), "backend/model/vendor"))
        self.assertEqual("file", tree_hash.stat_path(str(self.root / "backend" / "model"), "w.pth")["kind"])
        self.assertEqual(7, tree_hash.stat_path(str(self.root / "backend" / "model"), "w.pth")["bytes"])

    def test_missing_or_empty_is_an_error(self):
        with self.assertRaises(OSError):
            tree_hash.tree_fingerprint(str(self.root), "nowhere")
        (self.root / "empty").mkdir()
        with self.assertRaises(ValueError):
            tree_hash.tree_fingerprint(str(self.root), "empty")
        with self.assertRaises(ValueError):
            tree_hash.tree_fingerprint(str(self.root), "../outside")


class PlaceholdersTest(TempDirTestCase):
    def placeholders(self):
        with contextlib.redirect_stdout(io.StringIO()):
            release_tool.cmd_placeholders(types.SimpleNamespace(release=str(self.root / "release")))

    def test_fresh_release_has_only_empty_mountpoints(self):
        self.placeholders()
        self.assertEqual([], release_tool.placeholder_problems(self.root / "release"))
        for relative in release_tool.MOUNTPOINTS:
            self.assertTrue((self.root / "release" / relative).is_dir(), relative)
        for relative in release_tool.ASSET_PATHS:
            if release_tool.ASSET_KIND[relative] == "file":
                self.assertEqual(0, (self.root / "release" / relative).stat().st_size, relative)
            else:
                self.assertTrue((self.root / "release" / relative).is_dir(), relative)

    def test_filled_mountpoint_is_a_problem(self):
        self.placeholders()
        release = self.root / "release"
        write(release / "backend" / "static" / "stray.bin", "x")
        write(release / "backend" / "model" / "mmseg_config" / "model.pth", "not empty")
        problems = release_tool.placeholder_problems(release)
        self.assertEqual(2, len(problems), problems)
        self.assertTrue(any("backend/static" in item for item in problems))
        self.assertTrue(any("model.pth" in item for item in problems))

    def test_backend_fingerprint_ignores_mountpoints_on_both_sides(self):
        self.placeholders()
        release = self.root / "release"
        write(release / "backend" / "app.py", "x\n")
        host = tree_hash.tree_fingerprint(str(release), "backend", frozenset(release_tool.EXCLUDED))
        # 容器里：挂载点被卷和真资产盖住
        write(release / "backend" / "static" / "upload.bin", "data")
        write(release / "backend" / "model" / "mmseg_config" / "model.pth", "weights" * 1000)
        write(release / "backend" / "model" / "mmseg_config" / "dinov3_swinV1" / "backbone.pth", "weights")
        inside = tree_hash.tree_fingerprint(str(release), "backend", frozenset(release_tool.EXCLUDED))
        self.assertEqual(host, inside)


class NodeModulesTest(TempDirTestCase):
    def build(self, locked, installed):
        frontend = self.root / "frontend"
        write(frontend / "package-lock.json", json.dumps({"lockfileVersion": 3, "packages": locked}))
        write(frontend / "node_modules" / ".package-lock.json", json.dumps({"packages": installed}))
        return frontend

    def test_consistent(self):
        locked = {"": {"name": "root"}, "node_modules/a": {"version": "1.0.0"}, "node_modules/b": {"version": "2.0.0", "optional": True}}
        self.assertEqual([], release_tool.node_modules_problems(self.build(locked, locked)))

    def test_missing_required_package(self):
        locked = {"": {}, "node_modules/a": {"version": "1.0.0"}}
        problems = release_tool.node_modules_problems(self.build(locked, {"": {}}))
        self.assertEqual(["依赖目录缺包: node_modules/a"], problems)

    def test_missing_optional_package_is_fine(self):
        # 只在 macOS 上装的 fsevents：锁文件里有，Windows 上不装
        locked = {"": {}, "node_modules/fsevents": {"version": "2.3.3", "optional": True, "os": ["darwin"]}}
        self.assertEqual([], release_tool.node_modules_problems(self.build(locked, {"": {}})))

    def test_version_mismatch_and_extra_package(self):
        locked = {"": {}, "node_modules/a": {"version": "1.0.0"}}
        problems = release_tool.node_modules_problems(self.build(locked, {"": {}, "node_modules/a": {"version": "1.0.1"}, "node_modules/z": {"version": "9"}}))
        self.assertIn("node_modules/a 版本不符: 锁文件 1.0.0，目录 1.0.1", problems)
        self.assertIn("依赖目录多出锁文件里没有的包: node_modules/z", problems)

    def test_installed_by_something_else(self):
        frontend = self.build({"": {}}, {"": {}})
        (frontend / "node_modules" / ".package-lock.json").unlink()
        self.assertTrue(any("不是 npm ci 装的" in item for item in release_tool.node_modules_problems(frontend)))


class UntrackedClassificationTest(unittest.TestCase):
    def test_classification(self):
        entries = list(release_tool.ASSET_PATHS) + [
            "backend/__pycache__", "backend/applications/__pycache__", "backend/app/x.pyc", "backend/.venv",
            "backend/model/mmseg_config/dinov3_swinV1.py:Zone.Identifier", "backend/.flaskenv",
            "backend/static", "backend/model/mmseg_config/model.inference.pth.sha256", "backend/scratch/thing.pkl",
        ]
        assets, junk, unknown = release_tool.classify_untracked(entries)
        self.assertEqual(7, len(assets))
        self.assertEqual(["backend/scratch/thing.pkl"], unknown)
        flat = sorted(item for paths in junk.values() for item in paths)
        self.assertEqual(sorted([
            "backend/__pycache__", "backend/applications/__pycache__", "backend/app/x.pyc", "backend/.venv",
            "backend/model/mmseg_config/dinov3_swinV1.py:Zone.Identifier", "backend/.flaskenv",
            "backend/model/mmseg_config/model.inference.pth.sha256"]), flat)


class AssetRootTest(TempDirTestCase):
    def sources(self, env_lines, **extra):
        env_file = write(self.root / "yunnan.env", env_lines)
        args = types.SimpleNamespace(env_file=str(env_file), config_override=extra.get("config_override"), asset_root=extra.get("asset_root"))
        return release_tool.asset_root_of(args), release_tool.expected_sources("d:/releases/x", args)

    def test_asset_root_comes_from_env_file_and_defaults_to_its_directory(self):
        root, sources = self.sources("MYSQL_PASSWORD=x\nYUNNAN_ASSET_ROOT=E:/models\n")
        self.assertEqual("e:/models", root)
        self.assertEqual("e:/models/backend/model/mmseg_config/model.pth", sources["backend/model/mmseg_config/model.pth"])
        self.assertEqual("d:/releases/x/backend", sources["backend"])
        self.assertEqual("d:/releases/x/frontend/node_modules", sources["frontend/node_modules"])

    def test_default_asset_root_is_where_compose_runs(self):
        root, sources = self.sources("MYSQL_PASSWORD=x\n")
        self.assertEqual(release_tool.host_path(str(self.root)), root)
        self.assertEqual(root + "/backend/model/semantic_segmentation", sources["backend/model/semantic_segmentation"])

    def test_explicit_root_and_config_override_win(self):
        root, sources = self.sources("YUNNAN_ASSET_ROOT=E:/models\n", asset_root="F:/weights", config_override=str(self.root / "c.yaml"))
        self.assertEqual("f:/weights", root)
        self.assertEqual(release_tool.host_path(str(self.root / "c.yaml")), sources["config.yaml"])

    def test_every_mount_has_an_expected_source(self):
        _, sources = self.sources("MYSQL_PASSWORD=x\n")
        for mounts in release_tool.CODE_MOUNTS.values():
            for _, relative, _ in mounts:
                self.assertIn(relative, sources, relative)


class SameDataMountTest(unittest.TestCase):
    """现有容器的数据挂载与渲染结果比对的规则。"""

    NAMES = {"backend-static": "yunnan-rehearsal_backend_static", "mysql-data": "yunnan-rehearsal_mysql_data"}

    def test_named_volume_is_compared_by_its_real_name(self):
        existing = {"Type": "volume", "Name": "yunnan-rehearsal_backend_static", "Source": "/var/lib/docker/volumes/yunnan-rehearsal_backend_static/_data"}
        rendered = {"type": "volume", "source": "backend-static"}
        self.assertTrue(release_tool.same_data_mount(existing, rendered, self.NAMES))

    def test_a_different_volume_is_a_change(self):
        existing = {"Type": "volume", "Name": "yunnan_mysql_data", "Source": "/var/lib/docker/volumes/yunnan_mysql_data/_data"}
        rendered = {"type": "volume", "source": "mysql-data"}
        self.assertFalse(release_tool.same_data_mount(existing, rendered, self.NAMES))   # 挂到了生产的卷上

    def test_bind_is_compared_by_host_path(self):
        existing = {"Type": "bind", "Source": "/run/desktop/mnt/host/d/项目/YunNan/project_storage"}
        rendered = {"type": "bind", "source": "D:/项目/YunNan/./project_storage"}
        self.assertTrue(release_tool.same_data_mount(existing, rendered, self.NAMES))
        rendered["source"] = "D:/other/project_storage"
        self.assertFalse(release_tool.same_data_mount(existing, rendered, self.NAMES))


@unittest.skipUnless(shutil.which("docker"), "没有 docker 命令")
class RenderCheckTest(TempDirTestCase):
    """渲染对账的主体逻辑。现有容器用一个绝不存在的名字去查，等价于首次上线。"""

    def rendered(self, release, asset_root, config_override=None):
        def bind(source, target, read_only=True):
            return {"type": "bind", "source": source, "target": target, "read_only": read_only}
        volumes = [bind(release + "\\backend", "/app/backend"), bind(release + "\\docker", "/app/docker"),
                   bind(config_override or release + "\\config.yaml", "/app/config.yaml")]
        volumes += [bind(asset_root + "\\" + path.replace("/", "\\"), "/app/" + path) for path in release_tool.ASSET_PATHS]
        return {"services": {"backend": {"container_name": "release-tool-test-nonexistent", "volumes": volumes}}}

    def check(self, payload, *arguments):
        output = io.StringIO()
        stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(payload))   # contextlib 没有重定向标准输入的函数
        try:
            with contextlib.redirect_stdout(output):
                code = release_tool.cmd_render_check(types.SimpleNamespace(
                    release=str(self.root / "release"), config_override=None, asset_root=None,
                    env_file=str(self.root / "yunnan.env")))
        finally:
            sys.stdin = stdin
        return code, output.getvalue()

    def test_all_mounts_in_place_passes(self):
        write(self.root / "yunnan.env", "MYSQL_PASSWORD=x\n")
        release = release_tool.host_path(str(self.root / "release"))
        code, output = self.check(self.rendered(release, release_tool.host_path(str(self.root))))
        self.assertEqual(0, code, output)
        self.assertIn("对账通过", output)

    def test_code_mounted_from_the_dev_tree_is_caught(self):
        write(self.root / "yunnan.env", "MYSQL_PASSWORD=x\n")
        release = release_tool.host_path(str(self.root / "release"))
        payload = self.rendered(release, release_tool.host_path(str(self.root)))
        payload["services"]["backend"]["volumes"][0]["source"] = str(self.root / "backend")
        code, output = self.check(payload)
        self.assertEqual(1, code)
        self.assertIn("/app/backend", output)

    def test_missing_weight_mount_is_caught(self):
        write(self.root / "yunnan.env", "MYSQL_PASSWORD=x\n")
        release = release_tool.host_path(str(self.root / "release"))
        payload = self.rendered(release, release_tool.host_path(str(self.root)))
        payload["services"]["backend"]["volumes"] = [
            volume for volume in payload["services"]["backend"]["volumes"] if not volume["target"].endswith("model.pth")]
        code, output = self.check(payload)
        self.assertEqual(1, code)
        self.assertIn("缺挂载 /app/backend/model/mmseg_config/model.pth", output)

    def test_writable_weight_mount_is_caught(self):
        write(self.root / "yunnan.env", "MYSQL_PASSWORD=x\n")
        release = release_tool.host_path(str(self.root / "release"))
        payload = self.rendered(release, release_tool.host_path(str(self.root)))
        for volume in payload["services"]["backend"]["volumes"]:
            if volume["target"].endswith("model.pth"):
                volume["read_only"] = False
        code, output = self.check(payload)
        self.assertEqual(1, code)
        self.assertIn("读写属性变了", output)


@unittest.skipUnless(shutil.which("git"), "没有 git 命令")
class ManifestFlowTest(TempDirTestCase):
    """一条迷你仓库走完整个流程：归类检查 → 导出 → 建挂载点 → 记账 → 核对 → 动过之后被抓。"""

    LOCKED = {"": {"name": "root"}, "node_modules/a": {"version": "1.0.0"}}
    WEIGHTS = "backend/model/mmseg_config/model.pth"

    def setUp(self):
        super().setUp()
        self.repo, self.assets, self.release = self.root / "repo", self.root / "assets", self.root / "release"
        files = {
            "backend/app.py": "print('app')\n", "docker/start.sh": "echo start\n", "config.yaml": CONFIG.decode(),
            "frontend/src/main.js": "console.log(1)\n", "frontend/package.json": '{"name":"x","private":true}\n',
            "frontend/package-lock.json": json.dumps({"lockfileVersion": 3, "packages": self.LOCKED}),
            "miner/server.js": "// server\n", "miner/routes/a.js": "// a\n", "miner/services/b.js": "// b\n",
            "miner/src/c.js": "// c\n", "miner/vite.config.js": "// vite\n",
        }
        for relative, content in files.items():
            write(self.repo / relative, content)
        write(self.repo / "frontend" / "node_modules" / ".package-lock.json", json.dumps({"packages": self.LOCKED}))
        write(self.repo / "frontend" / "node_modules" / "a" / "index.js", "// a\n")
        write(self.repo / "backend" / "__pycache__" / "app.cpython-310.pyc", "bytes")
        write(self.repo / "backend" / ".flaskenv", "SECRET=x\n")
        write(self.repo / ".gitignore", "__pycache__/\n*.pyc\nbackend/.flaskenv\n")
        for relative, kind, _ in release_tool.ASSETS:
            if kind == "file":
                write(self.assets / relative, "weights of " + relative)
            else:
                write(self.assets / relative / "part.bin", "weights")
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init")

    def git(self, *arguments):
        return subprocess.run(["git", "-C", str(self.repo)] + list(arguments), check=True, capture_output=True)

    def tool(self, handler, **arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = handler(types.SimpleNamespace(**arguments))
        return code, output.getvalue()

    def export(self):
        """相当于 git archive 之后解压：只带已跟踪的文件。"""
        for relative in filter(None, self.git("ls-files", "-z").stdout.decode("utf-8").split("\0")):
            write(self.release / relative, read(self.repo / relative))

    def manifest(self):
        return self.tool(release_tool.cmd_manifest, release=str(self.release), commit="HEAD", repo=str(self.repo), name="test-release",
                         data_dir=str(self.root), asset_root=str(self.assets), asset_cache=str(self.root / "cache.json"))

    def check(self):
        return self.tool(release_tool.cmd_check, release=str(self.release), asset_root=str(self.assets))

    def flow(self):
        code, output = self.tool(release_tool.cmd_untracked_check, repo=str(self.repo), asset_root=str(self.assets))
        self.assertEqual(0, code, output)
        self.export()
        self.tool(release_tool.cmd_placeholders, release=str(self.release))
        code, output = self.manifest()
        self.assertEqual(0, code, output)
        return self.check()

    def test_full_flow_passes_and_records_everything(self):
        code, output = self.flow()
        self.assertEqual(0, code, output)
        manifest = json.loads(read(self.release / "RELEASE.json"))
        self.assertEqual(sorted(release_tool.TREES), sorted(manifest["trees"]))
        self.assertEqual(sorted(release_tool.ASSET_PATHS), sorted(manifest["assets"]))
        weights = manifest["assets"][self.WEIGHTS]
        self.assertEqual(hashlib.sha256(read(self.assets / self.WEIGHTS)).hexdigest(), weights["sha256"])
        self.assertEqual(len("weights of " + self.WEIGHTS), weights["bytes"])
        self.assertEqual("init", manifest["commit_subject"])

    def test_an_unusable_asset_directory_is_reported_not_raised(self):
        """资产目录被指到一个只剩空占位目录的位置：核对报出这一项并以非零退出，不带调用栈崩掉。"""
        code, output = self.flow()
        self.assertEqual(0, code, output)
        broken = self.root / "broken-assets"
        for relative, kind, _ in release_tool.ASSETS:
            if kind == "file":
                write(broken / relative, "weights")
            elif relative.endswith("dinov3_swinV1"):
                (broken / relative).mkdir(parents=True)      # 只有这一个目录是空的
            else:
                write(broken / relative / "part.bin", "weights")
        code, output = self.tool(release_tool.cmd_check, release=str(self.release), asset_root=str(broken))
        self.assertEqual(1, code, output)
        self.assertIn("不符: 运行时资产目录读不了", output)
        self.assertIn("空目录", output)

    def test_tampered_code_is_caught(self):
        self.flow()
        write(self.release / "backend" / "app.py", "print('tampered')\n")
        code, output = self.check()
        self.assertEqual(1, code)
        self.assertIn("backend 与清单不符", output)

    def test_changed_or_missing_asset_is_caught(self):
        self.flow()
        write(self.assets / self.WEIGHTS, "other weights")
        code, output = self.check()
        self.assertEqual(1, code)
        self.assertIn("资产 " + self.WEIGHTS + " 与清单不符", output)
        (self.assets / "backend" / "model" / "semantic_segmentation").rename(self.root / "moved-away")
        code, output = self.check()
        self.assertEqual(1, code)
        self.assertIn("资产目录里没有 backend/model/semantic_segmentation", output)

    def test_changed_asset_is_rehashed_despite_the_cache(self):
        self.flow()
        write(self.assets / self.WEIGHTS, "weights of " + self.WEIGHTS + " plus one")
        self.manifest()
        manifest = json.loads(read(self.release / "RELEASE.json"))
        self.assertEqual(hashlib.sha256(read(self.assets / self.WEIGHTS)).hexdigest(), manifest["assets"][self.WEIGHTS]["sha256"])

    def test_filled_mountpoint_blocks_the_manifest(self):
        self.export()
        self.tool(release_tool.cmd_placeholders, release=str(self.release))
        write(self.release / "backend" / "static" / "stray.bin", "x")
        with self.assertRaises(SystemExit):
            self.manifest()

    def test_capabilities_come_from_the_release_itself(self):
        # 这个迷你仓库没有几何接口、也不走后端校验：回滚到这样的发布，核对就应当期望"运行时也没有"
        self.export()
        self.assertEqual((False, False), release_tool.release_capabilities(self.root / "release"))
        write(self.root / "release" / "backend" / "applications" / "api" / "project.py",
              '# @project_api.post("/<int:project_id>/geometry/validate")\n')
        write(self.root / "release" / "frontend" / "src" / "api" / "results.js", "fetch('/geometry/validate')\n")
        self.assertEqual((True, True), release_tool.release_capabilities(self.root / "release"))

    def test_unclassified_untracked_file_blocks_the_release(self):
        write(self.repo / ".gitignore", read(self.repo / ".gitignore") + b"backend/mystery.bin\n")
        write(self.repo / "backend" / "mystery.bin", "?")
        code, output = self.tool(release_tool.cmd_untracked_check, repo=str(self.repo), asset_root=str(self.assets))
        self.assertEqual(1, code)
        self.assertIn("backend/mystery.bin", output)

    def test_assets_missing_from_the_asset_root_block_the_release(self):
        (self.assets / "backend" / "model" / "object_detection" / "part.bin").unlink()
        code, output = self.tool(release_tool.cmd_untracked_check, repo=str(self.repo), asset_root=str(self.assets))
        self.assertEqual(1, code)
        self.assertIn("backend/model/object_detection", output)


if __name__ == "__main__":
    unittest.main()
