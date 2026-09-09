import typing as T

from .desc import Description, Value, NotDef
from .parse import parse_func

from pydantic import create_model, Field


def value_to_field(value: Value, *, alias: str | None = None):
    kwargs = {
        "description": value.doc,
    }
    if value.default is not NotDef:
        kwargs["default"] = value.default  # type: ignore
    if alias is not None:
        kwargs["alias"] = alias
    field = Field(**kwargs)  # type: ignore
    return field


def desc_to_pydantic(description: Description) -> dict:
    res = {}
    for _tp in ("inputs", "outputs"):
        fields = {}
        names = {val.name for val in getattr(description, _tp)}
        for val in getattr(description, _tp):
            name = val.name
            # Python tools may accept legacy keys such as _action / _args.
            # Pydantic rejects those as field names. Keep the wire key as an
            # alias, rather than dropping the entire tool during discovery.
            alias = None
            if isinstance(name, str) and name.startswith("_"):
                alias = name
                name = "tool_param" + name
                while name in names:
                    name = "tool_param_" + name
                names.add(name)
            field = value_to_field(val, alias=alias)
            fields[name] = (val.type, field)
        res[_tp] = create_model(  # type: ignore
            description.name or _tp,
            **fields
        )
    return res


def parse_func_pydantic(func: T.Callable) -> dict:
    desc = parse_func(func)
    return desc_to_pydantic(desc)
