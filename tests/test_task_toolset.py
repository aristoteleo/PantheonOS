"""Tests for Modal Workflow TaskToolSet."""
import pytest
import asyncio
from pathlib import Path

# Direct imports to test each component
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestModeSemantics:
    """Test ModeSemantics class for multi-scenario support."""
    
    def test_plan_modes(self):
        from pantheon.toolsets.task.task_state import ModeSemantics
        
        # All plan modes should return True
        assert ModeSemantics.is_plan_mode("PLANNING") is True
        assert ModeSemantics.is_plan_mode("RESEARCH") is True
        assert ModeSemantics.is_plan_mode("DESIGN") is True
        
        # Case insensitive
        assert ModeSemantics.is_plan_mode("planning") is True
        assert ModeSemantics.is_plan_mode("Research") is True
        
        # Non-plan modes should return False
        assert ModeSemantics.is_plan_mode("EXECUTION") is False
        assert ModeSemantics.is_plan_mode("ANALYSIS") is False
    
    def test_execute_modes(self):
        from pantheon.toolsets.task.task_state import ModeSemantics
        
        assert ModeSemantics.is_execute_mode("EXECUTION") is True
        assert ModeSemantics.is_execute_mode("ANALYSIS") is True
        assert ModeSemantics.is_execute_mode("IMPLEMENTATION") is True
        
        assert ModeSemantics.is_execute_mode("PLANNING") is False
        assert ModeSemantics.is_execute_mode("VERIFICATION") is False
    
    def test_verify_modes(self):
        from pantheon.toolsets.task.task_state import ModeSemantics
        
        assert ModeSemantics.is_verify_mode("VERIFICATION") is True
        assert ModeSemantics.is_verify_mode("INTERPRETATION") is True
        assert ModeSemantics.is_verify_mode("TESTING") is True
        
        assert ModeSemantics.is_verify_mode("PLANNING") is False
        assert ModeSemantics.is_verify_mode("EXECUTION") is False
    
    def test_known_modes(self):
        from pantheon.toolsets.task.task_state import ModeSemantics
        
        # All modes in any group should be known
        assert ModeSemantics.is_known_mode("PLANNING") is True
        assert ModeSemantics.is_known_mode("RESEARCH") is True
        assert ModeSemantics.is_known_mode("EXECUTION") is True
        assert ModeSemantics.is_known_mode("ANALYSIS") is True
        assert ModeSemantics.is_known_mode("VERIFICATION") is True
        assert ModeSemantics.is_known_mode("INTERPRETATION") is True
        
        # Unknown modes
        assert ModeSemantics.is_known_mode("CUSTOM") is False
        assert ModeSemantics.is_known_mode("UNKNOWN") is False


class TestArtifactRoles:
    """Test ArtifactRoles class for artifact detection."""
    
    def test_get_role_plan_artifacts(self):
        from pantheon.toolsets.task.task_state import ArtifactRoles
        
        assert ArtifactRoles.get_role("implementation_plan.md") == "plan"
        assert ArtifactRoles.get_role("research_plan.md") == "plan"
        assert ArtifactRoles.get_role("plan.md") == "plan"
        assert ArtifactRoles.get_role("/path/to/implementation_plan.md") == "plan"
    
    def test_get_role_summary_artifacts(self):
        from pantheon.toolsets.task.task_state import ArtifactRoles
        
        assert ArtifactRoles.get_role("walkthrough.md") == "summary"
        assert ArtifactRoles.get_role("analysis_log.md") == "summary"
        assert ArtifactRoles.get_role("summary.md") == "summary"
    
    def test_get_role_other_artifacts(self):
        from pantheon.toolsets.task.task_state import ArtifactRoles
        
        assert ArtifactRoles.get_role("task.md") == "task"
        assert ArtifactRoles.get_role("hypothesis_tracker.md") == "tracker"
    
    def test_get_role_unknown(self):
        from pantheon.toolsets.task.task_state import ArtifactRoles
        
        assert ArtifactRoles.get_role("custom.md") is None
        assert ArtifactRoles.get_role("notes.md") is None
    
    def test_is_plan_artifact(self):
        from pantheon.toolsets.task.task_state import ArtifactRoles
        
        assert ArtifactRoles.is_plan_artifact("/brain/x/implementation_plan.md") is True
        assert ArtifactRoles.is_plan_artifact("/brain/x/research_plan.md") is True
        assert ArtifactRoles.is_plan_artifact("/brain/x/task.md") is False
    
    def test_is_artifact_generic(self):
        from pantheon.toolsets.task.task_state import ArtifactRoles
        
        brain_dir = "/brain/test"
        
        # Files in brain_dir with .md extension are artifacts
        assert ArtifactRoles.is_artifact("/brain/test/custom.md", brain_dir) is True
        assert ArtifactRoles.is_artifact("/brain/test/notes.md", brain_dir) is True
        
        # Files outside brain_dir or non-.md are not
        assert ArtifactRoles.is_artifact("/other/path/file.md", brain_dir) is False
        assert ArtifactRoles.is_artifact("/brain/test/script.py", brain_dir) is False


