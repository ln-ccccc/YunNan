# -*- coding: utf-8 -*-
"""API 层泛化异常的统一收口。

与 project_hub 已有范式一致：业务校验类异常（ValueError/FileNotFoundError）
的消息保持回显给前端；其余异常的原文可能包含物理路径、SQL、内部堆栈等信息，
只记入服务端日志并对客户端返回通用文案 + 500。

业务异常按类型白名单不可靠（2026-11-14 审查问题21）：原生 OS 异常也可能以
ValueError/FileNotFoundError 形态上抛，str() 含物理路径（如
"[Errno 2] No such file or directory: '/project_storage/...'"、上传会话把
OSError 拼进业务异常）。公开前按消息内容检测路径特征，命中即收口为通用文案。
"""
import logging
import re

from applications.common.utils.http import fail_api

# 这些异常的类型是面向用户的业务校验承载（消息通常是"项目不存在"等安全文案）
BUSINESS_EXCEPTIONS = (ValueError, FileNotFoundError)

# 原生 OS 异常标记 / Windows 盘符路径 / POSIX 常见根路径（URL 的 "://" 已被
# 后视排除；业务文案均为中文+存储相对键，不命中）
_UNSAFE_PATH_PATTERN = re.compile(
    r"\[(?:Errno|WinError)\s+\d+\]"
    r"|[A-Za-z]:[\\/]"
    r"|(?<![A-Za-z0-9:])/(?:home|root|var|tmp|opt|srv|data|mnt|storage|etc|usr|app|project_storage)(?:/|\b)"
)


def _contains_physical_path(text) -> bool:
    if not isinstance(text, str):
        return False
    return bool(_UNSAFE_PATH_PATTERN.search(text))


def business_or_server_failure(error, message, logger=None, business_status=None):
    """泛化异常收口：业务校验消息回显原文，其余只记日志并返回通用文案 + 500。

    必须在 except 块内调用，logger.exception 才能带上完整堆栈。
    business_status 用于业务异常需要特定 HTTP 状态（如 404）的既有接口。
    """
    log = logger or logging.getLogger(__name__)
    if isinstance(error, BUSINESS_EXCEPTIONS):
        text = str(error)
        if _contains_physical_path(text):
            log.warning("业务异常消息含物理路径，对外已收口: %s", text, exc_info=error)
            return fail_api(f"{message}，请检查服务日志", status=500)
        if business_status is None:
            return fail_api(text)
        return fail_api(text, status=business_status)
    log.exception(message)
    return fail_api(f"{message}，请检查服务日志", status=500)
