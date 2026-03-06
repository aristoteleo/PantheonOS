"""Ephemeral message generator for Modal Workflow System.

Aligned with Antigravity's ephemeral prompt design while maintaining
generic mode/artifact support for multi-scenario use cases.
"""

import os
from .task_state import ConversationState, ArtifactRoles

# =============================================================================
# Task State Reminders (mutually exclusive)
# =============================================================================

ACTIVE_TASK_REMINDER = """\
<active_task_reminder>
Remember to update the task as appropriate. The current task is:
task_name:"{task_name}" task_status:"{task_status}" task_summary:"{task_summary}" mode:{ctx_mode}
Tools since last update: {tools_since_update}
Task status changes: {task_update_count}

TASK UPDATE GUIDELINES:
- Update task_boundary only when entering a NEW work phase — NOT with every tool call
- Do not update the status too frequently, a work phase typically spans around 5 tool calls; update status/summary accordingly, Never make two status updates in a row without doing anything in between.
- If calling task_boundary with other tools in parallel, list task_boundary FIRST
- Use %SAME% for unchanged fields (task_name, mode, task_status, task_summary)
- CRITICAL REMINDER: task_status describes NEXT STEPS; task_summary describes what you've DONE

YOUR CURRENT MODE IS: {ctx_mode}. Embody this mindset.
REMEMBER: User WILL NOT SEE your messages. Use notify_user to communicate.
</active_task_reminder>"""

NO_ACTIVE_TASK_REMINDER = """\
<no_active_task_reminder>
You are currently not in a task because: {reason}
If there is no obvious task from the user or if you are just conversing, then it is acceptable to not have a task set. If you are just handling simple one-off requests, such as explaining a single file, or making one or two ad-hoc code edit requests, or making an obvious refactoring request such as renaming or moving code into a helper function, it is also acceptable to not have a task set.
Otherwise, you should use the task_boundary tool to set a task if there is one evident.
Remember that task boundaries should correspond to the artifact task.md, if you have not created the artifact task.md, you should do that first before setting the task_boundary. Remember that task names should be granular and correspond to top-level checklist items, not the entire user request as one task name. If you decide to use the task boundary tool, you must do so concurrently with other tools.
Since you are NOT in an active task section, DO NOT call the `notify_user` tool unless you are requesting review of files.
</no_active_task_reminder>"""

# =============================================================================
# Artifact Reminders
# =============================================================================

ARTIFACT_REMINDER_HAS_ARTIFACTS = """\
<artifact_reminder>
You have created the following artifacts in this conversation so far, here are the artifact paths:
{artifacts}
CRITICAL REMINDER: remember that user-facing artifacts should be AS CONCISE AS POSSIBLE. Keep this in mind when editing artifacts
</artifact_reminder>"""

ARTIFACT_REMINDER_NO_ARTIFACTS = """\
<artifact_reminder>
You have not yet created any artifacts. Please follow the artifact guidelines and create them as needed based on the task.
CRITICAL REMINDER: remember that user-facing artifacts should be AS CONCISE AS POSSIBLE. Keep this in mind when editing artifacts.
Artifacts should be written to: {brain_dir}
</artifact_reminder>"""

# =============================================================================
# Conditional Reminders
# =============================================================================

PLAN_ARTIFACT_MODIFIED_REMINDER = """\
<plan_artifact_modified_reminder>
You have modified {files} during this task in {ctx_mode} mode. Before you switch to execution/analysis mode, you should notify and request the user to review your plan changes via notify_user.
</plan_artifact_modified_reminder>"""

ARTIFACTS_MODIFIED_REMINDER = """\
<artifacts_modified_reminder>
You have modified {count} artifact(s) in this task.
Consider updating them as you progress.
</artifacts_modified_reminder>"""

REQUESTED_REVIEW_NOT_IN_TASK_REMINDER = """\
<requested_review_not_in_task_reminder>
You have used notify_user with {reviewed_files} but haven't set a task boundary since. Based on user intent you should either: (1) Enter planning mode to update the plan (feel free to do additional research based on user feedback), OR (2) Enter execution mode and proceed to implement. Under no circumstances should you update plan artifacts when you're not in a task.
</requested_review_not_in_task_reminder>"""

ARTIFACT_FILE_REMINDER = """\
<artifact_file_reminder>
There are important artifacts that you should be continuously checking or updating as you work:
You have not accessed these files recently:
{files}
please view these files soon to remind yourself of its contents
</artifact_file_reminder>"""

# New: Excessive tools without task boundary reminder (matches Antigravity's emphatic style)
EXCESSIVE_TOOLS_WITHOUT_TASK_REMINDER = """You have called {count} tools in a row without calling the task_boundary tool. This is extremely unexpected. Since you are doing so much work without active engagement with the user, for the next response or tool call you do please concurrently set the task boundary in parallel before continuing any further."""

