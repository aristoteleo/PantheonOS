import typing as T

from .desc import Description, Value, NotDef
from .parse import parse_func

from pydantic import create_model, Field


def value_to_field(value: Value):
    kwargs = {
        "description": value.doc,
    }
    if value.default is not NotDef:
        kwargs["default"] = value.default  # type: ignore
    field = Field(**kwargs)  # type: ignore
    return field


def desc_to_pydantic(description: Description) -> dict:
    res = {}
    for _tp in ("inputs", "outputs"):
        fields = {}
        for val in getattr(description, _tp):
            field = value_to_field(val)
            fields[val.name] = (val.type, field)
        res[_tp] = create_model(  # type: ignore
            description.name or _tp,
            **fields
        )
    return res


def parse_func_pydantic(func: T.Callable) -> dict:
    desc = parse_func(func)
    return desc_to_pydantic(desc)
