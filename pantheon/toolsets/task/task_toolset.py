"""TaskToolSet for Modal Workflow System.

Provides task_boundary and notify_user tools for managing
workflow modes (PLANNING/EXECUTION/VERIFICATION or RESEARCH/ANALYSIS/INTERPRETATION).
"""

import json
from pathlib import Path
from typing import Optional

from pantheon.toolset import ToolSet, tool
from pantheon.utils.log import logger
from .task_state import ConversationState, ModeSemantics
from .ephemeral import generate_ephemeral_message


class TaskToolSet(ToolSet):
    """Local task toolset - one instance per Agent, state persists across run() calls."""

    STATE_FILE = "task_state.json"

    def __init__(self, name="task", **kwargs):
        super().__init__(name, **kwargs)
        self.state = ConversationState()
        self._last: dict[str, Optional[str]] = {}  # task_name, mode, status, summary
        self._loaded = False

    def _save(self, brain_dir: str):
        """Persist state to disk."""
        path = Path(brain_dir) / self.STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"last": self._last, "state": self.state.to_dict()}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load(self, brain_dir: str):
        """Lazy load state from disk (only once)."""
        if self._loaded:
            return
        self._loaded = True
        path = Path(brain_dir) / self.STATE_FILE
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._last = data.get("last", {})
            self.state = ConversationState.from_dict(data.get("state", {}))
            logger.info(f"[TaskToolSet] Restored state from {path}")
        except Exception as e:
            logger.warning(f"[TaskToolSet] Failed to load state: {e}")

    def _get_brain_dir(self, context: dict) -> str:
        """Get the PER-CONVERSATION brain_dir from context.

        Keyed by chat_id ONLY — chat_id is globally unique, so it alone isolates
        each conversation's task_state.json + artifacts. (Previously also nested
        under client_id, the UI-connection id; that was redundant and made the
        path non-deterministic for readers that don't know the client_id.)

        Anchor priority:
        1. `project_root` — the chat's IMMUTABLE project root, set once by
           ChatRoom.chat(). Prefer this over `workdir`: `workdir` is an
           endpoint-cwd hint that proxy_toolset legitimately pops/overwrites on
           the shared per-task context for every endpoint-toolset call, so by the
           time a later task_boundary (or ephemeral refresh) runs it may be gone —
           which silently relocated the brain to the global home dir, splitting it
           away from the workspace. `project_root` is never mutated.
        2. `workdir` — legacy fallback (e.g. isolated chats that only set workdir).
        3. settings.brain_dir — global home, last resort.
        """
        chat_id = context.get("chat_id") or "default"

        root = context.get("project_root") or context.get("workdir")
        if root:
            brain_path = Path(root) / ".pantheon" / "brain" / chat_id
            logger.debug(f"[TaskToolSet] Using project-root brain_dir: {brain_path}")
            return str(brain_path)

        # Last resort: the global home brain dir.
        from pantheon.settings import get_settings
        brain_path = get_settings().brain_dir / chat_id
        logger.debug(f"[TaskToolSet] Using settings brain_dir: {brain_path}")
        return str(brain_path)

    @tool
    async def task_boundary(
        self,
        task_name: str,
        mode: str,
        task_summary: str,
        task_status: str,
        predicted_task_size: int,
        output_dir: str = "",
    ) -> dict:
        """
        CRITICAL: You must ALWAYS call this tool as the VERY FIRST tool in your list of tool calls, before any other tools.
        Indicate the start of a task or make an update to the current task. This should roughly correspond to the top-level items in your task.md.

        The tool should also be used to update the status and summary periodically throughout the task. When updating the status or summary of the current task, you must use the exact same task_name as before.

        To avoid repeating the same values, use the special string "%SAME%" for mode, task_name, task_status, task_summary, or output_dir to reuse the previous value.

        Args:
            task_name: Name of the task boundary. This is the identifier that groups steps together, should be human readable like 'Researching Existing Server Implementation'. This should correspond to a top-level item in task.md.
            mode: The agent focus to switch to. Common modes: PLANNING/EXECUTION/VERIFICATION (coding) or RESEARCH/ANALYSIS/INTERPRETATION (research).
            task_summary: Concise summary of what has been accomplished throughout the entire task so far. Should be at most 1-2 lines, past tense. Cite important files between backticks.
            task_status: Active status of the current action, e.g 'Looking for files'. Should describe what you are GOING TO DO NEXT, not what you have done.
            predicted_task_size: Your best estimation on how many tool calls are needed to fulfill this task.
            output_dir: The workspace-relative folder where THIS task's outputs (notebook, figures, data, reports) will live — e.g. 'scatac_pbmc5k' or 'scatac_pbmc5k/'. DECLARE THIS when you first set up the task (during PLANNING), even before the folder exists. The UI live-previews this folder so the user sees results appear as you produce them, before you formally register deliverables. Use "%SAME%" or omit to keep the previously declared folder. Leave empty only if this task produces no files.
        """
        # Handle %SAME% substitution
        task_name = self._last.get("task_name") if "%SAME%" in task_name else task_name
        mode = self._last.get("mode") if "%SAME%" in mode else mode
        task_summary = (
            self._last.get("summary") if "%SAME%" in task_summary else task_summary
        )
        task_status = self._last.get("status") if "%SAME%" in task_status else task_status
        output_dir = self._last.get("output_dir") if "%SAME%" in (output_dir or "") else output_dir

        # Validate mode: accept known modes, warn for unknown but allow
        if not mode or not mode.strip():
            return {"success": False, "error": "Mode cannot be empty"}

        mode_upper = mode.upper()
        if not ModeSemantics.is_known_mode(mode_upper):
            logger.warning(
                f"Unknown mode '{mode}', proceeding anyway. Known modes: {ModeSemantics.ALL_KNOWN_MODES}"
            )

        # Normalize (workspace-relative, no trailing slash). Empty/omitted ⇒ keep the
        # previously declared folder (self._last still holds the prior call's value).
        output_dir = (output_dir or "").strip().rstrip("/") or self._last.get("output_dir")

        # Store for next %SAME% reference
        self._last = {
            "task_name": task_name,
            "mode": mode_upper,
            "summary": task_summary,
            "status": task_status,
            "output_dir": output_dir,
        }

        # Decision-point GATE — one-time hard checkpoint before the FIRST execution.
        # Prompt + ephemeral nudges proved unreliable (the model would autopilot from
        # planning straight into execution, silently picking e.g. a dataset). So if
        # the agent enters an execute mode without ever having consulted the user,
        # fail this call ONCE: a tool error it must address forces a conscious "do I
        # need to confirm?" decision instead of barreling through. Fires once (never
        # loops), and for genuinely-specified tasks the agent simply re-calls and
        # proceeds — so the user is not interrupted on clear tasks.
        if (
            ModeSemantics.is_execute_mode(mode_upper)
            and not self.state.has_asked_user
            and not self.state.execution_gate_fired
        ):
            self.state.execution_gate_fired = True
            gate_context = self.get_context()
            if gate_context:
                self._save(self._get_brain_dir(gate_context))
            return {
                "success": False,
                "error": (
                    "DECISION CHECKPOINT — do not proceed yet. You are entering "
                    "execution without having consulted the user. If the request left "
                    "a consequential choice open, you MUST confirm first. In "
                    "particular, vague phrasing like 'find some data' / 'analyze "
                    "<topic>' with NO specific dataset/accession named IS "
                    "under-specified — picking the dataset is the user's call: "
                    "research candidates, then notify_user(blocked_on_user=true) with "
                    "your proposed dataset + 1-2 alternatives and WAIT for the user. "
                    "Only if the request already named the specific dataset/scope (or "
                    "the choice is trivial / easily reversible) may you call "
                    "task_boundary again now to proceed."
                ),
            }

        self.state.on_task_boundary(
            task_name, mode_upper, task_status, task_summary, output_dir=output_dir
        )

        # Persist state using context from toolset
        context = self.get_context()
        if context:
            brain_dir = self._get_brain_dir(context)
            self._save(brain_dir)

        return {"success": True, "mode": mode_upper, "task": task_name}

    @tool
    async def notify_user(
        self,
        paths_to_review: list[str],
        blocked_on_user: bool,
        message: str,
        confidence_justification: str,
        confidence_score: float,
        questions: list[dict] = [],
    ) -> dict:
        """
        This tool is used to communicate with the user.

        If you are currently in a task as set by the task_boundary tool, then this is the only way to communicate with the user. Other ways of sending messages while mid-task will not be visible.

        When sending messages, be very careful to make this as concise as possible. If requesting review, do not be redundant with the file you are asking to be reviewed.
        IMPORTANT: Format your message in github-style markdown to make your message easier for the USER to parse.

        CONFIDENCE GRADING: Before setting confidence_score, answer these 6 questions (Yes/No):
        (1) Gaps - any missing parts? (2) Assumptions - any unverified assumptions? (3) Complexity - complex logic with unknowns?
        (4) Risk - non-trivial interactions with bug risk? (5) Ambiguity - unclear requirements forcing design choices? (6) Irreversible - difficult to revert?
        SCORING: 0.8-1.0 = No to ALL questions; 0.5-0.7 = Yes to 1-2 questions; 0.0-0.4 = Yes to 3+ questions.

        IMPORTANT: This tool should NEVER be called in parallel with other tools. Execution control will be returned to the user once this tool is called.

        STRUCTURED QUESTIONS: You can include structured questions to gather specific user input beyond simple approval.
        Pass an empty list [] if you don't need questions. Pass a list of question dicts if you do.

        CRITICAL - When to use structured questions vs message text:
        - ✅ USE questions parameter: When you need user to SELECT from options or PROVIDE specific input
          Examples: "Which library?", "What port number?", "Which features to implement?"
        - ❌ DO NOT put questions in message text: Questions in message are NOT interactive and user cannot answer them directly
          The message field is for CONTEXT and EXPLANATION only, not for asking questions that need answers

        Rule: If you need an answer to proceed, use the questions parameter. If you just want to inform the user, use message.

        Args:
            paths_to_review: List of ABSOLUTE paths to files that the user should be notified about. MUST populate this if requesting review.
            blocked_on_user: Set to true if you are blocked on user approval to proceed. Set false if just notifying about completion.
                IMPORTANT: If you provide questions, the tool will automatically set interrupt=True regardless of this value,
                as asking questions implies waiting for answers. You typically should set this to True when providing questions.
            message: Required message to notify the user with, e.g to provide context or explanation. Use GitHub Flavored Markdown (GFM) format.
                IMPORTANT: Do NOT put questions in this field. Questions here are NOT interactive. Use the questions parameter instead.
            confidence_justification: Justification for the confidence score. MUST answer the 6 assessment questions with Yes/No.
            confidence_score: Agent's confidence from 0.0-1.0. MUST follow scoring rules above.
            questions: List of structured questions. REQUIRED parameter - pass [] if no questions needed.
                IMPORTANT: Questions in the message text will NOT create interactive prompts. Only questions in this parameter will be rendered as interactive UI elements.
                NOTE: Providing questions will automatically cause the tool to interrupt and wait for user response, even if blocked_on_user=False.
                Each question is a dict with:
                - question (str): The question text to ask the user
                - header (str): Short label for the question (max 12 chars), e.g. "Auth method", "Library"
                - input_type (str): Type of input - "single_choice", "multiple_choice", "text_input"
                - options (list[dict], required for choice types): List of options, each with:
                    - label (str): Display text for the option
                    - description (str): Explanation of what this option means
                    - value (str): Internal value to return when selected
                - placeholder (str, optional for text_input): Placeholder text
                - required (bool, optional): Whether this question must be answered (default: True)

        Returns:
            {
                "success": bool,
                "interrupt": bool,
                "message": str,
                "paths": list[str],
                "has_questions": bool,
                "questions": list[dict]
            }

        Examples:
            # No questions - just notification
            questions=[]

            # Single choice question
            questions=[{
                "question": "Which authentication method should we use?",
                "header": "Auth method",
                "input_type": "single_choice",
                "options": [
                    {"label": "JWT", "description": "JSON Web Tokens for stateless auth", "value": "jwt"},
                    {"label": "Session", "description": "Server-side session storage", "value": "session"},
                    {"label": "OAuth2", "description": "Third-party OAuth2 provider", "value": "oauth2"}
                ]
            }]

            # Multiple choice question
            questions=[{
                "question": "Which features should we implement first?",
                "header": "Features",
                "input_type": "multiple_choice",
                "options": [
                    {"label": "Login", "description": "Basic login functionality", "value": "login"},
                    {"label": "Registration", "description": "User registration flow", "value": "register"},
                    {"label": "Password Reset", "description": "Forgot password feature", "value": "reset"}
                ]
            }]

            # Text input question
            questions=[{
                "question": "What should we name the new API endpoint?",
                "header": "Endpoint",
                "input_type": "text_input",
                "placeholder": "e.g. /api/v1/users"
            }]

            # Mixed questions
            questions=[
                {
                    "question": "Which database should we use?",
                    "header": "Database",
                    "input_type": "single_choice",
                    "options": [
                        {"label": "PostgreSQL", "description": "Relational database", "value": "postgres"},
                        {"label": "MongoDB", "description": "Document database", "value": "mongo"}
                    ]
                },
                {
                    "question": "What port should the service run on?",
                    "header": "Port",
                    "input_type": "text_input",
                    "placeholder": "e.g. 8080"
                }
            ]
        """
        # Validate questions if provided
        if questions:
            if not isinstance(questions, list):
                return {
                    "success": False,
                    "error": "questions must be a list",
                }

            # No limit on number of questions - removed the 4-question restriction
            # Frontend can handle any number of questions with tab navigation

            for i, q in enumerate(questions):
                if not isinstance(q, dict):
                    return {
                        "success": False,
                        "error": f"Question {i+1} must be a dict",
                    }

                # Validate required fields
                if "question" not in q or "header" not in q or "input_type" not in q:
                    return {
                        "success": False,
                        "error": f"Question {i+1} missing required fields (question, header, input_type)",
                    }

                input_type = q["input_type"]
                if input_type not in ("single_choice", "multiple_choice", "text_input"):
                    return {
                        "success": False,
                        "error": f"Question {i+1} has invalid input_type: {input_type}",
                    }

                # Validate options for choice types
                if input_type in ("single_choice", "multiple_choice"):
                    if "options" not in q or not isinstance(q["options"], list):
                        return {
                            "success": False,
                            "error": f"Question {i+1} with {input_type} must have options list",
                        }

                    if len(q["options"]) < 1:
                        return {
                            "success": False,
                            "error": f"Question {i+1} must have at least 1 option",
                        }

                    for j, opt in enumerate(q["options"]):
                        if not isinstance(opt, dict):
                            return {
                                "success": False,
                                "error": f"Question {i+1} option {j+1} must be a dict",
                            }
                        if "label" not in opt or "description" not in opt or "value" not in opt:
                            return {
                                "success": False,
                                "error": f"Question {i+1} option {j+1} missing required fields (label, description, value)",
                            }

        if isinstance(paths_to_review, str):  # a lone path string would iterate char-by-char
            paths_to_review = [paths_to_review]
        self.state.on_notify_user(paths_to_review)

        # Persist state using context from toolset
        context = self.get_context()
        if context:
            brain_dir = self._get_brain_dir(context)
            self._save(brain_dir)

        # Auto-adjust interrupt behavior: if questions are provided, always interrupt
        # This ensures logical consistency - asking questions implies waiting for answers
        has_questions = len(questions) > 0
        actual_interrupt = blocked_on_user or has_questions

        return {
            "success": True,
            "interrupt": actual_interrupt,
            "message": message,
            "paths": paths_to_review,
            "has_questions": has_questions,
            "questions": questions,
        }

    def _resolve_output_path(self, path: str, context: dict):
        """Resolve an output path: check existence + normalize for storage.

        Relative paths resolve against the agent's effective workdir (the same
        root file_manager writes to), else cwd. Returns
        (abs_path, exists, is_dir, store_path) where store_path is normalized to
        a WORKSPACE-RELATIVE path when the file is under the root — so the UI
        renders a clean tree (`segmentation_outputs/`) instead of the full
        absolute path. Paths outside the root keep their original form.
        """
        import os

        # Prefer the immutable project anchor over `workdir` (which proxy_toolset
        # clears mid-run for endpoint calls — see _get_brain_dir). os.getcwd() is
        # the ChatRoom process dir, the wrong root, so it's only a last resort.
        root = context.get("project_root") or context.get("workdir") or os.getcwd()
        p = os.path.expanduser(path)
        abs_path = p if os.path.isabs(p) else os.path.join(root, p)
        exists = os.path.exists(abs_path)
        is_dir = os.path.isdir(abs_path) if exists else False

        store_path = path
        try:
            rel = os.path.relpath(abs_path, root)
            if not rel.startswith(".."):  # under the workspace root
                store_path = rel
        except ValueError:
            pass  # different drive (Windows) — keep original
        return abs_path, exists, is_dir, store_path

    @tool
    async def register_output(
        self,
        path: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        kind: Optional[str] = None,
    ) -> dict:
        """Register a user-facing deliverable so the user can find and browse it
        in the Output panel. Call this whenever you produce something the user
        should see.

        IMPORTANT: This is the ONLY way outputs become visible in the Output
        panel. In particular, files produced by RUNNING CODE (e.g. a plot saved
        by a script, a generated CSV) are NOT tracked anywhere else — you must
        register them here. Files you create with write_file should also be
        registered if they are deliverables (not scratch/intermediate files).

        Prefer organizing related deliverables into a folder and registering the
        FOLDER (its live contents are shown as a browsable tree) — then you don't
        have to register every file individually. You can also register single
        important files with a title/description.

        The output is automatically attributed to the task you are currently in
        (your most recent task_boundary), so the user sees which task produced
        what.

        Args:
            path: Path to the output file or directory (relative to your
                workspace, or absolute). MUST already exist — create/produce it
                first.
            title: Short human-readable title, e.g. "Hi-C contact matrix report".
            description: One-line description of what it is.
            kind: Category for grouping/display: 'report' | 'figure' | 'data' |
                'table' | 'dir' | 'other'. Defaults to 'dir' for directories,
                else 'other'.
        """
        context = self.get_context() or {}
        _abs, exists, is_dir, store_path = self._resolve_output_path(path, context)
        if not exists:
            return {
                "success": False,
                "error": f"Path does not exist: {path}. Create/produce it before registering.",
            }

        # Lazy-load so we accumulate onto any previously persisted outputs.
        brain_dir = self._get_brain_dir(context)
        self._load(brain_dir)
        self.state.on_register_output(
            path=store_path, title=title, description=description, kind=kind, is_dir=is_dir
        )
        self._save(brain_dir)
        return {"success": True, "path": store_path, "is_dir": is_dir}

    @tool
    async def list_outputs(self) -> dict:
        """List the registered output artifacts for the current conversation.

        Returns every output registered via register_output, each tagged with
        the task that produced it. Reads the persisted task state directly so it
        is reliable regardless of which agent instance handles the call and for
        conversations loaded from history.
        """
        context = self.get_context() or {}
        brain_dir = self._get_brain_dir(context)
        path = Path(brain_dir) / self.STATE_FILE
        if not path.exists():
            return {"success": True, "outputs": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            outputs = (data.get("state") or {}).get("outputs", [])
            return {"success": True, "outputs": outputs}
        except Exception as e:
            logger.warning(f"[TaskToolSet] Failed to read outputs: {e}")
            return {"success": False, "error": str(e), "outputs": []}

    def get_ephemeral_prompt(self, context_variables: dict) -> dict:
        """Generate EU message for agent loop to inject before LLM call.

        Args:
            context_variables: Agent context variables, should contain 'client_id'

        Returns a dict with:
        - content: The EU message content
        - role: "user"
        """
        brain_dir = self._get_brain_dir(context_variables)

        # Lazy load state from disk on first call
        self._load(brain_dir)

        eu_content = generate_ephemeral_message(self.state, brain_dir)

        # Debug logging
        logger.debug(f"[TaskToolSet] Generating EU for brain_dir={brain_dir}")
        logger.debug(
            f"[TaskToolSet] State: active_task={self.state.active_task}, "
            f"artifacts={self.state.created_artifacts}, "
            f"tools_since_boundary={self.state.tools_since_boundary}, "
            f"current_step={self.state.current_step}"
        )
        logger.debug(f"[TaskToolSet] EU Content:\n{eu_content}")

        disclaimer = """The following is an <EPHEMERAL_MESSAGE> not actually sent by the user. It is provided by the system as a set of reminders and general important information to pay attention to. Do NOT respond to this message, just act accordingly."""
        return {"role": "user", "content": f"{disclaimer}\n{eu_content}"}

    def process_tool_messages(
        self, tool_calls: list[dict], tool_messages: list[dict], context_variables: dict
    ):
        """Process tool messages to detect artifact access and think tool usage.

        Called by agent after _handle_tool_calls completes.
        Detects file_manager tool calls that access artifact files.
        Detects think tool usage to reset think counter.

        Args:
            tool_calls: Original tool calls from LLM
            tool_messages: Tool response messages
            context_variables: Agent context variables
        """
        brain_dir = self._get_brain_dir(context_variables)

        # 先检测 think tool 使用，并分离非 think 工具
        has_think = False
        non_think_tools = []

        for call in tool_calls:
            tool_name = call.get("function", {}).get("name", "")
            if tool_name == "think":
                has_think = True
                self.state.on_think_tool_used()
                logger.debug(f"[TaskToolSet] Think tool used at step {self.state.current_step}")
            else:
                non_think_tools.append(call)

        # 更新工具计数（不包括 think）
        if non_think_tools:
            self.state.on_tool_call(len(non_think_tools))

        # Detect artifact access via file_manager tools
        FILE_TOOLS = ("read_file", "write_file", "update_file", "view_file")

        for msg in tool_messages:
            tool_name = msg.get("tool_name", "")

            # Check for exact match or provider-prefixed match (e.g. file_manager__write_file)
            is_file_tool = tool_name in FILE_TOOLS or any(
                tool_name.endswith(f"__{t}") for t in FILE_TOOLS
            )

            if not is_file_tool:
                continue

            # Find corresponding tool call to get file_path argument
            tool_call_id = msg.get("tool_call_id")
            call = next((c for c in tool_calls if c["id"] == tool_call_id), None)
            if not call:
                continue

            try:
                args = json.loads(call["function"]["arguments"])
                file_path = args.get("file_path", "")

                # Check if path is in brain_dir (artifact file)
                if brain_dir in file_path:
                    self.state.on_artifact_modified(file_path, brain_dir)

                    # Also register as created if new
                    if file_path not in self.state.created_artifacts:
                        self.state.on_artifact_created(file_path)
            except (json.JSONDecodeError, KeyError):
                continue

        # Persist state after processing
        self._save(brain_dir)
