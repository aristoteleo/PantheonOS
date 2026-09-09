import json

import pytest

from pantheon.apps.builtin.task.task_toolset import TaskToolSet


@pytest.mark.asyncio
async def test_missing_option_value_uses_label_without_retry():
    ts = TaskToolSet()
    questions = [{"question": "Which?", "header": "Dataset", "input_type": "single_choice", "options": [{"label": "Whole embryo"}, {"label": "Heart"}]}]
    result = await ts.notify_user([], False, "Choose a dataset", "All No", 0.9, questions)
    assert result["success"] and result["interrupt"]
    assert [o["value"] for o in result["questions"][0]["options"]] == ["Whole embryo", "Heart"]
    assert "value" not in questions[0]["options"][0]


def test_task_state_follows_conversation_and_rejects_prefix_siblings(tmp_path):
    ts = TaskToolSet()
    a = {"project_root": str(tmp_path), "chat_id": "chat-1"}
    b = {"project_root": str(tmp_path), "chat_id": "chat-10"}
    ts.get_ephemeral_prompt(a)
    own = str(tmp_path / ".pantheon/brain/chat-1/plan.md")
    sibling = str(tmp_path / ".pantheon/brain/chat-10/plan.md")
    for i, path in enumerate([own, sibling]):
        ts.process_tool_messages(
            [{"id": str(i), "function": {"name": "write_file", "arguments": json.dumps({"file_path": path})}}],
            [{"tool_name": "write_file", "tool_call_id": str(i)}], a,
        )
    assert ts.state.created_artifacts == [own]
    assert own not in ts.get_ephemeral_prompt(b)["content"]
    assert ts.state.created_artifacts == []
    assert own in ts.get_ephemeral_prompt(a)["content"]