class TestTaskInfo:
    """Test TaskInfo with semantic phase detection."""
    
    def test_plan_phase_detection(self):
        from pantheon.toolsets.task.task_state import TaskInfo
        
        # PLANNING mode
        task = TaskInfo(name="Test", mode="PLANNING", status="", summary="")
        assert task.is_plan_phase is True
        assert task.is_execute_phase is False
        assert task.is_verify_phase is False
        
        # RESEARCH mode (same semantic group as PLANNING)
        task = TaskInfo(name="Test", mode="RESEARCH", status="", summary="")
        assert task.is_plan_phase is True
        assert task.is_execute_phase is False
    
    def test_execute_phase_detection(self):
        from pantheon.toolsets.task.task_state import TaskInfo
        
        # EXECUTION mode
        task = TaskInfo(name="Test", mode="EXECUTION", status="", summary="")
        assert task.is_execute_phase is True
        assert task.is_plan_phase is False
        
        # ANALYSIS mode (same semantic group as EXECUTION)
        task = TaskInfo(name="Test", mode="ANALYSIS", status="", summary="")
        assert task.is_execute_phase is True
        assert task.is_plan_phase is False
    
    def test_verify_phase_detection(self):
        from pantheon.toolsets.task.task_state import TaskInfo
        
        # VERIFICATION mode
        task = TaskInfo(name="Test", mode="VERIFICATION", status="", summary="")
        assert task.is_verify_phase is True
        
        # INTERPRETATION mode (same semantic group as VERIFICATION)
        task = TaskInfo(name="Test", mode="INTERPRETATION", status="", summary="")
        assert task.is_verify_phase is True


