# -*- coding: utf-8 -*-
"""API 层泛化异常的统一收口。

与 project_hub 已有范式一致：业务校验类异常（ValueError/FileNotFoundError）
的消息保持回显给前端；其余异常的原文可能包含物理路径、SQL、内部堆栈等信息，
只记入服务端日志并对客户端返回通用文案 + 500。
"""
import logging

from applications.common.utils.http import fail_api
from applications.project_hub._foundation import ProjectNotFoundError

# 这些异常的消息是面向用户的业务校验文案（如“项目不存在”“文件不存在: xx.tif”）
BUSINESS_EXCEPTIONS = (ValueError, FileNotFoundError)


def business_or_server_failure(error, message, logger=None, business_status=None):
    """泛化异常收口：业务校验消息回显原文，其余只记日志并返回通用文案 + 500。

    必须在 except 块内调用，logger.exception 才能带上完整堆栈。
    business_status 用于业务异常需要特定 HTTP 状态（如 404）的既有接口。
    """
    log = logger or logging.getLogger(__name__)
    if isinstance(error, BUSINESS_EXCEPTIONS):
        if business_status is not None:
            return fail_api(str(error), status=business_status)
        if isinstance(error, ProjectNotFoundError):
            # 类型化 404 语义（2026-11-13）：项目不存在/已软删在此统一映射，
            # 项目详情等经 get_project_detail 的入口不再落进 ValueError→400
            return fail_api(str(error), status=404)
        # 业务校验失败默认 400（2026-11-13 P2-2 收口：不再 HTTP 200 + success=false）
        return fail_api(str(error), status=400)
    log.exception(message)
    return fail_api(f"{message}，请检查服务日志", status=500)
