#!/usr/bin/env python3
"""发布工具里"切换的失败安全"这一部分的测试：unpin（解除钉住）、jobs（限时查任务）、state（切换状态）。
只碰临时目录；jobs 用假的 docker（fake_docker.py），不碰真的 docker。

    python -m unittest discover -s deploy -p "test_release_tool_state.py"
"""
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import time
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import release_tool  # noqa: E402
from test_release_tool import TempDirTestCase, read, write  # noqa: E402


class UnpinTest(TempDirTestCase):
    """--rollback 回到"没钉住"的状态靠它：只删那两行，其余字节不动。"""

    def test_pin_then_unpin_restores_the_original_bytes(self):
        original = b"A=1\r\nMYSQL_PASSWORD='a b'\r\n"
        path = write(self.root / "a.env", original)
        with contextlib.redirect_stdout(io.StringIO()):
            release_tool.cmd_pin(types.SimpleNamespace(env_file=str(path), pairs=["YUNNAN_SOURCE_ROOT=D:/rel/a", "YUNNAN_GIT_SHA=abc1234"]))
        self.assertNotEqual(original, read(path))            # 钉住之后确实变了
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = release_tool.cmd_unpin(types.SimpleNamespace(env_file=str(path)))
        self.assertEqual(0, code)
        self.assertEqual(["YUNNAN_SOURCE_ROOT", "YUNNAN_GIT_SHA"], json.loads(output.getvalue())["removed"])
        self.assertEqual(original, read(path))               # 解除之后逐字节回到原样

    def test_only_our_lines_are_removed_even_with_export_prefix(self):
        path = write(self.root / "b.env", b"export A=1\nexport YUNNAN_SOURCE_ROOT=D:/x\n# YUNNAN_GIT_SHA=comment\nB=2\n")
        with contextlib.redirect_stdout(io.StringIO()):
            release_tool.cmd_unpin(types.SimpleNamespace(env_file=str(path)))
        self.assertEqual(b"export A=1\n# YUNNAN_GIT_SHA=comment\nB=2\n", read(path))

    def test_nothing_to_remove_writes_nothing(self):
        path = write(self.root / "c.env", b"A=1\n")
        before = os.stat(path).st_mtime_ns
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            release_tool.cmd_unpin(types.SimpleNamespace(env_file=str(path)))
        self.assertEqual({"removed": []}, json.loads(output.getvalue()))
        self.assertEqual(before, os.stat(path).st_mtime_ns)


class JobsTest(TempDirTestCase):
    """切换前查库：没有任务才能切；有任务拦下；查不到或查挂了也拦下，而且有上限。"""

    def jobs(self, mode, timeout=3):
        environment = dict(os.environ, STUB_MODE=mode, PYTHONUTF8="1",
                           RELEASE_TOOL_DOCKER=shlex.join([sys.executable, str(HERE / "fake_docker.py")]))
        started = time.time()
        process = subprocess.run([sys.executable, str(HERE / "release_tool.py"), "jobs", "--project", "p", "--timeout", str(timeout)],
                                env=environment, capture_output=True)
        return process.returncode, process.stdout.decode("utf-8", "replace"), time.time() - started

    def test_no_jobs_can_switch(self):
        code, output, _ = self.jobs("zero")
        self.assertEqual(0, code, output)
        self.assertEqual({"inference": 0, "spatial": 0}, json.loads(output))

    def test_running_jobs_block_and_name_the_escape_hatch(self):
        code, output, _ = self.jobs("one")
        self.assertEqual(1, code)
        self.assertIn("--allow-running-jobs", output)

    def test_an_unreadable_answer_blocks(self):
        code, output, _ = self.jobs("garbage")
        self.assertEqual(2, code)
        self.assertIn("查不到", output)

    def test_a_hanging_query_is_bounded(self):
        code, output, seconds = self.jobs("hang", timeout=2)
        self.assertEqual(2, code)
        self.assertIn("超时", output)
        self.assertLess(seconds, 20)


class SwitchStateTest(TempDirTestCase):
    """--rollback 不带目录时靠它决定回到哪一份。"""

    def state(self, action, path, patch="{}"):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = release_tool.cmd_state(types.SimpleNamespace(action=action, path=str(path), patch=patch))
        return code, json.loads(output.getvalue())

    def test_show_on_a_missing_file_is_empty(self):
        code, shown = self.state("show", self.root / "releases" / ".switch-state.json")
        self.assertEqual((0, {}), (code, shown))

    def test_update_merges_and_keeps_the_other_keys(self):
        folder = self.root / "releases"
        folder.mkdir()
        path = folder / ".switch-state.json"
        self.state("update", path, '{"current": "D:/r/b", "last_attempt_status": "succeeded"}')
        _, shown = self.state("update", path, '{"previous": "D:/r/a"}')
        self.assertEqual(("D:/r/b", "D:/r/a", "succeeded"), (shown["current"], shown["previous"], shown["last_attempt_status"]))
        self.assertTrue(shown["updated_at"])
        self.assertEqual([".switch-state.json"], sorted(item.name for item in folder.iterdir()))   # 临时文件已改名，不留残片

    def test_null_previous_round_trips_as_none(self):
        path = self.root / ".switch-state.json"
        _, shown = self.state("update", path, '{"current": null, "previous": null}')
        self.assertEqual((None, None), (shown["current"], shown["previous"]))


if __name__ == "__main__":
    unittest.main()
