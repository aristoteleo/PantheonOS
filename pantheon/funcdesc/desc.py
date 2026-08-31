import typing as T
from copy import copy
import inspect

from .utils.json import DescriptionJSONEncoder, DescriptionJSONDecoder
from .utils.misc import CreateByGetItem


class _NotDef:
    def __str__(self):
        return "NotDef"


NotDef = _NotDef()


T1 = T.TypeVar("T1")


TypeChecker = T.Callable[[T.Any, type], bool]
RangeChecker = T.Callable[[T.Any, T.Any], bool]


class Value(metaclass=CreateByGetItem):
    """The description of a value."""
    type_to_range_checker: T.Dict[type, TypeChecker] = {}
    type_to_type_checker: T.Dict[type, RangeChecker] = {}

    def __init__(
            self,
            type_: T.Optional[T.Type[T1]] = None,
            range_: T.Optional[T.Any] = None,
            default: T.Union[_NotDef, T1] = NotDef,
            name: T.Optional[str] = None,
            doc: T.Optional[str] = None,
            **kwargs,
            ):
        self.name = name
        self.type = type_
        self.range = range_
        self.default = default
        self.kwargs = kwargs
        self.doc = doc

    @property
    def type_checker(self):
        if self.type is None:
            return None
        else:
            return self.type_to_type_checker.get(self.type.__name__, None)

    @property
    def range_checker(self):
        if self.type is None:
            return None
        else:
            return self.type_to_range_checker.get(self.type.__name__, None)

    @classmethod
    def register_range_check(cls, type, checker):
        cls.type_to_range_checker[type.__name__] = checker

    @classmethod
    def register_type_check(cls, type, checker=None):
        checker = checker or (lambda v, t: isinstance(v, t))
        cls.type_to_type_checker[type.__name__] = checker

    def check_type(self, val):
        if self.type_checker is not None:
            if not self.type_checker(val, self.type):
                raise TypeError(
                    f"Value {val} is not in valid type({self.type})")

    def check_range(self, val):
        if (self.range_checker is not None):
            if (not self.range_checker(val, self.range)):
                raise ValueError(
                    f"Value {val} is not in a valid range({self.range}).")

    def __repr__(self):
        return (
            f"<Value name={self.name} type={self.type} range={self.range} "
            f"default={self.default}>"
        )

    def __eq__(self, other):
        return (
            self.name == other.name and
            self.doc == other.doc and
            self.type == other.type and
            self.range == other.range and
            self.default == other.default
        )


# register basic types
def _check_number_in_range(v, range):
    return (range is None) or (range[0] <= v <= range[1])


Value.register_type_check(str)
Value.register_range_check(int, _check_number_in_range)
Value.register_type_check(int)
Value.register_range_check(float, _check_number_in_range)
Value.register_type_check(float)
Value.register_type_check(bool)


class SideEffect():
    def __init__(self, description: str):
        self._description = description

    @property
    def description(self) -> str:
        return self._description

    def check_before_run(self, inputs: dict) -> bool:
        return True

    def check_after_run(self, inputs: dict, outputs: dict) -> bool:
        return True

    def __repr__(self):
        return f"<{self.__class__.__name__} description={self.description}>"

    def __eq__(self, other):
        return self.description == other.description


class Description():
    """The description of a function."""
    def __init__(
            self,
            inputs: T.Optional[T.List[Value]] = None,
            outputs: T.Optional[T.List[Value]] = None,
            side_effects: T.Optional[T.List[SideEffect]] = None,
            name: T.Optional[str] = None,
            doc: T.Optional[str] = None,
            ):
        self.inputs = [] if inputs is None else inputs
        self.outputs = [] if outputs is None else outputs
        self.side_effects = [] if side_effects is None else side_effects
        self.name = name
        self.doc = doc

    def parse_pass_in(self, args: tuple, kwargs: dict) -> T.Dict[str, T.Any]:
        """Get the pass in value of the func
        arguments according to the inputs description."""

        args_ = list(args)
        kwargs = copy(kwargs)
        res = {}
        for val in self.inputs:
            has_default = val.default is not NotDef
            name = "?" if val.name is None else val.name
            if len(args_) > 0:
                res[name] = args_.pop(0)
            elif (len(kwargs) > 0) and (name in kwargs):
                res[name] = kwargs.pop(name)
            else:
                if has_default:
                    res[name] = val.default
                else:
                    raise TypeError(
                        f"{name} is not provided and has no default value."
                    )
        return res

    def to_dict(self) -> T.Dict[str, T.Any]:
        return DescriptionJSONEncoder().default(self)

    @classmethod
    def from_dict(
            cls,
            d: T.Dict[str, T.Any], env: T.Optional[T.Dict[str, T.Any]] = None
            ) -> "Description":
        return DescriptionJSONDecoder().decode_description(d, env=env)

    def to_json(self) -> str:
        json_str = DescriptionJSONEncoder().encode(self)
        return json_str

    @classmethod
    def from_json(
            cls,
            json_str: str,
            env: T.Optional[T.Dict[str, T.Any]] = None
            ) -> "Description":
        return DescriptionJSONDecoder().decode(json_str, env=env)

    def __repr__(self) -> str:
        return (
            "<Description\n"
            f"\tinputs={self.inputs}\n"
            f"\toutputs={self.outputs}\n"
            f"\tside_effects={self.side_effects}\n"
            f"\tname={self.name}\n"
            f"\tdoc={self.doc}\n"
            ">"
        )

    def __eq__(self, other):
        return (
            self.inputs == other.inputs and
            self.outputs == other.outputs and
            self.side_effects == other.side_effects and
            self.name == other.name and
            self.doc == other.doc
        )

    def compose_signature(self) -> inspect.Signature:
        """Compose the signature of the function
        according to the description."""
        params = []
        for val in self.inputs:
            default: T.Any
            if val.default is NotDef:
                default = inspect._empty
            else:
                default = val.default
            params.append(
                inspect.Parameter(
                    val.name or "?",
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    default=default,
                    annotation=val.type,
                )
            )
        rtn_type: T.Union[None, type, list]
        if len(self.outputs) == 0:  # pragma: no cover
            rtn_type = inspect._empty
        elif len(self.outputs) == 1:
            rtn_type = self.outputs[0].type
            if rtn_type is type(None):  # noqa: E721
                rtn_type = inspect._empty
        else:
            rtn_type = [val.type for val in self.outputs]
        sig = inspect.Signature(
            params,
            return_annotation=rtn_type
        )
        return sig
