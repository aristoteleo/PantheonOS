"""Which Apps an agent may use: the ``apps`` field of an agent's
configuration (docs/pantheonos-app-interface.md), which the platform applies
when it binds the agent's tools and the agent when it lists them."""
from __future__ import annotations

import re

_NAME = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,127}')


def app_name(name: str) -> str:
    """An App's name as the Agent sees it: separators as underscores."""
    return re.sub(r'_+', '_', re.sub(r'[^A-Za-z0-9]', '_', name)).strip('_')


class AppFilter:
    """Which Apps one agent may use: a template's ``apps`` field.

    ``allow`` (omitted: every App) and ``deny`` hold App names; a library
    App's id may be written with its separators (``game-of-life``).
    """

    def __init__(self, allow=None, deny=()):
        self.allow = None if allow is None else frozenset(app_name(n) for n in allow)
        self.deny = frozenset(app_name(n) for n in deny)

    def allows(self, name: str) -> bool:
        name = app_name(name)
        return (self.allow is None or name in self.allow) and name not in self.deny

    @staticmethod
    def check(value) -> dict:
        """The canonical form of an ``apps`` field: {allow?, deny?}."""
        if not isinstance(value, dict) or not value.keys() <= {'allow', 'deny'}:
            raise ValueError("apps takes allow and deny lists")
        out = {}
        for key in ('allow', 'deny'):
            if key not in value or value[key] is None:
                continue
            names = value[key]
            if (not isinstance(names, list) or len(names) > 256
                    or not all(isinstance(n, str) and _NAME.fullmatch(n) for n in names)):
                raise ValueError(f"apps.{key} takes a list of App names")
            out[key] = sorted(dict.fromkeys(names))
        return out

    @classmethod
    def from_config(cls, value) -> "AppFilter | None":
        if value is None:
            return None
        value = cls.check(value)
        return cls(value.get('allow'), value.get('deny', ()))

    @staticmethod
    def within(apps, bound: "AppFilter | None") -> dict | None:
        """An ``apps`` field kept inside what ``bound`` allows (None: every
        App): an agent defined elsewhere gets no App its starter may not use."""
        if bound is None:
            return apps
        own = AppFilter.from_config(apps) or AppFilter()
        allow = (bound.allow if own.allow is None else own.allow if bound.allow is None
                 else own.allow & bound.allow)
        out = {} if allow is None else {'allow': sorted(allow)}
        if own.deny | bound.deny:
            out['deny'] = sorted(own.deny | bound.deny)
        return out
