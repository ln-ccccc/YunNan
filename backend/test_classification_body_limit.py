"""保存与编辑期几何接口的请求体上限：超过 10 MiB 的请求体必须在读进内存之前被拒绝。

背景（2026-11-25 核查）：Flask 全局的 MAX_CONTENT_LENGTH 是 GB 级（给影像上传用的），而保存与三个
几何接口只收几十 KB 到几 MB 的 JSON。此前它们先把整个请求体读进内存，再由处理函数判断是否超过
10 MiB——登录用户发一个超大 JSON 就能占掉等量的内存。现在带上限读取，四个入口共用同一条规则。

夹具沿用 test_classification_result_api。
"""
import os
import sys
import unittest

sys.path.append(os.path.join(os.path.dirname(__file__), "."))

from test_classification_result_api import TestClassificationResultAPI

MIB = 1024 * 1024
ENDPOINTS = ("/geometry/validate", "/geometry/simplify", "/geometry/union", "/revisions")


class TestClassificationBodyLimit(unittest.TestCase):
    ROI = TestClassificationResultAPI.ROI
    setUp = TestClassificationResultAPI.setUp
    tearDown = TestClassificationResultAPI.tearDown
    _create_project_resources = TestClassificationResultAPI._create_project_resources
    _create_ready_result = TestClassificationResultAPI._create_ready_result
    _result_url = TestClassificationResultAPI._result_url

    def assertOversized(self, response):
        self.assertEqual(response.status_code, 422, response.data)
        payload = response.get_json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["data"]["details"], {"field": "body", "reason": "请求 JSON 超过 10 MiB"})
        return payload

    def test_declared_oversized_body_is_never_read(self):
        # 请求声明了 Content-Length 且超限：处理器一次底层读都不该做——
        # 把输入流换成一读就抛异常的流来证明（读了就是 500，下面的 422 断言会失败）
        class MustNotBeRead:
            def read(self, size=-1):
                raise AssertionError("声明了超限 Content-Length 的请求体不应被读取")

        for suffix in ENDPOINTS:
            with self.subTest(suffix=suffix):
                response = self.client.post(
                    self._result_url(suffix), data=b" " * (10 * MIB + 1), content_type="application/json",
                    environ_overrides={"wsgi.input": MustNotBeRead()},
                )
                self.assertOversized(response)

    def test_undeclared_body_is_read_only_up_to_the_limit(self):
        # 没声明 Content-Length（分块传输）：不知道有多大，只能边读边看——必须恰好读到上限加一字节就停
        class Endless:
            def __init__(self):
                self.served = 0

            def read(self, size=-1):
                if self.served >= 50 * MIB:
                    return b""
                size = min(size, 50 * MIB - self.served)
                self.served += size
                return b" " * size

        for suffix in ENDPOINTS:
            with self.subTest(suffix=suffix):
                stream = Endless()
                # gunicorn 处理分块请求时同样会声明 wsgi.input_terminated；
                # 不声明的话 Werkzeug 只试探读 128 字节就给一个空流，测不到真实的读取行为
                response = self.client.post(
                    self._result_url(suffix), content_type="application/json",
                    environ_overrides={"wsgi.input": stream, "CONTENT_LENGTH": "", "wsgi.input_terminated": True},
                )
                self.assertOversized(response)
                self.assertEqual(stream.served, 10 * MIB + 1)

    def test_body_within_the_limit_is_processed_normally(self):
        # 10 MiB 以内的请求体照常处理：内容不是合法几何，所以是几何层面的 422，不是请求体层面的
        padding = " " * MIB
        response = self.client.post(
            self._result_url("/geometry/validate"), data='{"geometries": ["' + padding + '"]}',
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 422, response.data)
        self.assertEqual(response.get_json()["data"]["details"]["field"], "geometry")

    def test_export_rejects_any_body_without_reading_it_all(self):
        # 导出接口此前为了判断「有没有请求体」把整个请求体读进来；现在声明了长度就一次都不读，
        # 没声明也最多读 1 个字节
        class Counting:
            def __init__(self):
                self.served = 0

            def read(self, size=-1):
                self.served += 1
                return b"x"

        stream = Counting()
        response = self.client.post(
            self._result_url("/export"), environ_overrides={"wsgi.input": stream, "CONTENT_LENGTH": str(50 * MIB)},
        )
        self.assertEqual(response.status_code, 422, response.data)
        self.assertEqual(response.get_json()["data"]["details"]["reason"], "导出仅使用服务器当前版本")
        self.assertEqual(stream.served, 0)

        stream = Counting()
        response = self.client.post(
            self._result_url("/export"),
            environ_overrides={"wsgi.input": stream, "CONTENT_LENGTH": "", "wsgi.input_terminated": True},
        )
        self.assertEqual(response.status_code, 422, response.data)
        self.assertEqual(stream.served, 1)


if __name__ == "__main__":
    unittest.main()
