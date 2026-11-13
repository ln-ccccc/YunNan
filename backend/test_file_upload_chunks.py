# -*- coding: utf-8 -*-
"""分片续传（100GB 级影像上传，2026-09-22）的 API 生命周期测试。

覆盖：init 幂等与冲突、chunk 尺寸/校验和/幂等、complete 合并校验与
upload_one_from_path 同源处理（photo 记录/响应形状与单发通道一致）、
总量上限、会话标识校验、鉴权、暂存清理。
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from applications.common.utils import upload_sessions
from applications.common.utils.tiff_processor import (
    MAX_UPLOAD_TIFF_SIZE_MB,
    UPLOAD_SESSION_MAX_TOTAL_MB,
)
from applications.models import Photo
from test_large_tiff_upload import LargeTiffUploadBase

CHUNK_MB = 1


def _make_tif(path: Path, width=1024, height=900) -> None:
    rng = np.random.default_rng(42)
    data = rng.integers(20, 235, size=(3, height, width), dtype=np.uint8)
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": 3,
        "dtype": "uint8",
        "crs": "EPSG:4326",
        "transform": rasterio.transform.from_origin(100.0, 40.0, 0.001, 0.001),
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class ChunkedUploadBase(LargeTiffUploadBase):
    def setUp(self):
        super().setUp()
        self.work = Path(tempfile.mkdtemp(prefix="chunked-upload-test-"))
        self.tif_path = self.work / "scene.tif"
        _make_tif(self.tif_path)
        self.payload = self.tif_path.read_bytes()
        self.key = "a" * 40

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)
        super().tearDown()

    def _init(self, key=None, size=None, chunk_mb=CHUNK_MB, filename="scene.tif"):
        return self.client.post(
            "/api/file/upload/init",
            json={
                "upload_key": self.key if key is None else key,
                "filename": filename,
                "total_size": size if size is not None else len(self.payload),
                "mime": "image/tiff",
                "chunk_size": chunk_mb * 1024 * 1024,
            },
        )

    def _put_chunk(self, index, payload, key=None, sha=None):
        headers = {"Content-Type": "application/octet-stream"}
        if sha is not None:
            headers["X-Chunk-Sha256"] = sha
        return self.client.post(
            f"/api/file/upload/chunk/{key or self.key}/{index}", data=payload, headers=headers
        )

    def _complete(self, key=None, body=None):
        return self.client.post(
            f"/api/file/upload/complete/{key or self.key}",
            json=body or {"type": "地物分类", "isSlice": True, "keepRawTiff": True},
        )


class TestChunkedUploadLifecycle(ChunkedUploadBase):
    def test_full_lifecycle_matches_single_post_contract(self):
        self.login_as_admin()
        init = self._init().get_json()["data"]
        self.assertGreaterEqual(init["total_chunks"], 2)
        self.assertEqual(init["received"], [])

        chunk_size = init["chunk_size"]
        for index in range(init["total_chunks"]):
            block = self.payload[index * chunk_size:(index + 1) * chunk_size]
            resp = self._put_chunk(index, block, sha=_sha256(block)).get_json()
            self.assertEqual(resp["data"]["bytes"], len(block))

        complete = self._complete()
        body = complete.get_json()
        self.assertTrue(body["success"], body)
        data = body["data"]
        self.assertEqual(len(data), 1)
        item = data[0]
        self.assertEqual(item["filename"], "scene.tif")
        self.assertTrue(isinstance(item["photo_id"], int))
        self.assertTrue(item["src"].startswith("/_uploads/photos/"))
        # keepRawTiff 快速路径：原始 tif 保留且 raw_tiff_path 指向已落盘文件
        self.assertTrue(item["raw_tiff_path"])
        self.assertTrue(Path(item["raw_tiff_path"]).is_file())
        # 合并产物与源逐字节一致
        self.assertEqual(Path(item["raw_tiff_path"]).read_bytes(), self.payload)
        # photo 记录入库；会话保留为完成态（done 幂等，过期由 sweep 清理）
        self.assertIsNotNone(Photo.query.filter_by(id=item["photo_id"]).first())
        staging = Path(self.upload_tmp) / ".chunks" / self.key
        self.assertTrue(staging.exists())

    def test_init_is_idempotent_and_reports_received_for_resume(self):
        self.login_as_admin()
        init = self._init().get_json()["data"]
        chunk_size = init["chunk_size"]
        self._put_chunk(0, self.payload[:chunk_size], sha=_sha256(self.payload[:chunk_size]))
        # 模拟页面重开/失败重试：同 key 重新 init → 已收分块可跳过
        again = self._init().get_json()["data"]
        self.assertEqual(again["session_id"], self.key)
        self.assertEqual(again["received"], [0])
        # 分块幂等：重传覆盖不报错
        self.assertTrue(self._put_chunk(0, self.payload[:chunk_size]).get_json()["success"])

    def test_upload_requires_login(self):
        resp = self._init()
        self.assertEqual(resp.status_code, 401)

    def test_invalid_session_key_rejected(self):
        self.login_as_admin()
        for bad in ("../evil", "ZZZ", "a" * 8, "a" * 65, ""):
            resp = self._init(key=bad)
            self.assertEqual(resp.status_code, 400, bad)

    def test_key_conflict_when_file_changed(self):
        self.login_as_admin()
        self._init()
        resp = self._init(size=len(self.payload) + 1)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("不一致", resp.get_json()["msg"])

    def test_total_size_cap_enforced(self):
        self.login_as_admin()
        # init_session 从模块命名空间读取常量，patch 模块级名字即可生效
        with patch("applications.common.utils.upload_sessions.UPLOAD_SESSION_MAX_TOTAL_MB", 0):
            resp = self._init()
        self.assertEqual(resp.status_code, 400)
        self.assertIn("分片上传上限", resp.get_json()["msg"])

    def test_chunk_size_bounds_enforced(self):
        self.login_as_admin()
        resp = self._init(chunk_mb=0)
        self.assertEqual(resp.status_code, 400)
        resp = self._init(chunk_mb=513)
        self.assertEqual(resp.status_code, 400)

    def test_chunk_size_mismatch_and_sha_mismatch_rejected(self):
        self.login_as_admin()
        init = self._init().get_json()["data"]
        chunk_size = init["chunk_size"]
        oversize = self._put_chunk(0, self.payload[:chunk_size] + b"extra")
        self.assertEqual(oversize.status_code, 400)
        short = self._put_chunk(0, self.payload[:chunk_size - 1])
        self.assertEqual(short.status_code, 400)
        bad_sha = self._put_chunk(0, self.payload[:chunk_size], sha="0" * 64)
        self.assertEqual(bad_sha.status_code, 400)
        self.assertIn("校验和", bad_sha.get_json()["msg"])
        ok = self._put_chunk(0, self.payload[:chunk_size], sha=_sha256(self.payload[:chunk_size]))
        self.assertTrue(ok.get_json()["success"])

    def test_complete_with_missing_chunk_rejected(self):
        self.login_as_admin()
        init = self._init().get_json()["data"]
        chunk_size = init["chunk_size"]
        self._put_chunk(0, self.payload[:chunk_size])
        resp = self._complete()
        self.assertEqual(resp.status_code, 400)
        self.assertIn("缺失", resp.get_json()["msg"])
        # 失败后会话保留（可续传）；上传根目录未产生半成品最终文件
        staging = Path(self.upload_tmp) / ".chunks" / self.key
        self.assertTrue(staging.exists())
        self.assertEqual(list(Path(self.upload_tmp).glob("*.tif")), [])

    def test_chunk_out_of_range_and_unknown_session_rejected(self):
        self.login_as_admin()
        init = self._init().get_json()["data"]
        resp = self._put_chunk(init["total_chunks"], b"x" * 16)
        self.assertEqual(resp.status_code, 400)
        unknown = self._put_chunk(0, b"x" * 16, key="b" * 40)
        self.assertEqual(unknown.status_code, 400)
        self.assertIn("不存在", unknown.get_json()["msg"])

    def test_non_tif_file_via_chunked_channel(self):
        self.login_as_admin()
        payload = b"\x89PNG\r\n\x1a\n" + os.urandom(64 * 1024)
        path = self.work / "preview.png"
        path.write_bytes(payload)
        self.payload = payload
        init = self._init(filename="preview.png").get_json()["data"]
        chunk_size = init["chunk_size"]
        for index in range(init["total_chunks"]):
            self._put_chunk(index, payload[index * chunk_size:(index + 1) * chunk_size])
        complete = self._complete(body={"type": "地物分类", "isSlice": False, "keepRawTiff": False})
        body = complete.get_json()
        self.assertTrue(body["success"], body)
        self.assertIsNone(body["data"][0]["raw_tiff_path"])

    def test_caps_parity_with_frontend_channel_design(self):
        # 单发通道 1GB（R5 下调，werkzeug 全量内存缓冲）；分片通道 100GB
        self.assertEqual(MAX_UPLOAD_TIFF_SIZE_MB, 1024)
        self.assertEqual(UPLOAD_SESSION_MAX_TOTAL_MB, 102400)

    def _complete_payload(self):
        return {"type": "地物分类", "isSlice": True, "keepRawTiff": True}

    def _upload_all_chunks(self, init_data):
        chunk_size = init_data["chunk_size"]
        for index in range(init_data["total_chunks"]):
            block = self.payload[index * chunk_size:(index + 1) * chunk_size]
            resp = self._put_chunk(index, block, sha=_sha256(block))
            self.assertEqual(resp.status_code, 200)

    def test_complete_is_idempotent_after_success(self):
        """done 幂等（2026-09-22 审查 P1）：响应丢失/批次重试/双标签并发 complete
        均返回同一结果，不重新合并、不重复入库。"""
        self.login_as_admin()
        from applications.models import Photo

        init = self._init().get_json()["data"]
        self._upload_all_chunks(init)
        first = self._complete().get_json()["data"]
        photo_count_after_first = Photo.query.count()

        # 再次 init：携带 done+result（前端据此跳过重传）
        again = self._init().get_json()["data"]
        self.assertTrue(again.get("done"))
        self.assertEqual(again.get("result"), first)

        # 再次 complete：原样返回，不重复入库
        second = self._complete().get_json()["data"]
        self.assertEqual(second, first)
        self.assertEqual(Photo.query.count(), photo_count_after_first)

    def test_session_owner_enforced_on_chunk_write(self):
        """属主校验：upload_key 客户端可算，他人拿到 id 也不得写块（审查 F5）。"""
        import io as _io

        from applications.common.utils import upload_sessions
        from applications.common.utils.tiff_processor import UPLOAD_SESSION_MAX_TOTAL_MB  # noqa: F401

        state = upload_sessions.init_session(
            upload_key="c" * 40,
            filename="owner.tif",
            total_size=len(self.payload),
            mime="image/tiff",
            chunk_size=CHUNK_MB * 1024 * 1024,
            owner="admin",
        )
        with self.assertRaises(upload_sessions.UploadSessionError) as ctx:
            upload_sessions.write_chunk(
                state["session_id"], 0, _io.BytesIO(self.payload[:CHUNK_MB * 1024 * 1024]),
                owner="someone-else",
            )
        self.assertIn("无权", str(ctx.exception))

    def test_api_level_owner_isolation_between_logged_in_sessions(self):
        """API 级属主隔离（2026-10-24 审查 P2-9）：file.py 曾读 session['username']
        （登录实际写 admin_username），owner 恒为 None——直调模块层测不出该断键
        （上方用例的盲区），必须走 HTTP 双登录会话：A 建、B 写块须 400，A 续传不受影响。"""
        from werkzeug.security import generate_password_hash

        from applications.extensions import db
        from applications.models import AdminUser

        self.login_as_admin()
        init = self._init().get_json()["data"]

        db.session.add(AdminUser(
            username="operator",
            password_hash=generate_password_hash("Secret123!"),
            is_active=True,
        ))
        db.session.commit()
        other = self.app.test_client()
        login = other.post("/api/auth/login", json={"username": "operator", "password": "Secret123!"})
        self.assertEqual(login.status_code, 200)

        block = self.payload[:init["chunk_size"]]
        hijack = other.post(
            f"/api/file/upload/chunk/{self.key}/0",
            data=block,
            headers={"Content-Type": "application/octet-stream"},
        )
        self.assertEqual(hijack.status_code, 400, hijack.get_data(as_text=True))
        self.assertIn("无权", hijack.get_json()["msg"])

        own = self._put_chunk(0, block)
        self.assertEqual(own.status_code, 200)

    def test_session_id_with_trailing_newline_rejected(self):
        """fullmatch：原 `$` 放行尾部 \\n（URL %0A 可达），可建含换行的目录名。"""
        self.login_as_admin()
        resp = self._init(key="d" * 40 + "\n")
        self.assertEqual(resp.status_code, 400)

    def test_chunks_staging_not_reachable_via_static_or_uploads_routes(self):
        """分片暂存不属公开产物：/static 与 /_uploads 对 .chunks 一律 404（审查 P3）。"""
        self.login_as_admin()
        self._init()
        for path in (
            "/static/upload/.chunks/%s/session.json" % self.key,
            "/_uploads/photos/.chunks/%s/chunk_0.bin" % self.key,
        ):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 404, path)


class TestChunkedExtensionWhitelistAndCompleteOwner(ChunkedUploadBase):
    """分片通道扩展名白名单与 complete 属主校验（2026-11-05 迁移审查 P1-1/P2-12）。

    江西同源教训（5.1+5.6 叠加）：分片通道此前只黑名单 ENVI、无正向白名单，
    任意后缀（.svg/.html）可经 init/chunk/complete 落盘并被 /_uploads 按扩展名
    推断的 Content-Type 内联提供（认证内存储型 XSS）；complete 亦缺属主校验。
    """

    def test_init_rejects_non_whitelisted_extension(self):
        self.login_as_admin()
        resp = self._init(filename="evil.svg", size=1024)
        body = resp.get_json()
        self.assertEqual(resp.status_code, 400, resp.get_data(as_text=True))
        self.assertFalse(body.get("success"), body)
        self.assertIn("分片续传仅支持", str(body.get("msg")))

    def test_init_rejects_envi_with_single_post_guidance(self):
        self.login_as_admin()
        resp = self._init(filename="scene.dat", size=1024)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("单次上传", resp.get_json()["msg"])

    def test_complete_rejects_svg_session_created_at_module_layer(self):
        """complete 侧兜底：绕过 API init（模块层直建 .svg 会话）同样被拒。"""
        self.login_as_admin()
        state = upload_sessions.init_session(
            upload_key="d" * 40,
            filename="evil.svg",
            total_size=CHUNK_MB * 1024 * 1024,
            mime="image/svg+xml",
            chunk_size=CHUNK_MB * 1024 * 1024,
            owner="admin",
        )
        self._put_chunk(0, b"<svg><script>alert(1)</script></svg>", key=state["session_id"])
        resp = self._complete(key=state["session_id"])
        self.assertEqual(resp.status_code, 400, resp.get_data(as_text=True))
        self.assertIn("仅支持", resp.get_json()["msg"])
        # 上传根不得残留任何 .svg 落盘产物
        self.assertFalse(
            [p for p in os.listdir(self.upload_tmp) if p.lower().endswith(".svg")],
            os.listdir(self.upload_tmp),
        )

    def test_complete_on_unknown_session_returns_400_not_500(self):
        """属主校验前置后（P2-12 修复），complete 对不存在会话必须仍走
        UploadSessionError → 400，不得因校验顺序变化变成 500。"""
        self.login_as_admin()
        resp = self._complete(key="e" * 40)
        self.assertEqual(resp.status_code, 400, resp.get_data(as_text=True))
        self.assertFalse(resp.get_json().get("success"))

    def test_complete_rejects_hijack_by_other_logged_in_session(self):
        """complete 属主校验（P2-12）：B 会话不得抢先完成收割 A 的上传会话。"""
        from werkzeug.security import generate_password_hash

        from applications.extensions import db
        from applications.models import AdminUser

        self.login_as_admin()
        init = self._init().get_json()["data"]
        self._put_chunk(0, self.payload[:init["chunk_size"]])

        db.session.add(AdminUser(
            username="operator2",
            password_hash=generate_password_hash("Secret123!"),
            is_active=True,
        ))
        db.session.commit()
        other = self.app.test_client()
        login = other.post("/api/auth/login", json={"username": "operator2", "password": "Secret123!"})
        self.assertEqual(login.status_code, 200)

        hijack = other.post(
            f"/api/file/upload/complete/{self.key}",
            json={"type": "地物分类", "isSlice": True, "keepRawTiff": True},
        )
        self.assertEqual(hijack.status_code, 400, hijack.get_data(as_text=True))
        self.assertIn("无权", hijack.get_json()["msg"])


class TestCompleteConcurrencyGuard(ChunkedUploadBase):
    """并发 complete 互斥（2026-11-14 审查问题33）。

    done 检查与 mark_session_done 之间的 check-then-act 竞态：双标签页并发
    complete 时两个请求都能通过 done 快路径检查，各自合并、各自调用
    upload_one_from_path → 双份照片入库（生产 gunicorn 2 worker × 4 thread
    为真多进程竞态，测试以双线程 + barrier 加宽窗口代表同一 interleaving）。
    修复后：O_EXCL 哨兵锁跨进程互斥，后到方等待先到方完成态后原样返回。
    """

    def _upload_all_chunks(self, init_data):
        chunk_size = init_data["chunk_size"]
        for index in range(init_data["total_chunks"]):
            block = self.payload[index * chunk_size:(index + 1) * chunk_size]
            resp = self._put_chunk(index, block, sha=_sha256(block))
            self.assertEqual(resp.status_code, 200)

    def test_concurrent_complete_ingests_once(self):
        import threading
        import time as _time

        from applications.api import file as file_api
        from applications.extensions import db
        from applications.models import Photo

        self.login_as_admin()
        init = self._init().get_json()["data"]
        self._upload_all_chunks(init)

        second = self.app.test_client()
        login = second.post(
            "/api/auth/login", json={"username": "admin", "password": "Secret123!"}
        )
        self.assertEqual(login.status_code, 200)

        barrier = threading.Barrier(2)
        original_ingest = file_api.upload_curd.upload_one_from_path

        def slow_ingest(*args, **kwargs):
            # 入库前延迟：先到方的完成态写入被推迟，保证后到方必然先越过
            # done 快路径检查（复现窗口；加锁后后到方根本不会走到这里）
            _time.sleep(0.5)
            return original_ingest(*args, **kwargs)

        results = {}

        def run_complete(client, label):
            barrier.wait(timeout=10)
            response = client.post(
                f"/api/file/upload/complete/{self.key}",
                json={"type": "地物分类", "isSlice": True, "keepRawTiff": True},
            )
            results[label] = (response.status_code, response.get_json(silent=True))

        with patch.object(
            file_api.upload_curd, "upload_one_from_path", side_effect=slow_ingest
        ):
            threads = [
                threading.Thread(target=run_complete, args=(self.client, "a")),
                threading.Thread(target=run_complete, args=(second, "b")),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
        self.assertFalse(any(thread.is_alive() for thread in threads), "complete 未在超时内返回")

        db.session.expire_all()
        self.assertEqual(Photo.query.count(), 1, "并发 complete 只允许一次入库")
        for label in ("a", "b"):
            status_code, body = results[label]
            self.assertEqual(status_code, 200, (label, body))
            self.assertTrue(body["success"], (label, body))
        self.assertEqual(results["a"][1]["data"][0]["photo_id"], results["b"][1]["data"][0]["photo_id"])


if __name__ == "__main__":
    unittest.main()
