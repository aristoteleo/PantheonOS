from funcdesc.desc import Description, NotDef
from typing import List


def desc_to_openai_function(
        desc: Description,
        skip_params: List[str] = []) -> dict:
    type_map = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        dict: "object",
        type(None): "null",
    }

    parameters = {}
    required = []

    for arg in desc.inputs:
        if arg.name in skip_params:
            continue
        tp = type_map.get(arg.type, "string")
        parameters[arg.name] = {
            "type": tp,
            "description": arg.doc or "",
        }
        if arg.default is NotDef:
            required.append(arg.name)

    func_dict = {
        "type": "function",
        "function": {
            "name": desc.name,
            "description": desc.doc or "",
            "parameters": {
                "type": "object",
                "properties": parameters,
                "required": required,
                "additionalProperties": False,
            },
            "strict": True,
        },
    }

    return func_dict
