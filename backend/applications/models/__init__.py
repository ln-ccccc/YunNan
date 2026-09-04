from importlib import import_module


_MODEL_EXPORTS = {
    "Analysis": (".analysis", "Analysis"),
    "AdminUser": (".admin_user", "AdminUser"),
    "ClassificationEditAudit": (".classification_result", "ClassificationEditAudit"),
    "ClassificationResult": (".classification_result", "ClassificationResult"),
    "ClassificationRevision": (".classification_result", "ClassificationRevision"),
    "Photo": (".photo", "Photo"),
    "InferenceJob": (".inference_job", "InferenceJob"),
    "InferenceWorkerState": (".inference_job", "InferenceWorkerState"),
    "Project": (".project", "Project"),
    "ProjectActivityLog": (".project", "ProjectActivityLog"),
    "ProjectBackupRecord": (".project", "ProjectBackupRecord"),
    "ProjectDataset": (".project", "ProjectDataset"),
    "ProjectExportRecord": (".project", "ProjectExportRecord"),
    "ProjectMineBinding": (".project", "ProjectMineBinding"),
    "ProjectSpatialResource": (".project_spatial", "ProjectSpatialResource"),
    "ProjectSpatialJob": (".project_spatial", "ProjectSpatialJob"),
}


def __getattr__(name):
    try:
        module_name, attribute_name = _MODEL_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    module = import_module(module_name, __name__)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value


def load_all_models():
    for name in _MODEL_EXPORTS:
        if name not in globals():
            __getattr__(name)


__all__ = (*_MODEL_EXPORTS, "load_all_models")
