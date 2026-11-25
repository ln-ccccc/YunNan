#!/usr/bin/env python3
"""测试用的假 docker：只给 release_tool.py 的 jobs 子命令用。按环境变量 STUB_MODE 应答，不碰真的 docker。

    zero     输出 "0\\t0"（没有任务）      one      输出 "1\\t0"（有一个推理任务在跑）
    garbage  输出一行数据库错误并返回 1     hang     睡 60 秒（模拟查询挂住）

通过环境变量 RELEASE_TOOL_DOCKER 指向本文件，例如：
    RELEASE_TOOL_DOCKER='"C:/Python/python.exe" "D:/.../deploy/fake_docker.py"' STUB_MODE=one python deploy/release_tool.py jobs
"""
import os
import sys
import time

mode = os.environ.get("STUB_MODE", "zero")
if mode == "hang":
    time.sleep(60)
elif mode == "one":
    sys.stdout.write("1\t0\n")
elif mode == "garbage":
    sys.stdout.write("ERROR 1054 (42S22): Unknown column 'x' in 'field list'\n")
    sys.exit(1)
else:
    sys.stdout.write("0\t0\n")
