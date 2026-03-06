"""
ACE Learning Pipeline module for Pantheon.

The pipeline manages the asynchronous learning workflow:
1. Receives LearningInput from agents
2. Runs Reflector analysis
3. Runs SkillManager to decide updates
4. Applies updates to Skillbook
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from pantheon.utils.log import logger
from .reflector import Reflector
from .skill_loader import load_skills_into_skillbook
from .skill_manager import SkillManager, UpdateOperation
from .skillbook import Skillbook
from .skill_loader import load_skills_into_skillbook
from .skill_manager import SkillManager, UpdateOperation
from .skillbook import Skillbook


# ===========================================================================
# LearningInput - Data structure for learning submissions
# ===========================================================================


@dataclass
class LearningInput:
    """Data submitted to the learning pipeline for processing.
    
    Simplified structure: only contains the path to saved messages.
    Compression happens internally during processing.
    """

    turn_id: str
    agent_name: str
    details_path: str  # Path to saved messages JSON
    chat_id: str = ""  # Original chat/memory ID for grouping
    round_number: int = 0  # Round number within the chat


def build_learning_input(
    turn_id: str,
    agent_name: str,
    messages: List[dict],
    learning_dir: str = ".pantheon/ace/learning",
    chat_id: str = "",
) -> LearningInput:
    """
    Build a LearningInput by saving messages to file.
    
    This unified function works for both pipeline and team modes.
    Compression is deferred to processing time.
    
    Args:
        turn_id: Unique identifier for this turn
        agent_name: Name of the agent
        messages: List of message dicts from the conversation
        learning_dir: Directory to save turn details
        chat_id: Original chat/memory ID for grouping learning files
    
    Returns:
        LearningInput with details_path pointing to saved messages
    """
    from pantheon.utils.memory_compress import save_messages_to_memory
    
    # Validate agent_name, fallback to global if empty
    if not agent_name or not agent_name.strip():
        logger.warning("agent_name is empty in build_learning_input, using 'global'")
        agent_name = "global"
    else:
        agent_name = agent_name.strip()
    
    # If chat_id provided, organize by chat and round number
    if chat_id:
        # Use first 8 chars of chat_id for directory name
        chat_prefix = str(chat_id)[:8]
        chat_dir = Path(learning_dir) / "chats" / chat_prefix
        chat_dir.mkdir(parents=True, exist_ok=True)
        
        # Compute round number by counting existing round files
        existing_rounds = list(chat_dir.glob("round_*.json"))
        # Filter out trajectory files (round_XXX_trajectory.txt)
        existing_rounds = [f for f in existing_rounds if not f.stem.endswith("_trajectory")]
        round_number = len(existing_rounds) + 1
        
        # Save messages with round-based naming
        memory_path = str(chat_dir / f"round_{round_number:03d}.json")
    else:
        # Legacy fallback: use turn_id directly
        Path(learning_dir).mkdir(parents=True, exist_ok=True)
        memory_path = f"{learning_dir}/turn_{turn_id}.json"
        round_number = 0
    
    save_messages_to_memory(messages, memory_path)
    
    logger.debug(f"Saved messages for learning: {memory_path}")
    
    return LearningInput(
        turn_id=turn_id,
        agent_name=agent_name,
        details_path=memory_path,
        chat_id=chat_id,
        round_number=round_number,
    )



# ===========================================================================
# LearningPipeline - Async learning workflow
# ===========================================================================


class LearningPipeline:
    """
    Asynchronous learning pipeline for ACE.
    
    Supports two modes via pluggable LearningPath classes:
    - "pipeline": PipelineLearningPath using Reflector + SkillManager
    - "team": TeamLearningPath using skill_learning_team
    
    Uses asyncio.Queue for thread-safe processing of learning tasks.
    Learning happens in the background without blocking user interactions.
    """

    # Minimum trajectory length to consider for learning
    MIN_TRAJECTORY_LENGTH = 50

    def __init__(
        self,
        skillbook: Skillbook,
        reflector: Reflector,
        skill_manager: SkillManager,
        learning_dir: str,
        cleanup_after_learning: bool = False,
        min_confidence_threshold: float = 0.5,
        min_atomicity_score: float = 0.85,
        mode: str = "pipeline",  # "pipeline" or "team"
        team_id: str = "skill_learning_team",
    ):
        self._mode = mode
        self._queue: asyncio.Queue[LearningInput] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._running = False
        
        # Create the appropriate path based on mode
        if mode == "pipeline":
            self._path: "BaseLearningPath" = PipelineLearningPath(
                skillbook=skillbook,
                reflector=reflector,
                skill_manager=skill_manager,
                learning_dir=learning_dir,
                cleanup_after_learning=cleanup_after_learning,
                min_confidence_threshold=min_confidence_threshold,
                min_atomicity_score=min_atomicity_score,
            )
        else:
            self._path = TeamLearningPath(
                skillbook=skillbook,  # For fallback and summary
                team_id=team_id,
                learning_dir=learning_dir,
                cleanup_after_learning=cleanup_after_learning,
            )
        
        logger.info(f"LearningPipeline created (mode={mode})")

    async def initialize_team(self, endpoint_service) -> None:
        """Initialize the path (for team mode, creates the learning team)."""
        await self._path.initialize(endpoint_service=endpoint_service)

    async def start(self) -> None:
        """Start the background learning task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._process_loop())
        logger.info(f"ACE learning pipeline started (mode={self._mode})")

    async def stop(self) -> None:
        """Stop the pipeline and save skillbook."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Final save via path
        await self._path.save()
        logger.info("ACE learning pipeline stopped")

    def submit(self, input: LearningInput) -> None:
        """Submit a learning task (non-blocking)."""
        if self._should_skip(input):
            logger.debug(f"Skipping learning for {input.agent_name}: criteria not met")
            if input.details_path:
                self._path._cleanup_learning_file(input.details_path)
            return
        self._queue.put_nowait(input)
        logger.debug(f"Submitted learning task for {input.agent_name}")

    def _should_skip(self, input: LearningInput) -> bool:
        """Check if this input should be skipped."""
        if not input.details_path:
            return True
        if not Path(input.details_path).exists():
            return True
        return False

    async def _process_loop(self) -> None:
        """Background loop processing learning tasks."""
        while self._running:
            try:
                input = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                await self._path.process_task(input)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Learning pipeline loop error: {e}")

    # ===================================================================
    # Public API for skill injection (used by PantheonTeam)
    # ===================================================================

    async def load_prompt(self, agent_name: str = "global") -> str:
        """Load skill prompt for agent injection."""
        return await self._path.load_prompt(agent_name)

    async def get_summary(self) -> str:
        """Get skillbook summary for logging."""
        return await self._path.get_summary()

    async def inject_to_team(self, team: "PantheonTeam") -> None:
        """Inject skills into all agents in a team."""
        await self._path.inject_to_team(team)


# ===========================================================================
# Learning Path Base and Implementations
# ===========================================================================


class BaseLearningPath:
    """Base class for learning paths.
    
    Both Pipeline and Team modes share the same file-based skillbook,
    so common operations (load_prompt, get_summary, inject_to_team, save)
    are implemented here.
    """
    
    def __init__(self, skillbook: Skillbook, learning_dir: str, cleanup_after_learning: bool = False):
        self._skillbook = skillbook
        self._learning_dir = learning_dir
        self._cleanup_after_learning = cleanup_after_learning
    
    async def initialize(self, **kwargs) -> None:
        """Initialize the path (override in subclass if needed)."""
        pass
    
    async def load_prompt(self, agent_name: str = "global") -> str:
        """Load skill prompt from local Skillbook."""
        self._skillbook.load()  # Reload from file
        return self._skillbook.as_prompt(agent_name)
    
    async def get_summary(self) -> str:
        """Get skillbook summary."""
        self._skillbook.load()
        return self._skillbook.summary_line()
    
    async def inject_to_team(self, team: "PantheonTeam") -> None:
        """Inject skills into team agents."""
        for agent in team.team_agents:
            if "📌 User Rules" in agent.instructions or "📚 Learned" in agent.instructions:
                continue
            prompt = await self.load_prompt(agent.name)
            if prompt:
                agent.instructions += f"\n\n{prompt}"
                logger.debug(f"Injected skillbook into agent: {agent.name}")
    
    async def process_task(self, input: LearningInput) -> None:
        """Process a learning task."""
        raise NotImplementedError
    
    async def save(self) -> None:
        """Save skillbook state."""
        self._skillbook.save()
    
    def _cleanup_learning_file(self, file_path: str) -> None:
        """Delete a learning trajectory file after processing."""
        try:
            import os
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.debug(f"Cleaned up learning file: {file_path}")
        except Exception as e:
            logger.warning(f"Failed to cleanup learning file {file_path}: {e}")



class PipelineLearningPath(BaseLearningPath):
    """Pipeline mode: local Skillbook with Reflector + SkillManager."""
    
    def __init__(
        self,
        skillbook: Skillbook,
        reflector: Reflector,
        skill_manager: SkillManager,
        learning_dir: str,
        cleanup_after_learning: bool = False,
        min_confidence_threshold: float = 0.5,
        min_atomicity_score: float = 0.85,
    ):
        super().__init__(skillbook, learning_dir, cleanup_after_learning)
        self._reflector = reflector
        self._skill_manager = skill_manager
        self._min_confidence_threshold = min_confidence_threshold
        self._min_atomicity_score = min_atomicity_score
    
    async def process_task(self, input: LearningInput) -> None:
        """Process learning task using Reflector + SkillManager pipeline."""
        try:
            logger.debug(f"Processing learning for {input.agent_name} (pipeline mode)")

            # 1. Reflector analysis
            reflection = await self._reflector.reflect(input, self._skillbook)
            
            if reflection.confidence < self._min_confidence_threshold:
                logger.debug(f"Low confidence reflection ({reflection.confidence}), skipping")
                return

            # 2. Apply skill tags immediately
            for tag in reflection.skill_tags:
                skill = self._skillbook.tag_skill(tag.id, tag.tag)
                if skill:
                    logger.debug(f"Tagged skill {tag.id} as {tag.tag}")

            # 3. Get update operations from SkillManager
            operations = await self._skill_manager.update_skills(
                reflection, self._skillbook, input.agent_name,
                min_atomicity_score=self._min_atomicity_score,
            )

            # 4. Apply operations and collect stats
            ops_summary = {"ADD": 0, "UPDATE": 0, "TAG": 0, "REMOVE": 0}
            for op in operations:
                self._apply_operation(op, default_agent_scope=input.agent_name)
                ops_summary[op.type] = ops_summary.get(op.type, 0) + 1

            # 5. Persist after each update
            self._skillbook.save()
            
            # 6. Reload skills from files (in case user modified them)
            self._reload_skills_from_files()

            # 7. Log learning summary
            ops_str = ", ".join(f"{k}:{v}" for k, v in ops_summary.items() if v > 0) or "none"
            logger.info(
                f"📚 [ACE Learning] Agent: {input.agent_name} | "
                f"Tags: {len(reflection.skill_tags)} | Ops: [{ops_str}] | "
                f"Confidence: {reflection.confidence:.2f}"
            )
            logger.info(f"📊 [ACE Stats] {self._skillbook.summary_line()}")

        except Exception as e:
            logger.error(f"Learning task failed for {input.agent_name}: {e}")
        finally:
            if self._cleanup_after_learning and input.details_path:
                self._cleanup_learning_file(input.details_path)
    
    def _reload_skills_from_files(self) -> None:
        """Reload skills from files to pick up any user modifications."""
        skills_dir = self._skillbook.skills_dir
        if not skills_dir or not skills_dir.exists():
            return
        try:
            loaded = self._skillbook._merge_from_files()
            if loaded > 0:
                self._skillbook.save()
                logger.debug(f"Reloaded {loaded} skills from files")
        except Exception as e:
            logger.warning(f"Failed to reload skills from files: {e}")
    
    def _apply_operation(self, op: UpdateOperation, default_agent_scope: str = "global") -> None:
        """Apply a single update operation to the skillbook."""
        try:
            # Protect user-defined skills from modification (allow TAG only)
            if op.skill_id and op.type in ("UPDATE", "REMOVE"):
                skill = self._skillbook.get_skill(op.skill_id)
                if skill and skill.is_user_defined():
                    logger.warning(
                        f"Skipping {op.type} on user-defined skill: {op.skill_id}"
                    )
                    return

            if op.type == "ADD":
                if op.section and op.content:
                    skill = self._skillbook.add_skill(
                        section=op.section,
                        content=op.content,
                        agent_scope=default_agent_scope,
                        description=op.description,
                    )
                    if skill:
                        logger.info(f"Added skill: {skill.id}")

            elif op.type == "UPDATE":
                if op.skill_id and op.content:
                    skill = self._skillbook.update_skill(op.skill_id, content=op.content)
                    if skill:
                        logger.info(f"Updated skill: {skill.id}")

            elif op.type == "TAG":
                if op.skill_id:
                    if op.helpful:
                        self._skillbook.tag_skill(op.skill_id, "helpful", op.helpful)
                        logger.debug(f"Tagged skill {op.skill_id}: helpful+{op.helpful}")
                    if op.harmful:
                        self._skillbook.tag_skill(op.skill_id, "harmful", op.harmful)
                        logger.debug(f"Tagged skill {op.skill_id}: harmful+{op.harmful}")
                    if op.neutral:
                        self._skillbook.tag_skill(op.skill_id, "neutral", op.neutral)
                        logger.debug(f"Tagged skill {op.skill_id}: neutral+{op.neutral}")

            elif op.type == "REMOVE":
                if op.skill_id:
                    self._skillbook.remove_skill(op.skill_id, soft=True)
                    logger.info(f"Removed skill: {op.skill_id}")

        except Exception as e:
            logger.error(f"Failed to apply operation {op.type}: {e}")


class TeamLearningPath(BaseLearningPath):
    """Team mode: uses skill_learning_team for learning.
    
    Uses the same file-based skillbook as PipelineLearningPath.
    All common operations (load_prompt, get_summary, inject_to_team, save)
    are inherited from BaseLearningPath.
    """
    
    def __init__(
        self,
        skillbook: Skillbook,
        team_id: str = "skill_learning_team",
        learning_dir: str = "",
        cleanup_after_learning: bool = False,
    ):
        super().__init__(skillbook, learning_dir, cleanup_after_learning)
        self._team_id = team_id
        self._team: Optional["PantheonTeam"] = None
        self._endpoint_service = None
    
    async def initialize(self, endpoint_service=None, **kwargs) -> None:
        """Initialize the learning team."""
        if self._team is not None:
            return
        
        self._endpoint_service = endpoint_service
        
        if endpoint_service is None:
            logger.warning("No endpoint_service provided for TeamLearningPath")
            return
        
        logger.info(f"Initializing learning team '{self._team_id}'...")
        try:
            from pantheon.factory import create_team_from_template
            
            # Create team WITHOUT learning to prevent circular references
            self._team = await create_team_from_template(
                endpoint_service=endpoint_service,
                template_id=self._team_id,
                check_toolsets=True,
                enable_mcp=False,
            )
            
            logger.info(f"Learning team '{self._team_id}' initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize learning team: {e}")
            raise
    
    async def process_task(self, input: LearningInput) -> None:
        """Process learning task using skill_learning_team."""
        from pantheon.internal.memory import Memory
        
        try:
            logger.debug(f"Processing learning for {input.agent_name} (team mode)")
            
            if self._team is None:
                logger.error("Team not initialized, cannot process learning task")
                return
            
            # Determine team runs directory based on chat_id
            if input.chat_id:
                # Organize team runs by chat_id
                chat_prefix = str(input.chat_id)[:8]
                team_runs_dir = Path(self._learning_dir) / "team_runs" / chat_prefix
            else:
                # Legacy fallback
                team_runs_dir = Path(self._learning_dir) / "team_runs"
            team_runs_dir.mkdir(parents=True, exist_ok=True)
            
            memory = Memory(name=f"learning_{input.turn_id}")
            memory.id = input.turn_id
            
            # Run the team - agent_name passed in message, not context_variables
            result = await self._team.run(
                msg=f"Learn from: {input.details_path}\nAgent: {input.agent_name}",
                memory=memory,
            )
            
            # Save team conversation memory with round-based naming
            if input.round_number > 0:
                memory_file = team_runs_dir / f"round_{input.round_number:03d}_learning.json"
            else:
                memory_file = team_runs_dir / f"{input.turn_id}.json"
            memory.save(str(memory_file))
            logger.debug(f"Saved team memory to {memory_file}")
            
            # Get summary (will reload from file)
            summary = await self.get_summary()
            
            logger.info(
                f"📚 [ACE Learning] Agent: {input.agent_name} | Mode: team | Completed"
            )
            logger.info(f"📊 [ACE Stats] {summary}")
            
        except Exception as e:
            logger.error(f"Team learning task failed for {input.agent_name}: {e}")
        finally:
            if self._cleanup_after_learning and input.details_path:
                self._cleanup_learning_file(input.details_path)


# Type hint for PantheonTeam (imported at runtime to avoid circular imports)
if __name__ != "__main__":
    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from pantheon.team import PantheonTeam