class TestConversationState:
    """Test ConversationState dataclass."""
    
    def test_initial_state(self):
        from pantheon.toolsets.task.task_state import ConversationState
        state = ConversationState()
        
        assert state.active_task is None
        assert state.created_artifacts == []
        assert state.tools_since_boundary == 0
        assert state.current_step == 0
        assert state.artifacts_modified_in_task == {}
        
    def test_on_task_boundary(self):
        from pantheon.toolsets.task.task_state import ConversationState
        state = ConversationState()
        
        state.on_task_boundary("Test Task", "PLANNING", "Looking for files", "Started.")
        
        assert state.active_task is not None
        assert state.active_task.name == "Test Task"
        assert state.active_task.mode == "PLANNING"
        assert state.tools_since_boundary == 0
    
    def test_on_task_boundary_research_mode(self):
        """Test task boundary with RESEARCH mode."""
        from pantheon.toolsets.task.task_state import ConversationState
        state = ConversationState()
        
        state.on_task_boundary("Research Task", "RESEARCH", "Analyzing", "Started.")
        
        assert state.active_task.mode == "RESEARCH"
        assert state.active_task.is_plan_phase is True
        
    def test_on_tool_call(self):
        from pantheon.toolsets.task.task_state import ConversationState
        state = ConversationState()
        
        state.on_tool_call(3)
        assert state.tools_since_boundary == 3
        assert state.current_step == 3
        
        state.on_tool_call(2)
        assert state.tools_since_boundary == 5
        assert state.current_step == 5
        
    def test_on_artifact_created(self):
        from pantheon.toolsets.task.task_state import ConversationState
        state = ConversationState()
        
        state.on_artifact_created("/path/to/task.md")
        assert "/path/to/task.md" in state.created_artifacts
        assert state.artifact_last_access["/path/to/task.md"] == 0
        
    def test_on_notify_user(self):
        from pantheon.toolsets.task.task_state import ConversationState
        state = ConversationState()
        
        state.on_task_boundary("Test", "PLANNING", "Status", "Summary")
        state.on_notify_user(["/path/to/plan.md"])
        
        assert state.active_task is None
        assert state.pending_review_paths == ["/path/to/plan.md"]
    
    def test_on_artifact_modified_tracking(self):
        """Test artifact modification tracking by role."""
        from pantheon.toolsets.task.task_state import ConversationState
        state = ConversationState()
        brain_dir = "/brain/test"
        
        state.on_task_boundary("Test", "RESEARCH", "Status", "Summary")
        state.on_artifact_modified(f"{brain_dir}/research_plan.md", brain_dir)
        
        assert state.has_plan_artifacts_modified() is True
        assert "plan" in state.artifacts_modified_in_task
        assert f"{brain_dir}/research_plan.md" in state.artifacts_modified_in_task["plan"]
        # Backward compatibility
        assert state.plan_edited_in_planning is True
    
    def test_on_artifact_modified_multiple_roles(self):
        """Test tracking artifacts of different roles."""
        from pantheon.toolsets.task.task_state import ConversationState
        state = ConversationState()
        brain_dir = "/brain/test"
        
        state.on_task_boundary("Test", "ANALYSIS", "Status", "Summary")
        state.on_artifact_modified(f"{brain_dir}/task.md", brain_dir)
        state.on_artifact_modified(f"{brain_dir}/analysis_log.md", brain_dir)
        state.on_artifact_modified(f"{brain_dir}/custom.md", brain_dir)
        
        assert "task" in state.artifacts_modified_in_task
        assert "summary" in state.artifacts_modified_in_task
        assert "other" in state.artifacts_modified_in_task
        
        all_modified = state.get_all_modified_artifacts()
        assert len(all_modified) == 3
    
    def test_new_task_resets_modifications(self):
        """Test that starting a new task resets modification tracking."""
        from pantheon.toolsets.task.task_state import ConversationState
        state = ConversationState()
        brain_dir = "/brain/test"
        
        state.on_task_boundary("Task 1", "PLANNING", "Status", "Summary")
        state.on_artifact_modified(f"{brain_dir}/plan.md", brain_dir)
        assert state.has_plan_artifacts_modified() is True
        
        # New task with different name should reset
        state.on_task_boundary("Task 2", "EXECUTION", "Status", "Summary")
        assert state.has_plan_artifacts_modified() is False
        assert state.artifacts_modified_in_task == {}


