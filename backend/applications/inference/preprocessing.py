"""解译请求和 worker 共用的显式预处理编号校验。"""


def normalize_preprocessing_options(payload):
    result = {}
    for field, allowed in (("prehandle", (0, 2, 4)), ("denoise", (0, 3, 5))):
        if field not in payload:
            continue
        value = payload[field]
        if type(value) is not int or value not in allowed:
            raise ValueError(f"{field} 必须是整数编号 {', '.join(map(str, allowed))}，请重新选择处理方式")
        result[field] = value
    return result
