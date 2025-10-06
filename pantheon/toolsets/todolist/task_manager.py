"""TaskManager - Global singleton with chat-based isolation

Manages tasks and todos with chat isolation.
Data models (Task, Todo) are defined inline.
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class Todo:
    """Individual todo item"""
    id: str
    task_id: str
    content: str
    status: str  # pending | in_progress | completed
    order: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "content": self.content,
            "status": self.status,
            "order": self.order
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Todo':
        return cls(
            id=data["id"],
            task_id=data["task_id"],
            content=data["content"],
            status=data["status"],
            order=data.get("order", 0)
        )


@dataclass
class Task:
    """Task containing multiple todos"""
    id: str
    chat_id: str
    title: str
    description: str = ""
    status: str = "pending"  # pending | in_progress | completed
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    todo_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "todo_ids": self.todo_ids
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Task':
        return cls(
            id=data["id"],
            chat_id=data["chat_id"],
            title=data["title"],
            description=data.get("description", ""),
            status=data.get("status", "pending"),
            created_at=data["created_at"],
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            todo_ids=data.get("todo_ids", [])
        )

    @staticmethod
    def generate_id() -> str:
        """Generate unique task ID"""
        return f"task_{str(uuid.uuid4())[:8]}"


def generate_todo_id() -> str:
    """Generate unique todo ID"""
    return f"todo_{str(uuid.uuid4())[:8]}"


# ============================================================================
# TaskManager
# ============================================================================

class TaskManager:
    """Global singleton manager for all tasks and todos"""

    def __init__(self, workspace_path: Path = None):
        """Initialize task manager.

        Args:
            workspace_path: Directory to store the global tasks file
                           Defaults to current directory if not provided
        """
        self.workspace_path = workspace_path or Path.cwd()
        self.tasks_file = self.workspace_path / ".pantheon_tasks.json"

        # In-memory storage
        self.tasks: dict[str, Task] = {}  # task_id -> Task
        self.todos: dict[str, Todo] = {}  # todo_id -> Todo

        # Indexes
        self.by_chat: dict[str, list[str]] = {}  # chat_id -> [task_ids]
        self.by_status: dict[str, list[str]] = {}  # status -> [task_ids]

        # Current task tracking per chat
        self.current_task: dict[str, str] = {}  # chat_id -> task_id

        self._load_tasks()

    def _load_tasks(self):
        """Load tasks from JSON file"""
        if not self.tasks_file.exists():
            return

        try:
            with open(self.tasks_file, 'r') as f:
                data = json.load(f)

            # Load tasks
            for task_id, task_data in data.get("tasks", {}).items():
                self.tasks[task_id] = Task.from_dict(task_data)

            # Load todos
            for todo_id, todo_data in data.get("todos", {}).items():
                self.todos[todo_id] = Todo.from_dict(todo_data)

            # Load indexes
            indexes = data.get("indexes", {})
            self.by_chat = indexes.get("by_chat", {})
            self.by_status = indexes.get("by_status", {})

            # Load current task tracking
            self.current_task = data.get("current_task", {})

        except (json.JSONDecodeError, KeyError) as e:
            print(f"Warning: Failed to load tasks file: {e}")
            # Reset to empty state
            self.tasks = {}
            self.todos = {}
            self.by_chat = {}
            self.by_status = {}
            self.current_task = {}

    def _save_tasks(self):
        """Save tasks to JSON file"""
        data = {
            "version": "3.0",
            "tasks": {tid: t.to_dict() for tid, t in self.tasks.items()},
            "todos": {tid: t.to_dict() for tid, t in self.todos.items()},
            "indexes": {
                "by_chat": self.by_chat,
                "by_status": self.by_status
            },
            "current_task": self.current_task
        }

        try:
            # Ensure workspace directory exists
            self.workspace_path.mkdir(parents=True, exist_ok=True)
            with open(self.tasks_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            raise IOError(f"Failed to save tasks: {e}")

    def _update_indexes(self, task: Task):
        """Update indexes for a task"""
        # Update by_chat index
        if task.chat_id not in self.by_chat:
            self.by_chat[task.chat_id] = []
        if task.id not in self.by_chat[task.chat_id]:
            self.by_chat[task.chat_id].append(task.id)

        # Update by_status index
        if task.status not in self.by_status:
            self.by_status[task.status] = []
        if task.id not in self.by_status[task.status]:
            self.by_status[task.status].append(task.id)

    def _remove_from_indexes(self, task: Task):
        """Remove task from indexes"""
        # Remove from by_chat
        if task.chat_id in self.by_chat:
            if task.id in self.by_chat[task.chat_id]:
                self.by_chat[task.chat_id].remove(task.id)

        # Remove from by_status
        if task.status in self.by_status:
            if task.id in self.by_status[task.status]:
                self.by_status[task.status].remove(task.id)

    def create_task(
        self,
        chat_id: str,
        title: str,
        description: str = "",
        initial_todos: list[str] = None
    ) -> Task:
        """Create a new task"""
        task = Task(
            id=Task.generate_id(),
            chat_id=chat_id,
            title=title,
            description=description,
            status="pending",
            created_at=time.time()
        )

        # Add task to storage first before adding todos
        self.tasks[task.id] = task
        self._update_indexes(task)

        # Add todos if provided (task must exist in self.tasks first)
        if initial_todos:
            for i, content in enumerate(initial_todos):
                self.add_todo_to_task(task.id, content, order=i)

        # Set as current task for this chat
        self.current_task[chat_id] = task.id

        self._save_tasks()
        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID"""
        return self.tasks.get(task_id)

    def get_current_task(self, chat_id: str) -> Optional[Task]:
        """Get current active task for a chat"""
        task_id = self.current_task.get(chat_id)
        if task_id:
            return self.tasks.get(task_id)
        return None

    def add_todo_to_task(
        self,
        task_id: str,
        content: str,
        status: str = "pending",
        order: int = None
    ) -> str:
        """Add a todo to a task"""
        task = self.tasks.get(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        if order is None:
            order = len(task.todo_ids)

        todo = Todo(
            id=generate_todo_id(),
            task_id=task_id,
            content=content,
            status=status,
            order=order
        )

        self.todos[todo.id] = todo
        task.todo_ids.append(todo.id)

        # Update task status if needed
        if task.status == "pending" and status == "in_progress":
            task.status = "in_progress"
            task.started_at = time.time()
            self._remove_from_indexes(task)
            self._update_indexes(task)

        self._save_tasks()
        return todo.id

    def update_todo(self, todo_id: str, status: str = None, content: str = None) -> bool:
        """Update todo status and/or content"""
        todo = self.todos.get(todo_id)
        if not todo:
            return False

        # Update content if provided
        if content is not None:
            todo.content = content

        # Update status if provided
        if status is not None:
            todo.status = status

            # Update task status if needed
            task = self.tasks.get(todo.task_id)
            if task:
                self._update_task_status(task)

        self._save_tasks()
        return True

    def _update_task_status(self, task: Task):
        """Update task status based on todos"""
        if not task.todo_ids:
            return

        todos = [self.todos.get(tid) for tid in task.todo_ids if tid in self.todos]
        if not todos:
            return

        # Check if all completed
        if all(t.status == "completed" for t in todos):
            if task.status != "completed":
                task.status = "completed"
                task.completed_at = time.time()
                self._remove_from_indexes(task)
                self._update_indexes(task)
        # Check if any in progress
        elif any(t.status == "in_progress" for t in todos):
            if task.status != "in_progress":
                task.status = "in_progress"
                if not task.started_at:
                    task.started_at = time.time()
                self._remove_from_indexes(task)
                self._update_indexes(task)

    def remove_todo(self, todo_id: str) -> bool:
        """Remove a todo"""
        todo = self.todos.get(todo_id)
        if not todo:
            return False

        task = self.tasks.get(todo.task_id)
        if task and todo_id in task.todo_ids:
            task.todo_ids.remove(todo_id)

        del self.todos[todo_id]
        self._save_tasks()
        return True

    def complete_task(self, task_id: str) -> bool:
        """Mark a task as completed"""
        task = self.tasks.get(task_id)
        if not task:
            return False

        task.status = "completed"
        task.completed_at = time.time()

        # Update indexes
        self._remove_from_indexes(task)
        self._update_indexes(task)

        # Keep current_task pointing to this task (focus pointer semantics)
        # current_task tracks the most recently operated task, even after completion

        self._save_tasks()
        return True

    def get_tasks_by_chat(
        self,
        chat_id: str,
        status: str = None,
        include_todos: bool = True
    ) -> list[dict]:
        """Get all tasks for a chat"""
        task_ids = self.by_chat.get(chat_id, [])
        tasks = []

        for tid in task_ids:
            task = self.tasks.get(tid)
            if not task:
                continue

            if status and task.status != status:
                continue

            task_dict = task.to_dict()

            if include_todos:
                task_dict["todos"] = [
                    self.todos[tid].to_dict()
                    for tid in task.todo_ids
                    if tid in self.todos
                ]

            tasks.append(task_dict)

        return tasks

    def get_todos_for_task(self, task_id: str) -> list[dict]:
        """Get all todos for a task"""
        task = self.tasks.get(task_id)
        if not task:
            return []

        return [
            self.todos[tid].to_dict()
            for tid in task.todo_ids
            if tid in self.todos
        ]

    def get_summary(self, chat_id: str = None) -> dict:
        """Get summary statistics"""
        if chat_id:
            task_ids = self.by_chat.get(chat_id, [])
            tasks = [self.tasks[tid] for tid in task_ids if tid in self.tasks]
        else:
            tasks = list(self.tasks.values())

        return {
            "total": len(tasks),
            "pending": sum(1 for t in tasks if t.status == "pending"),
            "in_progress": sum(1 for t in tasks if t.status == "in_progress"),
            "completed": sum(1 for t in tasks if t.status == "completed")
        }
