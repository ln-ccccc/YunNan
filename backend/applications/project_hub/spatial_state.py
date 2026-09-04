RESOURCE_TYPES = ("mine_vector", "basemap")


def serialize_project_spatial_state(project):
    resources = list(getattr(project, "spatial_resources", ()) or ())
    active_types = {item.resource_type for item in resources if item.status == "active"}
    missing = [item for item in RESOURCE_TYPES if item not in active_types]
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
