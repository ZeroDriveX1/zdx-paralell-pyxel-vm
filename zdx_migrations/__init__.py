"""ZDX schema migration module registry.

Future migrations are added as modules in this package. Import-time
registration through :func:`migration` makes them discoverable without
modifying the storage engine.
"""

import importlib
import pkgutil

from zdx_storage import DEFAULT_MIGRATIONS, Migration

_discovered = False


def migration(schema, from_version, to_version, *, rollback=None, name=""):
    def register(function):
        DEFAULT_MIGRATIONS.register(Migration(
            schema=schema,
            from_version=from_version,
            to_version=to_version,
            migrate=function,
            rollback=rollback,
            name=name or function.__name__,
        ))
        return function
    return register


def discover():
    global _discovered
    if _discovered:
        return
    _discovered = True
    for module in pkgutil.iter_modules(__path__):
        if not module.name.startswith("_"):
            importlib.import_module(f"{__name__}.{module.name}")
