import sys
from importlib import import_module
from types import ModuleType

from .database import db


_LAZY_ATTRIBUTES = {
    "ma": (".init_sqlalchemy", "ma"),
    "init_databases": (".init_sqlalchemy", "init_databases"),
    "init_dotenv": (".init_dotenv", "init_dotenv"),
    "init_upload": (".init_upload", "init_upload"),
}


def init_plugs(app):
    from .init_dotenv import init_dotenv
    from .init_sqlalchemy import init_databases
    from .init_upload import init_upload

    init_databases(app)
    init_upload(app)
    init_dotenv()


def __getattr__(name):
    try:
        module_name, attribute_name = _LAZY_ATTRIBUTES[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    module = import_module(module_name, __name__)
    value = getattr(module, attribute_name)
    globals()[name] = value
    return value


class _ExtensionsModule(ModuleType):
    def __getattribute__(self, name):
        value = super().__getattribute__(name)
        if name in _LAZY_ATTRIBUTES and isinstance(value, ModuleType):
            return __getattr__(name)
        return value


sys.modules[__name__].__class__ = _ExtensionsModule


__all__ = (
    "db",
    "ma",
    "init_databases",
    "init_dotenv",
    "init_plugs",
    "init_upload",
)
