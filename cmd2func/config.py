import typing as T
import sys
if sys.version_info < (3, 11):  # pragma: no cover
    from typing_extensions import TypedDict, NotRequired
else:  # pragma: no cover
    from typing import TypedDict, NotRequired


from funcdesc.desc import Description, Value, NotDef


class ArgDef(TypedDict):
    type: str
    default: NotRequired[T.Any]
    true_insert: NotRequired[T.Any]
    false_insert: NotRequired[T.Any]


class CLIConfig(TypedDict):
    name: NotRequired[str]
    inputs: T.Dict[str, ArgDef]
    inputs_order: NotRequired[T.List[str]]


def extrace_key(d: dict, key, default) -> T.Any:
    if key in d:
        return d.pop(key)
    else:
        return default


def config_to_desc(config: CLIConfig) -> Description:
    """Convert a config to a funcdesc's Description object."""
    args_conf = config['inputs'].copy()
    order = config.get('inputs_order', list(args_conf.keys()))
    args = [args_conf.pop(n) for n in order]
    inputs = []
    for n, p_conf in zip(order, args):
        pc: dict = {**p_conf}
        _tp = extrace_key(pc, 'type', None)
        _default = extrace_key(pc, 'default', NotDef)
        val = Value(
            type_=eval(_tp),
            default=_default,
            name=n,
            **pc
        )
        inputs.append(val)
    desc = Description(inputs)
    return desc
