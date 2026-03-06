"""Task state management for Modal Workflow System."""
import os
from dataclasses import dataclass, field, asdict, fields
from typing import Optional


class ModeSemantics:
    """Mode semantic grouping for multi-scenario support.
    
    Groups different mode names into semantic categories:
    - plan: Planning/Research phase (design, research, hypothesis)
    - execute: Execution/Analysis phase (implementation, analysis)
    - verify: Verification/Interpretation phase (testing, interpretation)
    """
    
    # Semantic groups
    PLAN_MODES = frozenset(["PLANNING", "RESEARCH", "DESIGN"])
    EXECUTE_MODES = frozenset(["EXECUTION", "ANALYSIS", "IMPLEMENTATION"])
    VERIFY_MODES = frozenset(["VERIFICATION", "INTERPRETATION", "TESTING", "REVIEW"])
    
    # All known modes
    ALL_KNOWN_MODES = PLAN_MODES | EXECUTE_MODES | VERIFY_MODES
    
    @classmethod
    def is_plan_mode(cls, mode: str) -> bool:
        """Check if mode is a planning/research phase."""
        return mode.upper() in cls.PLAN_MODES
    
    @classmethod
    def is_execute_mode(cls, mode: str) -> bool:
        """Check if mode is an execution/analysis phase."""
        return mode.upper() in cls.EXECUTE_MODES
    
    @classmethod
    def is_verify_mode(cls, mode: str) -> bool:
        """Check if mode is a verification/interpretation phase."""
        return mode.upper() in cls.VERIFY_MODES
    
    @classmethod
    def is_known_mode(cls, mode: str) -> bool:
        """Check if mode is a known/recognized mode."""
        return mode.upper() in cls.ALL_KNOWN_MODES


class ArtifactRoles:
    """Artifact role detection for multi-scenario support.
    
    Identifies artifact files by their semantic role:
    - task: Task tracking checklist (task.md)
    - plan: Planning/research documents (implementation_plan.md, research_plan.md)
    - summary: Summary/log documents (walkthrough.md, analysis_log.md)
    - tracker: Tracking documents (hypothesis_tracker.md)
    """
    
    # Role to filename patterns mapping
    ROLE_PATTERNS = {
        "task": ["task.md"],
        "plan": [
            "implementation_plan.md",
            "research_plan.md",
            "plan.md",
        ],
        "summary": [
            "walkthrough.md",
            "analysis_log.md",
            "summary.md",
            "report.md",
        ],
        "tracker": [
            "hypothesis_tracker.md",
        ],
    }
    
    @classmethod
    def get_role(cls, path: str) -> str | None:
        """Get the semantic role of an artifact by path.
        
        Args:
            path: File path to check
            
        Returns:
            Role name ('task', 'plan', 'summary', 'tracker') or None
        """
        filename = os.path.basename(path)
        for role, patterns in cls.ROLE_PATTERNS.items():
            if filename in patterns:
                return role
        return None
    
    @classmethod
    def is_plan_artifact(cls, path: str) -> bool:
        """Check if path is a plan-type artifact."""
        return cls.get_role(path) == "plan"
    
    @classmethod
    def is_artifact(cls, path: str, brain_dir: str) -> bool:
        """Check if path is any artifact (based on brain_dir location).
        
        Args:
            path: File path to check
            brain_dir: Brain directory path
            
        Returns:
            True if path is within brain_dir and ends with .md
        """
        return brain_dir in path and path.endswith(".md")


@dataclass
class TaskInfo:
    """Current task information."""
    name: str
    mode: str  # Any mode string (PLANNING, RESEARCH, EXECUTION, ANALYSIS, etc.)
    status: str
    summary: str
    start_step: int = 0
    
    @property
    def is_plan_phase(self) -> bool:
        """Check if current mode is a planning/research phase."""
        return ModeSemantics.is_plan_mode(self.mode)
    
    @property
    def is_execute_phase(self) -> bool:
        """Check if current mode is an execution/analysis phase."""
        return ModeSemantics.is_execute_mode(self.mode)
    
    @property
    def is_verify_phase(self) -> bool:
        """Check if current mode is a verification/interpretation phase."""
        return ModeSemantics.is_verify_mode(self.mode)


