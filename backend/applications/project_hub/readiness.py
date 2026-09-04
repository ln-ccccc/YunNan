"""Pure Project Hub readiness and capability rules."""

from collections.abc import Mapping


CHECK_ORDER = (
    "PROJECT_PROFILE",
    "MINE_BOUNDARY",
    "ACTIVE_BASEMAP",
    "INFERENCE_INPUT",
    "REVIEWABLE_RESULT",
)

_CHECK_REASONS = {
    "PROJECT_PROFILE": "PROJECT_PROFILE_INCOMPLETE",
    "MINE_BOUNDARY": "NO_MINE_BOUNDARY",
    "ACTIVE_BASEMAP": "NO_ACTIVE_BASEMAP",
    "INFERENCE_INPUT": "NO_INFERENCE_INPUT",
    "REVIEWABLE_RESULT": "NO_REVIEWABLE_RESULT",
}

_CHECK_ACTIONS = {
    "MINE_BOUNDARY": ("IMPORT_MINE_BOUNDARY", "spatial_resource"),
    "ACTIVE_BASEMAP": ("CONFIGURE_BASEMAP", "spatial_resource"),
    "INFERENCE_INPUT": ("REGISTER_INFERENCE_INPUT", "dataset"),
    "REVIEWABLE_RESULT": ("REVIEW_RESULT", "classification_result"),
}


def _field(value, name, default=None):
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _lifecycle_status(project):
    return _field(project, "lifecycle_status", _field(project, "status"))


def _has_ready_asset(assets, asset_type, source_type=None):
    return any(
        isinstance(asset, Mapping)
        and asset.get("asset_type") == asset_type
        and asset.get("status") == "ready"
        and (source_type is None or asset.get("source_type") == source_type)
        for asset in assets or ()
    )


def _has_valid_profile(project):
    name = str(_field(project, "name") or "").strip()
    start_year = _field(project, "monitor_start_year")
    end_year = _field(project, "monitor_end_year")
    if not name or isinstance(start_year, bool) or isinstance(end_year, bool):
        return False
    try:
        return int(start_year) <= int(end_year)
    except (TypeError, ValueError):
        return False


def _check_statuses(readiness):
    checks = _field(readiness, "checks", ()) or ()
    return {
        _field(check, "code"): _field(check, "status") == "passed"
        for check in checks
    }


def build_readiness(project, assets):
    """Build the fixed Project Hub readiness DTO from public asset summaries."""
    passed_by_code = {
        "PROJECT_PROFILE": _has_valid_profile(project),
        "MINE_BOUNDARY": _has_ready_asset(assets, "mine_boundary"),
        "ACTIVE_BASEMAP": _has_ready_asset(assets, "basemap"),
        "INFERENCE_INPUT": _has_ready_asset(assets, "imagery"),
        "REVIEWABLE_RESULT": _has_ready_asset(
            assets, "inference_result", source_type="classification_result"
        ),
    }
    checks = [
        {
            "code": code,
            "status": "passed" if passed_by_code[code] else "blocked",
            "reason_code": None if passed_by_code[code] else _CHECK_REASONS[code],
        }
        for code in CHECK_ORDER
    ]
    passed = sum(passed_by_code.values())
    if passed == len(CHECK_ORDER):
        status = "ready"
    elif passed <= 1:
        status = "blocked"
    else:
        status = "partial"

    readiness = {
        "status": status,
        "passed": passed,
        "total": len(CHECK_ORDER),
        "checks": checks,
    }
    blockers = [
        {"code": check["reason_code"], "severity": "warning"}
        for check in checks
        if check["status"] != "passed"
    ]
    return readiness, blockers


def build_next_actions(readiness):
    """Return domain actions for failed resource checks in their fixed order."""
    actions = []
    for check in _field(readiness, "checks", ()) or ():
        action = _CHECK_ACTIONS.get(_field(check, "code"))
        if action is None or _field(check, "status") == "passed":
            continue
        action_code, target = action
        if action_code == "REVIEW_RESULT" and _field(readiness, "status") == "blocked":
            continue
        actions.append({"action_code": action_code, "target": target})
    return actions


def build_capabilities(project, assets, readiness):
    """Return lifecycle-aware project capabilities without transport concerns."""
    passed_by_code = _check_statuses(readiness)
    archived = _lifecycle_status(project) == "archived"
    can_review_result = passed_by_code.get("REVIEWABLE_RESULT", False)
    first_four_passed = all(
        passed_by_code.get(code, False)
        for code in CHECK_ORDER[:4]
    )
    return {
        "can_configure_spatial": not archived,
        "can_open_map": (
            passed_by_code.get("MINE_BOUNDARY", False)
            and passed_by_code.get("ACTIVE_BASEMAP", False)
        ),
        "can_start_inference": not archived and first_four_passed,
        "can_review_result": can_review_result,
        "can_export": can_review_result,
    }
