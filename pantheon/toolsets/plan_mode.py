"""Plan Mode ToolSet - Exposes plan mode control as tools"""

from pantheon.toolset import ToolSet, tool


class PlanModeToolSet(ToolSet):
    """Tools for entering and exiting Plan Mode - a read-only planning environment"""

    def __init__(self, agent, name: str = "plan_mode"):
        super().__init__(name=name)
        self.agent = agent

    @tool
    async def enable_plan_mode(self) -> dict:
        """Enter Plan Mode for safe planning and analysis. Use for complex features (5+ steps), large architectural changes, or when thorough codebase understanding is needed. In Plan Mode you can read/analyze code, search codebase, and discuss with user. You CANNOT write/edit files, run commands, or make changes. After thorough analysis, call exit_plan_mode() with your comprehensive implementation plan."""
        result = self.agent.enable_plan_mode()
        return result

    @tool
    async def exit_plan_mode(self, plan: str) -> dict:
        """Exit Plan Mode by presenting your comprehensive implementation plan to the user.

        Args:
            plan: Your detailed implementation plan in markdown format. Must include:
                  - Overview of changes
                  - Step-by-step implementation strategy
                  - Files to modify and why
                  - Potential risks and edge cases
                  - Testing approach

        Returns:
            dict with success status and plan content

        This will present your plan to the user and pause execution for their approval.
        """
        if not self.agent.plan_mode:
            return {"success": False, "error": "Not in Plan Mode"}

        # Exit Plan Mode internally (restore tool access)
        self.agent._disable_plan_mode_internal()

        return {
            "success": True,
            "plan": plan,
            "exit_plan_mode": True,  # Signal to stop execution loop
            "message": "Plan presented. Waiting for user approval to proceed with implementation.",
        }
