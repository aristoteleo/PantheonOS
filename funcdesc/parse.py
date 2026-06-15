import types
import typing as T
import inspect

from .desc import Description, Value
from .mark import FUNC_MARK_STORE_KEY, FuncMarks


def _update_val_by_marks(
        mark_idx: int, name: str,
        marks: T.Dict, val: Value
        ):
    if mark_idx in marks:
        store = marks[mark_idx].copy()
    elif name in marks:
        store = marks[name].copy()
    else:
        store = None

    if store is not None:
        val.kwargs.update(store.pop("attrs"))
        val.__dict__.update(store)


def parse_func_inputs(
        sig: inspect.Signature,
        is_method: bool,
        marks: T.Dict[T.Union[int, str], dict],
        ) -> T.List[Value]:
    inputs = []
    for idx, (name, param) in enumerate(sig.parameters.items()):
        ann = param.annotation
        if isinstance(ann, Value):
            val = ann
        elif ann is inspect._empty:
            val = Value(None)
        else:
            val = Value(ann)

        val.name = name

        # set default value
        if param.default is not inspect._empty:
            val.default = param.default

        # update by marks
        mark_idx = idx + 1 if is_method else idx
        _update_val_by_marks(mark_idx, name, marks, val)

        inputs.append(val)
    return inputs


def marks_to_outputs(marks):
    outputs = []
    if len(marks) == 0:
        val = Value(type(None))
        outputs.append(val)
    else:
        marks_keys = list(marks.keys())
        if all([isinstance(k, int) for k in marks_keys]):
            for _ in range(max(marks_keys) + 1):  # type: ignore
                val = Value(type(None))
                outputs.append(val)
        elif (len(marks) == 1) and isinstance(marks_keys[0], str):
            val = Value(type(None), name=marks_keys[0])
            outputs.append(val)
        else:
            raise ValueError(
                f"Invalid marks keys: {marks_keys},"
                " the keys should be all int or single str."
            )
    return outputs


def parse_func_outputs(
        sig: inspect.Signature,
        marks: T.Dict[T.Union[int, str], dict]
        ) -> T.List[Value]:
    outputs = []
    ret = sig.return_annotation

    def to_val(o):
        return o if isinstance(o, Value) else Value(o)

    val: Value
    if ret is inspect._empty:
        outputs.extend(marks_to_outputs(marks))
    elif isinstance(ret, Value):
        outputs.append(ret)
    elif isinstance(ret, list):
        outputs.extend([to_val(o) for o in ret])
    elif T.get_origin(ret) is tuple:
        outputs.extend([to_val(o) for o in ret.__args__])
    else:
        val = Value(ret)
        outputs.append(val)

    # update by marks
    for idx, val in enumerate(outputs):
        if val.name is None:
            val.name = f"output_{idx}"
        _update_val_by_marks(idx, val.name, marks, val)
    return outputs


def update_using_docstring(desc: Description, docstring: str):
    import docstring_parser
    doc = docstring_parser.parse(docstring)
    for i, val in enumerate(desc.inputs):
        if val.doc is None:
            val.doc = doc.params[i].description
        if val.type is None:
            val.type = eval(doc.params[i].type_name or "None")
    if doc.returns is not None:
        for i, val in enumerate(desc.outputs):
            if val.doc is None:
                val.doc = doc.returns.description
            if val.type is type(None):
                val.type = eval(doc.returns.type_name or "None")


def parse_func(
        func: T.Callable,
        update_by_docstring: bool = False
        ) -> Description:
    """Parse the function and return a Description object."""
    sig = inspect.signature(func)
    is_method = isinstance(func, types.MethodType)
    func_marks: T.Optional[FuncMarks] = func.__dict__.get(FUNC_MARK_STORE_KEY)
    desc = parse_signature(sig, is_method, func_marks)
    if update_by_docstring:
        update_using_docstring(desc, func.__doc__ or "")
    desc.name = func.__name__
    desc.doc = func.__doc__
    return desc


def parse_signature(
        sig: inspect.Signature,
        is_method: bool = False,
        func_marks: T.Optional[FuncMarks] = None
        ) -> Description:
    """Parse the function signature and return a Description object."""
    if func_marks is None:
        func_marks = FuncMarks()
    desc = Description()
    inputs = parse_func_inputs(sig, is_method, func_marks.input_marks)
    desc.inputs = inputs
    outputs = parse_func_outputs(sig, func_marks.output_marks)
    desc.outputs = outputs
    desc.side_effects = func_marks.side_effect_marks
    return desc
