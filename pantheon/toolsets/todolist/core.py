"""TodoList ToolSet - TaskGroup-based Design (v2.0)

Refactored for hierarchical task management:
- 4 core tools (create, manage, complete, list)
- Global singleton manager with session isolation
- Complete todos returned in all responses
"""

from ...toolset import ToolSet, tool
from .task_manager import TaskManager
from ...utils.log import logger


class TodoListToolSet(ToolSet):
    """TaskGroup-based todo management with session isolation"""

    # Global singleton manager (shared across all instances)
    _global_manager: TaskManager | None = None

    def __init__(
        self, name: str = "todolist", use_global_manager: bool = False, **kwargs
    ):
        super().__init__(name=name, **kwargs)
        self._service_name = name

        # Initialize global manager if not exists (uses default workspace)
        if use_global_manager and TodoListToolSet._global_manager is None:
            TodoListToolSet._global_manager = TaskManager()

        if use_global_manager:
            self.manager = TodoListToolSet._global_manager
        else:
            self.manager = TaskManager()

    @tool
    async def create_task(
        self,
        title: str,
        description: str = "",
        initial_todos: list[str] = None,
    ) -> dict:
        """Create a new task with optional initial todos.

        Args:
            title: Task title
            description: Optional task description
            initial_todos: List of initial todo items

        Returns:
            dict with success status, task info, and complete todos list
        """
        if not self.manager:
            return {"success": False, "error": "Manager not initialized"}
        logger.info(f"Current Context: {self.get_context()}")
        chat_id = self.get_session_id()
        if not chat_id:
            return {"success": False, "error": "No session_id provided"}

        try:
            task = self.manager.create_task(
                chat_id=chat_id,
                title=title,
                description=description,
                initial_todos=initial_todos or [],
            )

            # Get complete todos for this task
            todos = self.manager.get_todos_for_task(task.id)

            return {
                "success": True,
                "task": {
                    "id": task.id,
                    "title": task.title,
                    "description": task.description,
                    "status": task.status,
                    "todos": todos,
                },
                "todo_count": len(todos),
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def manage_task(
        self,
        task_id: str = None,
        add_todos: list[str] = None,
        update_todos: list[dict] = None,
        remove_todos: list[str] = None,
    ) -> dict:
        """Manage a task's todos with incremental updates.

        This tool returns ONLY the changes made (incremental updates), not the complete task.
        Use list_tasks(current_only=True) if you need the full current task state.

        Args:
            task_id: Task ID to manage (None = current task, default)
            add_todos: List of new todo content strings
            update_todos: List of dicts with {id, status?, content?} to update
            remove_todos: List of todo IDs to remove

        Returns:
            dict with incremental changes, success, task_id
        """
        if not self.manager:
            return {"success": False, "error": "Manager not initialized"}

        chat_id = self.get_session_id()
        if not chat_id:
            return {"success": False, "error": "No session_id provided"}

        # Get target task
        if task_id:
            task = self.manager.get_task(task_id)
            if not task:
                return {"success": False, "error": f"Task {task_id} not found"}
            if task.chat_id != chat_id:
                return {"success": False, "error": "Task belongs to different chat"}
        else:
            task = self.manager.get_current_task(chat_id)
            if not task:
                return {"success": False, "error": "No active task found"}

        results = {
            "success": True,
            "task_id": task.id,
            "added": [],
            "updated": [],
            "removed": [],
        }

        try:
            # Add todos
            if add_todos:
                for content in add_todos:
                    todo_id = self.manager.add_todo_to_task(
                        task.id, content, status="pending"
                    )
                    # Get the created todo
                    todo = self.manager.todos.get(todo_id)
                    if todo:
                        results["added"].append(todo.to_dict())

            # Update todos
            if update_todos:
                for item in update_todos:
                    todo_id = item.get("id")
                    new_status = item.get("status")
                    new_content = item.get("content")

                    if not todo_id:
                        continue

                    # Validate status if provided
                    if new_status:
                        valid_statuses = ["pending", "in_progress", "completed"]
                        if new_status not in valid_statuses:
                            continue

                    # Update todo (status and/or content)
                    if self.manager.update_todo(
                        todo_id, status=new_status, content=new_content
                    ):
                        update_result = {"id": todo_id}
                        if new_status:
                            update_result["status"] = new_status
                        if new_content:
                            update_result["content"] = new_content
                        results["updated"].append(update_result)

            # Remove todos
            if remove_todos:
                for todo_id in remove_todos:
                    if self.manager.remove_todo(todo_id):
                        results["removed"].append(todo_id)

            # Update current_task pointer (focus pointer semantics)
            # Operating on any task automatically switches current_task
            self.manager.current_task[chat_id] = task.id

            return results

        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def complete_task(self, task_id: str = None) -> dict:
        """Complete a task (defaults to current task).

        Args:
            task_id: Task ID to complete (None = current task, default)

        Returns:
            dict with success, task info, complete todos, duration
        """
        if not self.manager:
            return {"success": False, "error": "Manager not initialized"}

        chat_id = self.get_session_id()
        if not chat_id:
            return {"success": False, "error": "No session_id provided"}

        # Get target task
        if task_id:
            task = self.manager.get_task(task_id)
            if not task:
                return {"success": False, "error": f"Task {task_id} not found"}
            if task.chat_id != chat_id:
                return {"success": False, "error": "Task belongs to different chat"}
        else:
            task = self.manager.get_current_task(chat_id)
            if not task:
                return {"success": False, "error": "No active task found"}

        try:
            # Complete the task
            self.manager.complete_task(task.id)

            # Update current_task pointer (focus pointer semantics)
            # Keep pointing to this task even after completion
            self.manager.current_task[chat_id] = task.id

            # Refresh task data
            completed_task = self.manager.get_task(task.id)
            todos = self.manager.get_todos_for_task(task.id)

            # Calculate duration
            duration = None
            if completed_task.started_at and completed_task.completed_at:
                duration = completed_task.completed_at - completed_task.started_at

            # Count completed todos
            completed_todos = sum(1 for t in todos if t["status"] == "completed")

            return {
                "success": True,
                "task": {
                    "id": completed_task.id,
                    "title": completed_task.title,
                    "description": completed_task.description,
                    "status": completed_task.status,
                    "completed_at": completed_task.completed_at,
                    "todos": todos,
                },
                "duration": duration,
                "completed_todos": completed_todos,
                "show_in_timeline": True,  # Signal frontend to show in timeline
            }

        except Exception as e:
            return {"success": False, "error": str(e)}

    @tool
    async def list_tasks(
        self,
        status: str = None,
        include_todos: bool = True,
        current_only: bool = False,
    ) -> dict:
        """List tasks for the current session.

        Args:
            status: Filter by status (pending/in_progress/completed)
            include_todos: Whether to include complete todos list
            current_only: Only return the current in_progress task (default: False)

        Returns:
            dict with success, tasks list, summary counts, and current_task_id
        """
        if not self.manager:
            return {"success": False, "error": "Manager not initialized"}

        chat_id = self.get_session_id()
        if not chat_id:
            return {"success": False, "error": "No session_id provided"}

        try:
            if current_only:
                current_task = self.manager.get_current_task(chat_id)
                if current_task:
                    task_dict = current_task.to_dict()
                    if include_todos:
                        task_dict["todos"] = self.manager.get_todos_for_task(
                            current_task.id
                        )
                    tasks = [task_dict]
                else:
                    tasks = []
            else:
                tasks = self.manager.get_tasks_by_chat(
                    chat_id=chat_id, status=status, include_todos=include_todos
                )

            summary = self.manager.get_summary(chat_id=chat_id)

            # Get current task ID for this chat
            current_task_id = self.manager.current_task.get(chat_id)

            return {
                "success": True,
                "tasks": tasks,
                "summary": summary,
                "current_task_id": current_task_id,  # Add current_task_id to response
            }

        except Exception as e:
            return {"success": False, "error": str(e)}