# Think Tool Reminder
THINK_TOOL_REMINDER = """\
<think_tool_reminder>
You have called {tools_since_think} tools without using the think tool.
Before proceeding, consider using think() to:
- Analyze results from your recent tool calls
- Verify your approach aligns with requirements
- Check if you have all necessary information to proceed
- Identify any potential issues or edge cases

Using think() improves decision quality in complex tool chains.
</think_tool_reminder>"""

# Too Many Steps in One Task Reminder
TOO_MANY_STEPS_IN_TASK_REMINDER = """\
<too_many_steps_in_task_reminder>
WARNING: You have updated the current task "{task_name}" {task_update_count} times.
This suggests the task scope may be too broad. Consider:
1. Should you create a NEW task with a different task_name for the next phase of work?
2. Remember: Each task should correspond to ONE top-level checklist item in task.md
3. If you've completed a distinct phase, start a new task instead of continuing to update this one

Consider whether the current work belongs in a new task boundary.
</too_many_steps_in_task_reminder>"""


def generate_ephemeral_message(state: ConversationState, brain_dir: str) -> str:
    """Generate EPHEMERAL_MESSAGE based on current state.

    Args:
        state: Current conversation state
        brain_dir: Path to the brain directory for artifacts

    Returns:
        XML-formatted ephemeral message content
    """
    parts = []

    # 1. artifact_reminder (always included)
    if state.created_artifacts:
        parts.append(
            ARTIFACT_REMINDER_HAS_ARTIFACTS.format(
                artifacts=chr(10).join(state.created_artifacts)
            )
        )
    else:
        parts.append(ARTIFACT_REMINDER_NO_ARTIFACTS.format(brain_dir=brain_dir))

    # 2. Task state reminder (mutually exclusive)
    EXCESSIVE_TOOLS_THRESHOLD = 5
    TOO_MANY_STEPS_THRESHOLD = 5

    if state.active_task:
        t = state.active_task
        parts.append(
            ACTIVE_TASK_REMINDER.format(
                task_name=t.name,
                ctx_mode=t.mode,
                task_status=t.status,
                task_summary=t.summary,
                tools_since_update=state.tools_since_update,
                task_update_count=state.task_update_count,
            )
        )

        # Add too many steps reminder when threshold exceeded
        if state.task_update_count >= TOO_MANY_STEPS_THRESHOLD:
            parts.append(
                TOO_MANY_STEPS_IN_TASK_REMINDER.format(
                    task_update_count=state.task_update_count,
                    task_name=t.name
                )
            )
    else:
        parts.append(NO_ACTIVE_TASK_REMINDER.format(reason=state.task_boundary_reason))

        # Add excessive tools reminder when not in task
        if state.tools_since_boundary >= EXCESSIVE_TOOLS_THRESHOLD:
            parts.append(
                EXCESSIVE_TOOLS_WITHOUT_TASK_REMINDER.format(
                    count=state.tools_since_boundary
                )
            )

    # 3. Plan artifact modified in plan phase reminder (semantic check)
    if state.active_task and state.active_task.is_plan_phase:
        if state.has_plan_artifacts_modified():
            plan_files = state.get_modified_artifacts_by_role("plan")
            files_str = ", ".join(os.path.basename(p) for p in plan_files)
            parts.append(
                PLAN_ARTIFACT_MODIFIED_REMINDER.format(
                    files=files_str, ctx_mode=state.active_task.mode
                )
            )

    # 4. General artifact modification reminder (non-plan phases)
    if state.active_task and not state.active_task.is_plan_phase:
        all_modified = state.get_all_modified_artifacts()
        if all_modified:
            parts.append(ARTIFACTS_MODIFIED_REMINDER.format(count=len(all_modified)))

    # 5. Pending review reminder (after notify_user, not in task)
    if state.pending_review_paths and not state.active_task:
        reviewed_files = ", ".join(
            os.path.basename(p) for p in state.pending_review_paths
        )
        parts.append(
            REQUESTED_REVIEW_NOT_IN_TASK_REMINDER.format(reviewed_files=reviewed_files)
        )

    # 6. Think Tool reminder (conditional)
    if state.should_remind_think():
        parts.append(
            THINK_TOOL_REMINDER.format(
                tools_since_think=state.tools_since_think
            )
        )

    # 7. Artifact access reminder (stale artifacts)
    STALE_THRESHOLD = 10
    stale_artifacts = [
        path
        for path, last_step in state.artifact_last_access.items()
        if state.current_step - last_step > STALE_THRESHOLD
    ]
    if stale_artifacts:
        artifact_lines = [
            f"- {p} (last access: {state.current_step - state.artifact_last_access[p]} steps ago)"
            for p in stale_artifacts
        ]
        parts.append(ARTIFACT_FILE_REMINDER.format(files=chr(10).join(artifact_lines)))

    return f"<EPHEMERAL_MESSAGE>\n{chr(10).join(parts)}\n</EPHEMERAL_MESSAGE>"
