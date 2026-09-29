# -*- coding: utf-8 -*-
"""UTC 时间 DTO 口径（gui-audit #1 收尾）：naive 视作 UTC 补 Z，aware 归一到 UTC。

所有对外时间字段统一经此序列化；快照 manifest 内的原始时间例外
（service._serialize_internal_activity，恢复需回写原始墙钟时间）。
"""
from datetime import date, datetime, timezone


def to_utc_z(value):
    """datetime/ISO 串 → 带 Z 后缀的 UTC ISO 串；无法解析时原样返回。"""
    if value is None or isinstance(value, (int, float)):
        return value
    # datetime 是 date 的子类，必须先判 datetime，否则全部时间被截成午夜
    if isinstance(value, datetime):
        pass
    elif isinstance(value, date):
        value = datetime(value.year, value.month, value.day)
    else:
        try:
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")