class TestEphemeralMessage:
    """Test ephemeral message generation."""
    
    def test_no_artifacts_no_task(self):
        from pantheon.toolsets.task.task_state import ConversationState
        from pantheon.toolsets.task.ephemeral import generate_ephemeral_message
        
        state = ConversationState()
        msg = generate_ephemeral_message(state, ".pantheon/brain/test")
        
        assert "<EPHEMERAL_MESSAGE>" in msg
        assert "<artifact_reminder>" in msg
        assert "<no_active_task_reminder>" in msg
        assert "You have not yet created any artifacts" in msg
        
    def test_with_task(self):
        from pantheon.toolsets.task.task_state import ConversationState
        from pantheon.toolsets.task.ephemeral import generate_ephemeral_message
        
        state = ConversationState()
        state.on_task_boundary("Test Task", "EXECUTION", "Building", "Completed design.")
        
        msg = generate_ephemeral_message(state, ".pantheon/brain/test")
        
        assert "<active_task_reminder>" in msg
        assert "Test Task" in msg
        assert "EXECUTION" in msg
        assert "<no_active_task_reminder>" not in msg
    
    def test_with_research_mode(self):
        """Test ephemeral message with RESEARCH mode."""
        from pantheon.toolsets.task.task_state import ConversationState
        from pantheon.toolsets.task.ephemeral import generate_ephemeral_message
        
        state = ConversationState()
        state.on_task_boundary("Research Task", "RESEARCH", "Analyzing", "Started.")
        
        msg = generate_ephemeral_message(state, ".pantheon/brain/test")
        
        assert "RESEARCH" in msg
        assert "<active_task_reminder>" in msg
    
    def test_plan_artifact_modified_reminder(self):
        """Test plan artifact modified reminder in plan phase."""
        from pantheon.toolsets.task.task_state import ConversationState
        from pantheon.toolsets.task.ephemeral import generate_ephemeral_message
        
        brain_dir = "/brain/test"
        state = ConversationState()
        state.on_task_boundary("Research", "RESEARCH", "Planning", "Started.")
        state.on_artifact_modified(f"{brain_dir}/research_plan.md", brain_dir)
        
        msg = generate_ephemeral_message(state, brain_dir)
        
        assert "<plan_artifact_modified_reminder>" in msg
        assert "research_plan.md" in msg
        assert "RESEARCH" in msg
    
    def test_artifacts_modified_reminder_non_plan_phase(self):
        """Test artifacts modified reminder in non-plan phase."""
        from pantheon.toolsets.task.task_state import ConversationState
        from pantheon.toolsets.task.ephemeral import generate_ephemeral_message
        
        brain_dir = "/brain/test"
        state = ConversationState()
        state.on_task_boundary("Analysis", "ANALYSIS", "Running", "Started.")
        state.on_artifact_modified(f"{brain_dir}/analysis_log.md", brain_dir)
        
        msg = generate_ephemeral_message(state, brain_dir)
        
        assert "<artifacts_modified_reminder>" in msg
        assert "1 artifact(s)" in msg
        # Should NOT have plan_artifact_modified_reminder (not in plan phase)
        assert "<plan_artifact_modified_reminder>" not in msg
        
    def test_stale_artifact_reminder(self):
        from pantheon.toolsets.task.task_state import ConversationState
        from pantheon.toolsets.task.ephemeral import generate_ephemeral_message
        
        state = ConversationState()
        state.on_artifact_created("/path/to/old.md")
        state.on_tool_call(15)  # Make it stale (> 10 steps)
        
        msg = generate_ephemeral_message(state, ".pantheon/brain/test")

        assert "<artifact_file_reminder>" in msg
        assert "/path/to/old.md" in msg


class TestConfirmOpenChoicesReminder:
    """During PLANNING, before the agent has asked anything, the EM must nudge it to
    confirm under-specified choices (which dataset/method) — the stronger lever for
    'agent picked the dataset without asking'. Self-limits once it asks / leaves plan."""

    def test_fires_when_planning_and_not_yet_asked(self, tmp_path):
        from pantheon.toolsets.task.ephemeral import generate_ephemeral_message
        from pantheon.toolsets.task.task_state import ConversationState
        s = ConversationState()
        s.on_task_boundary("Plan it", "PLANNING", "status", "summary")
        em = generate_ephemeral_message(s, str(tmp_path))
        assert "<confirm_open_choices>" in em
        assert "find some data" in em   # the loophole it must not rationalize past

    def test_silent_once_review_requested(self, tmp_path):
        from pantheon.toolsets.task.ephemeral import generate_ephemeral_message
        from pantheon.toolsets.task.task_state import ConversationState
        s = ConversationState()
        s.on_task_boundary("Plan it", "PLANNING", "status", "summary")
        s.pending_review_paths = ["/x/plan.md"]   # agent already asked the user
        em = generate_ephemeral_message(s, str(tmp_path))
        assert "<confirm_open_choices>" not in em

    def test_silent_in_execution_phase(self, tmp_path):
        from pantheon.toolsets.task.ephemeral import generate_ephemeral_message
        from pantheon.toolsets.task.task_state import ConversationState
        s = ConversationState()
        s.on_task_boundary("Do it", "EXECUTION", "status", "summary")
        em = generate_ephemeral_message(s, str(tmp_path))
        assert "<confirm_open_choices>" not in em   # only the plan/research window


