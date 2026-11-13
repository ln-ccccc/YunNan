# -*- coding: utf-8 -*-
"""分片上传并发与恢复（2026-11-14 审查问题12+13）。

问题12：多 Gunicorn worker / 双标签页并发 complete——done 判定到写完成态
整体持会话文件锁串行化，第二者在锁内复用上次结果，不重复合并、不重复入库；
write_chunk 持锁后迟到分块不得再写入已完成会话。
问题13：complete 成功但响应丢失（网络断/页面刷新）后重开页面 init——
先查会话完成态带回 done+result，不再被 incoming 同名 Conflict 挡死。
"""
import hashlib
import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.common.utils import upload as upload_curd
from applications.common.utils import upload_sessions
from applications.models import Photo

from test_file_upload_chunks import ChunkedUploadBase, _sha256
from test_project_api import TestProjectAPI


class TestUploadSessionLock(ChunkedUploadBase):
    """通用通道（/api/file/upload/*）的锁语义与并发 complete。"""

    def test_session_lock_serializes_concurrent_holders(self):
        self.login_as_admin()
        self._init()

        events = []
        barrier = threading.Barrier(2)

        def worker(name):
            with self.app.test_request_context():
                barrier.wait()
                with upload_sessions.session_lock(self.key, timeout=10):
                    events.append(f"{name}-acquired")
                    time.sleep(0.2)
                    events.append(f"{name}-released")

        threads = [
            threading.Thread(target=worker, args=(name,))
            for name in ("first", "second")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # 两个持有者的 acquired/released 不交错：第二个必等第一个完全释放
        self.assertEqual(len(events), 4)
        self.assertTrue(events[1].endswith("-released"), events)
        self.assertTrue(events[2].endswith("-acquired"), events)

    def test_write_chunk_rejected_after_session_done(self):
        self.login_as_admin()
        init = self._init().get_json()["data"]
        for index in range(init["total_chunks"]):
            block = self.payload[
                index * init["chunk_size"]:(index + 1) * init["chunk_size"]
            ]
            response = self._put_chunk(index, block, sha=_sha256(block))
            self.assertEqual(response.status_code, 200)

        response = self._complete()
        self.assertTrue(response.get_json()["success"])

        # complete 已成功的会话：迟到/重放的分块必须被拒，而不是静默重写
        block = self.payload[:init["chunk_size"]]
        response = self._put_chunk(0, block, sha=_sha256(block))
        self.assertEqual(response.status_code, 400)
        self.assertIn("上传已完成", response.get_json()["msg"])

    def test_concurrent_complete_registers_single_photo(self):
        self.login_as_admin()
        init = self._init().get_json()["data"]
        for index in range(init["total_chunks"]):
            block = self.payload[
                index * init["chunk_size"]:(index + 1) * init["chunk_size"]
            ]
            response = self._put_chunk(index, block, sha=_sha256(block))
            self.assertEqual(response.status_code, 200)

        original = upload_curd.upload_one_from_path
        calls = {"count": 0, "guard": threading.Lock()}

        def slow_upload(*args, **kwargs):
            with calls["guard"]:
                calls["count"] += 1
            time.sleep(0.5)
            return original(*args, **kwargs)

        barrier = threading.Barrier(2)
        responses = [None, None]

        def completer(slot):
            with self.app.test_client() as client:
                login = client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "Secret123!"},
                )
                assert login.status_code == 200
                barrier.wait()
                responses[slot] = client.post(
                    f"/api/file/upload/complete/{self.key}",
                    json={"type": "地物分类", "isSlice": False, "keepRawTiff": True},
                )

        threads = [
            threading.Thread(target=completer, args=(slot,)) for slot in (0, 1)
        ]
        with patch(
            "applications.common.utils.upload.upload_one_from_path",
            side_effect=slow_upload,
        ):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        # 两个并发 complete 都成功且返回同一份结果，但只入库一张 Photo
        self.assertEqual(responses[0].status_code, 200)
        self.assertEqual(responses[1].status_code, 200)
        data_first = responses[0].get_json()["data"]
        data_second = responses[1].get_json()["data"]
        self.assertEqual(data_first, data_second)
        self.assertEqual(calls["count"], 1)
        self.assertEqual(Photo.query.count(), 1)


