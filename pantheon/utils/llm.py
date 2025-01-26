from copy import deepcopy
import warnings


def import_litellm():
    warnings.filterwarnings("ignore")
    import litellm
    litellm.suppress_debug_info = True
    return litellm


litellm = import_litellm()


def remove_parsed(messages: list[dict]) -> list[dict]:
    for message in messages:
        if "parsed" in message:
            del message["parsed"]
    return messages


def convert_tool_message(messages: list[dict]) -> list[dict]:
    new_messages = []
    for msg in messages:
        if msg["role"] == "tool":
            resp_prompt = (
                f"Tool `{msg['tool_name']}` called with id `{msg['tool_call_id']}` "
                f"got result:\n{msg['content']}"
            )
            new_msg = {
                "role": "user",
                "content": resp_prompt,
            }
            new_messages.append(new_msg)
        elif msg.get("tool_calls"):
            tool_call_str = str(msg["tool_calls"])
            msg["content"] += f"\nTool calls:\n{tool_call_str}"
            del msg["tool_calls"]
            new_messages.append(msg)
        else:
            new_messages.append(msg)
    return new_messages


def process_messages(messages: list[dict], model: str) -> list[dict]:
    messages = deepcopy(messages)
    messages = remove_parsed(messages)
    if model.startswith("deepseek/"):
        messages = convert_tool_message(messages)
    return messages
