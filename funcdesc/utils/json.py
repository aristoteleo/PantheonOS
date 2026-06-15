import types
import typing as T
import warnings
import json

if T.TYPE_CHECKING:  # pragma: no cover
    from ..desc import Value, SideEffect, Description


NoneType = type(None)


class DescriptionJSONEncoder(json.JSONEncoder):
    def default(self, o: T.Any) -> T.Any:
        from ..desc import Description, Value, SideEffect, _NotDef
        if isinstance(o, Description):
            return {
                "inputs": [self.default(v) for v in o.inputs],
                "outputs": [self.default(v) for v in o.outputs],
                "side_effects": [self.default(v) for v in o.side_effects],
                "name": o.name,
                "doc": o.doc,
            }
        elif isinstance(o, Value):
            if o.type is None:
                t = None
            elif isinstance(o.type, str):
                t = o.type
            elif o.type.__module__ == "typing":
                t = str(o.type)
            elif isinstance(
                    o.type, (T.GenericAlias, types.UnionType)):  # type: ignore
                t = str(o.type)
            else:
                t = o.type.__name__
            d: str | object
            if isinstance(o.default, _NotDef):
                d = "not_defined"
            else:
                d = o.default
            return {
                "type": t,
                "range": o.range,
                "default": d,
                "name": o.name,
                "doc": o.doc,
            }
        elif isinstance(o, SideEffect):
            return {
                "description": o.description,
            }
        else:
            return super().default(o)


class DescriptionJSONDecoder(json.JSONDecoder):
    def decode_value(
            self,
            v: T.Dict[str, T.Any],
            env: T.Optional[T.Dict[str, T.Any]] = None
            ) -> "Value":
        from ..desc import Value, NotDef
        import typing

        # fill the missing values with default values
        v['type'] = v.get('type', None)
        v['range'] = v.get('range', None)
        v['default'] = v.get('default', NotDef)
        v['name'] = v.get('name', None)
        v['doc'] = v.get('doc', None)

        if env is not None:
            env["typing"] = typing

        if isinstance(v["type"], str):
            try:
                t = eval(v["type"], env)
            except NameError:
                warnings.warn(
                    f"Failed to eval type {v['type']}, "
                    "using the original string as type."
                )
                t = v["type"]
        else:
            t = v["type"]

        r = v["range"]

        if v["default"] == "not_defined":
            d = NotDef
        else:
            d = v["default"]
        value = Value(
            type_=t,
            range_=r,
            default=d,
            name=v["name"],
            doc=v["doc"],
        )
        return value

    def decode_side_effect(
            self, v: T.Dict[str, T.Any]) -> "SideEffect":
        from ..desc import SideEffect
        return SideEffect(**v)

    def decode_description(
            self,
            jdict: T.Dict[str, T.Any],
            env: T.Optional[T.Dict[str, T.Any]] = None
            ) -> "Description":
        from ..desc import Description
        desc = Description()
        desc.name = jdict["name"]
        desc.doc = jdict.get("doc", "")
        desc.inputs = [
            self.decode_value(v, env) for v in jdict.get("inputs", [])
        ]
        desc.outputs = [
            self.decode_value(v, env) for v in jdict.get("outputs", [])
        ]
        desc.side_effects = [
            self.decode_side_effect(v) for v in jdict.get("side_effects", [])
        ]
        return desc

    def decode(self, *args, **kwargs) -> T.Any:
        s = args[0]
        jdict = super().decode(s)
        return self.decode_description(jdict, **kwargs)