class TestBasemapUploadRecoveryAndConcurrency(TestProjectAPI):
    """底图分片通道（/spatial/basemap-upload/*）的并发 complete 与 init 恢复。"""

    def _prepare_session(self, project_id, filename="recover.tif"):
        payload_bytes = b"y" * (1024 * 1024)  # 恰好 1MB = 单块
        upload_key = hashlib.sha256(
            f"pr11:{filename}:{time.time()}".encode()
        ).hexdigest()
        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemap-upload/init",
            json={
                "upload_key": upload_key,
                "filename": filename,
                "total_size": len(payload_bytes),
                "mime": "application/octet-stream",
                "chunk_size": 1024 * 1024,
            },
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        session_id = self._json(response)["data"]["session_id"]
        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemap-upload/chunk/{session_id}/0",
            data=payload_bytes,
            headers={
                "Content-Type": "application/octet-stream",
                "X-Chunk-Sha256": hashlib.sha256(payload_bytes).hexdigest(),
            },
        )
        self.assertEqual(response.status_code, 200)
        return session_id, upload_key, payload_bytes

    def _init_payload(self, project_id, upload_key, filename, size):
        return self.client.post(
            f"/api/projects/{project_id}/spatial/basemap-upload/init",
            json={
                "upload_key": upload_key,
                "filename": filename,
                "total_size": size,
                "mime": "application/octet-stream",
                "chunk_size": 1024 * 1024,
            },
        )

    def test_init_recovers_done_session_after_lost_response(self):
        # 问题13：complete 成功但响应丢失（网络断/页面刷新）后重开页面 init——
        # 不得被 incoming 同名 Conflict（409）挡死，应带回 done+上次结果
        self.login_as_admin()
        project_id = self._create_project("底图恢复项目")
        session_id, upload_key, payload_bytes = self._prepare_session(project_id)

        response = self.client.post(
            f"/api/projects/{project_id}/spatial/basemap-upload/complete/{session_id}",
            json={"filename": "recover.tif"},
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))

        # 同 upload_key + 同文件名 re-init：恢复入口，200 带回 done+result
        response = self._init_payload(
            project_id, upload_key, "recover.tif", len(payload_bytes)
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        state = self._json(response)["data"]
        self.assertTrue(state.get("done"))
        self.assertEqual(
            state["result"][0]["candidate"], "incoming/recover.tif"
        )

        # 同 upload_key 但换文件名：客户端不一致，409 而非静默恢复
        # （msg 经 _spatial_failure_response 对 HTTPException 的既有收口为
        # 通用文案，与 complete 侧改名重试 409 同款，只断言状态码）
        response = self._init_payload(
            project_id, upload_key, "renamed.tif", len(payload_bytes)
        )
        self.assertEqual(response.status_code, 409)

    def test_concurrent_basemap_complete_assembles_once(self):
        # 问题12：双 worker 并发 complete 同一会话——共享 staging 按文件名命名，
        # 并发交错会互相截断；持锁串行化后第二者复用结果，assemble 恰好一次
        self.login_as_admin()
        project_id = self._create_project("底图并发项目")
        session_id, _, payload_bytes = self._prepare_session(
            project_id, "concurrent.tif"
        )

        original_assemble = upload_sessions.assemble_session
        calls = {"count": 0, "guard": threading.Lock()}

        def slow_assemble(*args, **kwargs):
            with calls["guard"]:
                calls["count"] += 1
            time.sleep(0.5)
            return original_assemble(*args, **kwargs)

        barrier = threading.Barrier(2)
        responses = [None, None]

        def completer(slot):
            with self.app.test_client() as client:
                login = client.post(
                    "/api/auth/login",
                    json={"username": "admin", "password": "Secret123!"},
                )
                assert login.status_code == 200
                barrier.wait()
                responses[slot] = client.post(
                    f"/api/projects/{project_id}/spatial/basemap-upload/complete/{session_id}",
                    json={"filename": "concurrent.tif"},
                )

        threads = [
            threading.Thread(target=completer, args=(slot,)) for slot in (0, 1)
        ]
        with patch(
            "applications.common.utils.upload_sessions.assemble_session",
            side_effect=slow_assemble,
        ):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

        self.assertEqual(responses[0].status_code, 200)
        self.assertEqual(responses[1].status_code, 200)
        self.assertEqual(self._json(responses[0])["data"], self._json(responses[1])["data"])
        self.assertEqual(calls["count"], 1)
        final_file = self.storage_root / "incoming" / "concurrent.tif"
        self.assertTrue(final_file.is_file())
        self.assertEqual(final_file.read_bytes(), payload_bytes)


if __name__ == "__main__":
    unittest.main()