@dataclass
class ConversationState:
    """Workflow state tracking for ephemeral message generation.

    Tracks artifacts, current task, and counters for generating
    contextual ephemeral reminders injected before each LLM call.
    """

    # Artifact tracking
    created_artifacts: list[str] = field(default_factory=list)
    artifact_last_access: dict[str, int] = field(default_factory=dict)  # path -> step count

    # Artifact modification tracking (by role)
    # Structure: {"plan": ["research_plan.md"], "summary": [], ...}
    artifacts_modified_in_task: dict[str, list[str]] = field(default_factory=dict)

    # Task tracking
    active_task: Optional[TaskInfo] = None
    task_boundary_reason: str = "a task boundary has never been set yet in this conversation"

    # Counter tracking
    tools_since_boundary: int = 0
    tools_since_update: int = 0
    current_step: int = 0

    # Think Tool tracking
    tools_since_think: int = 0  # 自上次 think 以来的工具调用数
    last_think_step: int = 0    # 上次使用 think 的步骤
    think_reminder_threshold: int = 5  # 触发提醒的阈值（可配置）

    # Task update tracking (for detecting too many steps in one task)
    task_update_count: int = 0  # 当前 task 的更新次数（step 变更次数）

    # Conditional flags (deprecated, kept for backward compatibility)
    plan_edited_in_planning: bool = False
    pending_review_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert state to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationState":
        """Create state from dictionary."""
        if not data:
            return cls()
            
        # Filter keys to match fields (safety) and copy to avoid mutating input
        valid_keys = {f.name for f in fields(cls)}
        init_data = {k: v for k, v in data.items() if k in valid_keys}
        
        # Handle nested active_task
        if init_data.get("active_task") and isinstance(init_data["active_task"], dict):
            init_data["active_task"] = TaskInfo(**init_data["active_task"])
            
        return cls(**init_data)


    def on_task_boundary(self, name: str, mode: str, status: str, summary: str):
        """Called when task_boundary tool is invoked."""
        is_new_task = self.active_task is None or self.active_task.name != name

        if is_new_task:
            # New task, reset modification tracking and update count
            self.artifacts_modified_in_task = {}
            self.plan_edited_in_planning = False
            self.task_update_count = 0  # 新 task，重置更新计数
            self.tools_since_boundary = 0
        else:
            # Same task, increment update count (this is a step change)
            self.task_update_count += 1

        self.active_task = TaskInfo(
            name=name, mode=mode, status=status, summary=summary,
            start_step=self.current_step
        )
        self.tools_since_update = 0
        self.tools_since_think = 0  # 新增：重置 think 计数
        self.task_boundary_reason = ""

    def on_notify_user(self, paths: list[str]):
        """Called when notify_user tool is invoked."""
        self.pending_review_paths = paths
        self.active_task = None
        self.tools_since_think = 0  # 新增：重置 think 计数
        self.task_boundary_reason = "there has been a notify_user action since the last task boundary"
        
    def on_artifact_created(self, path: str):
        """Track artifact creation."""
        if path not in self.created_artifacts:
            self.created_artifacts.append(path)
        self.artifact_last_access[path] = self.current_step
        
    def on_artifact_modified(self, path: str, brain_dir: str = ""):
        """Track artifact modification/access.
        
        Args:
            path: Path to the modified artifact
            brain_dir: Brain directory path (optional, for artifact detection)
        """
        self.artifact_last_access[path] = self.current_step
        
        # Track by role
        role = ArtifactRoles.get_role(path) or "other"
        if role not in self.artifacts_modified_in_task:
            self.artifacts_modified_in_task[role] = []
        if path not in self.artifacts_modified_in_task[role]:
            self.artifacts_modified_in_task[role].append(path)
        
        # Backward compatibility: check if plan edited in plan phase
        if ArtifactRoles.is_plan_artifact(path) and self.active_task:
            if self.active_task.is_plan_phase:
                self.plan_edited_in_planning = True
            
    def on_tool_call(self, count: int = 1):
        """Update counters after tool execution."""
        self.tools_since_boundary += count
        self.tools_since_update += count
        self.tools_since_think += count  # 新增：更新 think 计数
        self.current_step += count

    def on_think_tool_used(self):
        """记录 think tool 使用"""
        self.tools_since_think = 0
        self.last_think_step = self.current_step

    def should_remind_think(self) -> bool:
        """判断是否应该提醒使用 think tool"""
        # 策略 1: 固定阈值
        if self.tools_since_think >= self.think_reminder_threshold:
            return True

        # 策略 2: 在任务活跃且工具链较长时提醒
        if self.active_task and self.tools_since_think >= 3:
            return True

        return False
    
    def has_plan_artifacts_modified(self) -> bool:
        """Check if any plan-type artifacts were modified in this task."""
        return bool(self.artifacts_modified_in_task.get("plan"))
    
    def get_modified_artifacts_by_role(self, role: str) -> list[str]:
        """Get list of modified artifacts for a specific role."""
        return self.artifacts_modified_in_task.get(role, [])
    
    def get_all_modified_artifacts(self) -> list[str]:
        """Get all modified artifacts in this task."""
        all_artifacts = []
        for artifacts in self.artifacts_modified_in_task.values():
            all_artifacts.extend(artifacts)
        return all_artifacts
