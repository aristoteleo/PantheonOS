"""Chat-completion message normalization shared by Apps and provider adapters.

Keep this module free of Agent, settings and provider SDK imports. The legacy
public functions re-export these implementations with their existing semantics.
"""

_ALLOWED_MESSAGE_FIELDS = {
    "role", "content", "name", "tool_calls", "tool_call_id",
    "refusal", "function_call",  # OpenAI standard fields
}


def remove_metadata(messages: list[dict]) -> list[dict]:
    """
    Strip messages down to only standard OpenAI fields before sending to LLM.

    Strict providers like Groq reject ANY unknown field (chat_id, _metadata,
    _llm_content, _user_metadata, detected_attachments, etc.) and also
    reject null values for optional fields like tool_calls.
    """
    for msg in messages:
        # Remove non-standard fields
        extra_keys = [k for k in msg if k not in _ALLOWED_MESSAGE_FIELDS]
        for k in extra_keys:
            del msg[k]
        # Remove fields with None/null values (Groq rejects "tool_calls": null)
        null_keys = [k for k in ("tool_calls", "tool_call_id", "name", "function_call", "refusal")
                     if k in msg and msg[k] is None]
        for k in null_keys:
            del msg[k]
    return messages


def _sanitize_tool_messages_for_chat_completions(messages: list[dict]) -> list[dict]:
    """Strip image content from tool-role messages for Chat Completions.

    OpenAI Chat Completions API rejects image_url blocks in tool-role messages
    with: "Image URLs are only allowed for messages with role 'user'". When a
    tool returns images in a native-mode-capable design, the OpenAI adapter
    must flatten them to a text placeholder so the call still succeeds.

    Returns a new list; original messages are not mutated.
    """
    from pantheon.utils.adapters.image_blocks import has_image_content, split_text_and_images

    result: list[dict] = []
    for msg in messages:
        if msg.get("role") != "tool":
            result.append(msg)
            continue
        content = msg.get("content")
        if not has_image_content(content):
            result.append(msg)
            continue
        text, inline, http = split_text_and_images(content)
        n_images = len(inline) + len(http)
        placeholder_parts = []
        if text:
            placeholder_parts.append(text)
        if n_images:
            placeholder_parts.append(
                f"[{n_images} image(s) returned by the tool but not shown: "
                "OpenAI Chat Completions does not support images in tool "
                "messages. Use an Anthropic or Gemini model to see them.]"
            )
        new_msg = dict(msg)
        new_msg["content"] = "\n\n".join(placeholder_parts) if placeholder_parts else ""
        result.append(new_msg)
    return result

