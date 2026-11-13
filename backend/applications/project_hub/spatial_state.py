RESOURCE_TYPES = ("mine_vector", "basemap")

# A 轨 missing_resources 兼容词汇与 readiness 检查码的对应关系（问题24 契约：
# 权威可操作性判定 = readiness/capabilities，含 R3 磁盘瓦片巡检；本模块的
# mine_vector/basemap 是旧 DTO 兼容词汇，序列化层经此表与权威检查码对齐，
# 消费方不得再自建第三套判断）。词汇映射：mine_vector↔MINE_BOUNDARY、
# basemap↔ACTIVE_BASEMAP；overview summary 的 missing_resources 额外含
# inference_input/reviewable_result（readiness 词汇），差异见架构契约文档。
RESOURCE_TYPE_BY_CHECK_CODE = {
    "MINE_BOUNDARY": "mine_vector",
    "ACTIVE_BASEMAP": "basemap",
}


def serialize_project_spatial_state(project, basemap_tiles_available=None):
    """项目空间三键（spatial_status/map_ready/missing_resources，A 轨兼容词汇）。

    basemap_tiles_available 是激活底图的磁盘瓦片探针结果（问题24 统一判据）：
    - None=未探测（项目列表等轻量口径，不扫磁盘——N 项目×磁盘探针违反
      AGENTS §10 性能底线，列表三键因此是记录级兼容快照，权威可操作性
      判定以 overview 的 readiness/capabilities 为准）；
    - False=active 记录存在但瓦片缺失：basemap 按未就绪处理（map_ready=False、
      missing_resources 含 basemap），与 readiness 的 ACTIVE_BASEMAP_TILES_MISSING
      语义一致，地图打开/空间面板/概览三入口对可操作性结论一致；
    - True=瓦片可用，与记录级判定同判。
    """
    resources = list(getattr(project, "spatial_resources", ()) or ())
    active_types = {item.resource_type for item in resources if item.status == "active"}
    missing = [item for item in RESOURCE_TYPES if item not in active_types]
    if basemap_tiles_available is False and "basemap" not in missing:
        missing = missing + ["basemap"]
    if any(item.status in {"pending", "processing"} for item in resources):
        status = "processing"
    elif not missing:
        status = "ready"
    elif any(item.status == "failed" and item.resource_type in missing for item in resources):
        status = "failed"
    else:
        status = "unconfigured"
    return {
        "spatial_status": status,
        "map_ready": not missing,
        "missing_resources": missing,
    }
