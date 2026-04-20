import os

from pantheon.agent import Agent, AgentRunContext, _RUN_CONTEXT
from pantheon.internal.memory import Memory
from pantheon.repl.conversationRecovery import (
    deserializeMessages,
    deserializeMessagesWithInterruptDetection,
    loadConversationForResume,
)
from pantheon.repl.sessionRestore import (
    exitRestoredWorktree,
    processResumedConversation,
    restoreWorktreeForResume,
)
from pantheon.repl.sessionStorage import (
    adoptResumedSessionFile,
    getSessionStorageState,
    restoreSessionMetadata,
)
from pantheon.utils.token_optimization import (
    _build_collapsed_message,
    _find_collapsible_groups,
    _fingerprint_messages,
    projectView,
    save_context_collapse_entries,
)


def test_deserializeMessages_repairs_missing_tool_results():
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
    ]

    restored = deserializeMessages(messages)

    assert restored[1]["tool_calls"][0]["id"] == "call-1"
    assert restored[2]["role"] == "tool"
    assert restored[2]["tool_call_id"] == "call-1"


def test_deserializeMessagesWithInterruptDetection_converts_interrupted_turn():
    messages = [
        {"role": "user", "content": "run it"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "tool_name": "shell", "content": "partial"},
    ]

    restored = deserializeMessagesWithInterruptDetection(messages)

    assert restored.turnInterruptionState.kind == "interrupted_prompt"
    assert restored.messages[-2]["role"] == "user"
    assert restored.messages[-2]["content"] == "Continue from where you left off."
    assert restored.messages[-1]["role"] == "assistant"


def test_loadConversationForResume_includes_persisted_context_collapse_entries():
    memory = Memory("resume-test")
    messages = [
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "tool_name": "read_file", "content": "a" * 200},
    ]
    memory.add_messages(messages)

    group = _find_collapsible_groups(messages, min_group_size=2)[0]
    summary_message = _build_collapsed_message(group, commit_id=1)
    save_context_collapse_entries(
        memory,
        [
            {
                "collapseId": 1,
                "archivedPattern": list(_fingerprint_messages(messages[1:3])),
                "summaryMessage": summary_message,
                "summaryText": str(summary_message.get("content", "")),
                "archivedCount": 2,
            }
        ],
    )

    resumed = loadConversationForResume(memory)

    assert resumed is not None
    assert len(resumed["contextCollapseCommits"]) == 1
    assert resumed["customTitle"] == "resume-test"


def test_restoreFromEntries_rehydrates_manager_from_memory():
    memory = Memory("collapse-memory")
    original_messages = [
        {"role": "user", "content": "inspect"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "tool_name": "read_file", "content": "x" * 200},
    ]

    group = _find_collapsible_groups(original_messages, min_group_size=2)[0]
    summary_message = _build_collapsed_message(group, commit_id=1)
    save_context_collapse_entries(
        memory,
        [
            {
                "collapseId": 1,
                "archivedPattern": list(_fingerprint_messages(original_messages[1:3])),
                "summaryMessage": summary_message,
                "summaryText": str(summary_message.get("content", "")),
                "archivedCount": 2,
            }
        ],
    )

    token = _RUN_CONTEXT.set(
        AgentRunContext(
            agent=Agent(name="resume-agent", instructions="x"),
            memory=memory,
        )
    )
    try:
        projected = projectView(original_messages)
    finally:
        _RUN_CONTEXT.reset(token)

    assert projected[1]["role"] == "user"
    assert "[contextCollapse]" in projected[1]["content"]


def test_session_storage_metadata_and_adopted_file_pointer():
    memory = Memory("chat-a")

    restoreSessionMetadata(
        {
            "customTitle": "Renamed Chat",
            "agentSetting": "Leader",
            "tag": "resume",
        },
        memory=memory,
    )
    adoptResumedSessionFile(memory)

    state = getSessionStorageState(memory)
    assert memory.name == "Renamed Chat"
    assert state["metadata"]["customTitle"] == "Renamed Chat"
    assert state["metadata"]["agentSetting"] == "Leader"
    assert state["sessionFile"]["adopted"] is True


def test_restoreSessionMetadata_preserves_auto_renamed_name():
    """Regression: once a chat is auto-renamed (name_generated=True), a stale
    customTitle must not clobber memory.name on the next turn. Sync should go
    the other way — customTitle follows memory.name."""
    memory = Memory("👋Simple Greeting")
    memory.extra_data["name_generated"] = True
    # Pre-seed stale session_storage like what would be on disk after msg 1
    # completed before the customTitle-sync fix landed.
    memory.extra_data["session_storage"] = {
        "metadata": {"customTitle": "New Chat"},
    }

    # Simulate turn-2 loadConversationForResume returning the stale title.
    restoreSessionMetadata({"customTitle": "New Chat"}, memory=memory)

    assert memory.name == "👋Simple Greeting"
    state = getSessionStorageState(memory)
    assert state["metadata"]["customTitle"] == "👋Simple Greeting"


async def test_processResumedConversation_restores_worktree_and_metadata(tmp_path):
    original_cwd = os.getcwd()
    original_dir = tmp_path / "orig"
    worktree_dir = tmp_path / "worktree"
    original_dir.mkdir()
    worktree_dir.mkdir()
    os.chdir(original_dir)
    try:
        memory = Memory("resume-chat")
        result = {
            "messages": [{"role": "user", "content": "hello"}],
            "customTitle": "Resume Chat",
            "worktreeSession": {
                "originalCwd": str(original_dir),
                "worktreePath": str(worktree_dir),
                "worktreeName": "worktree",
                "sessionId": "chat-1",
            },
            "contextCollapseCommits": [],
            "contextCollapseSnapshot": None,
        }

        processed = await processResumedConversation(
            result,
            {"forkSession": False},
            {"memory": memory, "initialState": {}},
        )

        assert processed["messages"][0]["content"] == "hello"
        assert os.getcwd() == str(worktree_dir)
        state = getSessionStorageState(memory)
        assert state["metadata"]["customTitle"] == "Resume Chat"
        assert state["sessionFile"]["adopted"] is True
    finally:
        exitRestoredWorktree()
        os.chdir(original_cwd)
