import typing as T
from pathlib import Path

from ..desc import Value


class ValueType(object):
    """The base custom value type, for
    custom type and range checking.

    Sub-class should override the `check_type` or
    `check_range` method.
    """
    @staticmethod
    def check_type(val: T.Any, type_: T.Type) -> bool:
        return True

    @staticmethod
    def check_range(val: T.Any, range_: T.Any) -> bool:
        return True


class OneOf(ValueType):
    """Input value should be equal to at least one elements in `range`"""
    @staticmethod
    def check_range(val, range_):
        return val in range_


class SubSet(ValueType):
    """Input value should be a subset of `range`"""
    @staticmethod
    def check_range(val, range_):
        return all([v in range_ for v in val])


class InputPath(ValueType):
    """Input value should be a file path,
    and the file should be exist."""
    @staticmethod
    def check_type(val, type_):
        return isinstance(val, str) or isinstance(val, Path)

    @staticmethod
    def check_range(val, range_):
        path = Path(val) if isinstance(val, str) else val
        return path.exists()


class OutputPath(ValueType):
    """Input value should be a file path. """
    @staticmethod
    def check_type(val, type_):
        return isinstance(val, str) or isinstance(val, Path)


Value.register_range_check(OneOf, OneOf.check_range)
Value.register_range_check(SubSet, SubSet.check_range)
Value.register_type_check(InputPath, InputPath.check_type)
Value.register_range_check(InputPath, InputPath.check_range)
Value.register_type_check(OutputPath, OutputPath.check_type)