class TestTaskToolSet:
    """Test TaskToolSet integration."""
    
    @pytest.mark.asyncio
    async def test_task_boundary_tool(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        
        ts = TaskToolSet()
        result = await ts.task_boundary(
            task_name="Test",
            mode="PLANNING",
            task_summary="Testing",
            task_status="Running test",
            predicted_task_size=5
        )
        
        assert result["success"] is True
        assert result["mode"] == "PLANNING"
        assert ts.state.active_task.name == "Test"
    
    @pytest.mark.asyncio
    async def test_task_boundary_research_mode(self):
        """Test task_boundary accepts RESEARCH mode."""
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        
        ts = TaskToolSet()
        result = await ts.task_boundary(
            task_name="Research Task",
            mode="RESEARCH",
            task_summary="Researching",
            task_status="Gathering info",
            predicted_task_size=10
        )
        
        assert result["success"] is True
        assert result["mode"] == "RESEARCH"
        assert ts.state.active_task.is_plan_phase is True
    
    @pytest.mark.asyncio
    async def test_task_boundary_analysis_mode(self):
        """Test task_boundary accepts ANALYSIS mode."""
        from pantheon.toolsets.task.task_toolset import TaskToolSet

        ts = TaskToolSet()
        ts.state.has_asked_user = True   # satisfy the pre-execution gate (tested separately)
        result = await ts.task_boundary(
            task_name="Analysis Task",
            mode="ANALYSIS",
            task_summary="Analyzing",
            task_status="Processing data",
            predicted_task_size=15
        )
        
        assert result["success"] is True
        assert result["mode"] == "ANALYSIS"
        assert ts.state.active_task.is_execute_phase is True
    
    @pytest.mark.asyncio
    async def test_task_boundary_interpretation_mode(self):
        """Test task_boundary accepts INTERPRETATION mode."""
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        
        ts = TaskToolSet()
        result = await ts.task_boundary(
            task_name="Interpretation Task",
            mode="INTERPRETATION",
            task_summary="Interpreting",
            task_status="Drawing conclusions",
            predicted_task_size=5
        )
        
        assert result["success"] is True
        assert result["mode"] == "INTERPRETATION"
        assert ts.state.active_task.is_verify_phase is True
    
    @pytest.mark.asyncio
    async def test_task_boundary_empty_mode_fails(self):
        """Test that empty mode fails."""
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        
        ts = TaskToolSet()
        result = await ts.task_boundary(
            task_name="Test",
            mode="",
            task_summary="Testing",
            task_status="Running",
            predicted_task_size=5
        )
        
        assert result["success"] is False
        assert "empty" in result["error"].lower()
    
    @pytest.mark.asyncio
    async def test_task_boundary_unknown_mode_warns_but_succeeds(self):
        """Test that unknown mode warns but still succeeds."""
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        
        ts = TaskToolSet()
        result = await ts.task_boundary(
            task_name="Test",
            mode="CUSTOM_MODE",
            task_summary="Testing",
            task_status="Running",
            predicted_task_size=5
        )
        
        # Should succeed but log warning
        assert result["success"] is True
        assert result["mode"] == "CUSTOM_MODE"
    
    @pytest.mark.asyncio
    async def test_task_boundary_mode_case_normalization(self):
        """Test that mode is normalized to uppercase."""
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        
        ts = TaskToolSet()
        result = await ts.task_boundary(
            task_name="Test",
            mode="research",  # lowercase
            task_summary="Testing",
            task_status="Running",
            predicted_task_size=5
        )
        
        assert result["success"] is True
        assert result["mode"] == "RESEARCH"  # Normalized to uppercase
        
    @pytest.mark.asyncio
    async def test_same_substitution(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        
        ts = TaskToolSet()
        await ts.task_boundary("Initial", "PLANNING", "Summary", "Status", 5)
        
        result = await ts.task_boundary("%SAME%", "%SAME%", "New summary", "%SAME%", 3)
        
        assert result["task"] == "Initial"
        assert result["mode"] == "PLANNING"
        
    @pytest.mark.asyncio
    async def test_notify_user_interrupt(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        
        ts = TaskToolSet()
        result = await ts.notify_user(
            paths_to_review=["/path/to/plan.md"],
            blocked_on_user=True,
            message="Please review",
            confidence_justification="All No",
            confidence_score=0.9
        )
        
        assert result["success"] is True
        assert result["interrupt"] is True
        
    def test_get_ephemeral_prompt(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        
        ts = TaskToolSet()
        eu = ts.get_ephemeral_prompt({"client_id": "test123"})
        
        assert eu["role"] == "user"
        assert "<EPHEMERAL_MESSAGE>" in eu["content"]


class TestBrainDirAnchor:
    """The brain dir must anchor to the IMMUTABLE `project_root`, not the mutable
    `workdir`. proxy_toolset pops/overwrites `workdir` on the shared per-task
    context for every endpoint-toolset call (steering the endpoint's cwd); if the
    brain followed `workdir`, the first notebook/shell call would relocate the
    task brain to the global home dir mid-run — splitting brain from workspace.
    """

    def test_prefers_project_root_over_workdir(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        # workdir is the (transient) endpoint hint; project_root is the anchor.
        brain = ts._get_brain_dir({
            "chat_id": "c1",
            "project_root": "/Users/me/Desktop/tmp",
            "workdir": "/some/endpoint/override",
        })
        assert brain == "/Users/me/Desktop/tmp/.pantheon/brain/c1"

    def test_survives_workdir_being_popped(self):
        """The real failure mode: a later turn whose context has had `workdir`
        popped by proxy_toolset still resolves to the project, not global home."""
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        brain = ts._get_brain_dir({"chat_id": "c1", "project_root": "/proj"})  # no workdir
        assert brain == "/proj/.pantheon/brain/c1"

    def test_falls_back_to_workdir_when_no_project_root(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        brain = ts._get_brain_dir({"chat_id": "c1", "workdir": "/legacy/iso"})
        assert brain == "/legacy/iso/.pantheon/brain/c1"

    def test_falls_back_to_settings_when_no_root(self):
        from unittest.mock import patch, MagicMock
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        fake = MagicMock()
        fake.brain_dir = Path("/home/.pantheon/brain")
        with patch("pantheon.settings.get_settings", return_value=fake):
            brain = ts._get_brain_dir({"chat_id": "c1"})
        assert brain == str(Path("/home/.pantheon/brain/c1"))

    def test_resolve_output_path_uses_project_root(self):
        """register_output's path resolution shares the same anchor — a relative
        deliverable must resolve under the project, never os.getcwd()."""
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        abs_path, _exists, _is_dir, store_path = ts._resolve_output_path(
            "outputs/fig.png",
            {"project_root": "/Users/me/Desktop/tmp"},  # workdir absent (popped)
        )
        assert abs_path == "/Users/me/Desktop/tmp/outputs/fig.png"
        assert store_path == "outputs/fig.png"  # workspace-relative for the tree


class TestTaskOutputDir:
    """task_boundary declares the task's output folder up front (at PLANNING), so
    the UI can live-preview that folder before the agent registers deliverables."""

    @pytest.mark.asyncio
    async def test_records_declared_output_dir(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        await ts.task_boundary("scATAC", "PLANNING", "sum", "status", 5,
                               output_dir="scatac_pbmc5k/")
        assert ts.state.active_task.output_dir == "scatac_pbmc5k"   # trailing slash stripped
        assert ts.state.task_dirs["scATAC"] == "scatac_pbmc5k"

    @pytest.mark.asyncio
    async def test_output_dir_persists_when_omitted_on_update(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        await ts.task_boundary("scATAC", "PLANNING", "s", "st", 5, output_dir="scatac_pbmc5k")
        # A later update of the SAME task omits output_dir → keeps the declared one.
        await ts.task_boundary("scATAC", "EXECUTION", "s2", "st2", 5)
        assert ts.state.active_task.output_dir == "scatac_pbmc5k"
        assert ts.state.task_dirs["scATAC"] == "scatac_pbmc5k"

    @pytest.mark.asyncio
    async def test_output_dir_same_sentinel_reuses(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        await ts.task_boundary("scATAC", "PLANNING", "s", "st", 5, output_dir="scatac_pbmc5k")
        await ts.task_boundary("%SAME%", "%SAME%", "%SAME%", "%SAME%", 5, output_dir="%SAME%")
        assert ts.state.active_task.output_dir == "scatac_pbmc5k"

    @pytest.mark.asyncio
    async def test_second_task_gets_its_own_dir(self):
        """Two tasks in one chat → two folders, keyed by task (drives 'By task')."""
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        await ts.task_boundary("scATAC", "PLANNING", "s", "st", 5, output_dir="scatac_pbmc5k")
        await ts.task_boundary("Visium", "PLANNING", "s", "st", 5, output_dir="visium_qc")
        assert ts.state.task_dirs == {"scATAC": "scatac_pbmc5k", "Visium": "visium_qc"}

    def test_state_roundtrip_preserves_dirs(self):
        from pantheon.toolsets.task.task_state import ConversationState
        s = ConversationState()
        s.on_task_boundary("T", "PLANNING", "st", "sum", output_dir="folder_a")
        restored = ConversationState.from_dict(s.to_dict())
        assert restored.task_dirs == {"T": "folder_a"}
        assert restored.active_task.output_dir == "folder_a"

    def test_backward_compat_old_state_without_dirs(self):
        """Pre-output_dir task_state.json must still load cleanly."""
        from pantheon.toolsets.task.task_state import ConversationState
        old = {
            "active_task": {"name": "T", "mode": "PLANNING", "status": "s",
                            "summary": "x", "start_step": 3},
            "outputs": [],
        }
        s = ConversationState.from_dict(old)
        assert s.task_dirs == {}
        assert s.active_task.output_dir is None


class TestExecutionGate:
    """One-time hard checkpoint: the FIRST entry into an execute mode WITHOUT having
    consulted the user fails (a tool error the agent must address), forcing a
    conscious confirm-or-proceed decision. Fires once (never loops); permanently
    satisfied once the agent calls notify_user. This is the reliable backstop after
    prompt + ephemeral nudges proved unreliable."""

    @pytest.mark.asyncio
    async def test_gates_first_execution_when_user_not_asked(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        r = await ts.task_boundary("Analyze", "EXECUTION", "sum", "status", 5)
        assert r["success"] is False
        assert "CHECKPOINT" in r["error"]
        assert ts.state.active_task is None          # blocked → did not transition
        assert ts.state.execution_gate_fired is True

    @pytest.mark.asyncio
    async def test_proceeds_on_retry_after_gate(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        await ts.task_boundary("Analyze", "EXECUTION", "s", "st", 5)      # gated (fires once)
        r = await ts.task_boundary("Analyze", "EXECUTION", "s", "st", 5)  # retry → passes
        assert r["success"] is True
        assert ts.state.active_task.mode == "EXECUTION"

    @pytest.mark.asyncio
    async def test_not_gated_after_notify_user(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        await ts.notify_user(
            paths_to_review=[], blocked_on_user=True, message="Which dataset?",
            confidence_justification="x", confidence_score=0.5, questions=[],
        )
        assert ts.state.has_asked_user is True
        r = await ts.task_boundary("Analyze", "EXECUTION", "s", "st", 5)
        assert r["success"] is True                  # already consulted the user

    @pytest.mark.asyncio
    async def test_planning_is_never_gated(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        r = await ts.task_boundary("Plan", "PLANNING", "s", "st", 5)
        assert r["success"] is True
        assert ts.state.execution_gate_fired is False

    @pytest.mark.asyncio
    async def test_analysis_mode_also_gated(self):
        from pantheon.toolsets.task.task_toolset import TaskToolSet
        ts = TaskToolSet()
        r = await ts.task_boundary("Analyze", "ANALYSIS", "s", "st", 5)   # ANALYSIS = execute phase
        assert r["success"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
