"""Todo management for Pantheon CLI with Claude Code style display"""

import json
import uuid
import atexit
import signal
import sys
from pathlib import Path
from typing import Dict, List, Optional
from ...utils.log import logger
from rich.text import Text

class Todo:
    def __init__(self, content: str, status: str = "pending", id: Optional[str] = None):
        self.id = id or str(uuid.uuid4())[:8]
        self.content = content
        self.status = status  # pending, in_progress, completed
    
    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "content": self.content,
            "status": self.status
        }
    
    @classmethod
    def from_dict(cls, data: Dict) -> 'Todo':
        return cls(
            content=data["content"],
            status=data["status"],
            id=data["id"]
        )

class TodoManager:
    def __init__(self, workspace_path: Path, auto_cleanup_on_exit: bool = True):
        self.workspace_path = workspace_path
        self.todo_file = workspace_path / ".pantheon_todos.json"
        self.todos: List[Todo] = []
        self._last_display_hash = None  # Prevent duplicate displays
        self.auto_cleanup_on_exit = auto_cleanup_on_exit
        self._load_todos()
        
        # Register cleanup handlers if enabled
        if auto_cleanup_on_exit:
            self._register_cleanup_handlers()
    
    def _register_cleanup_handlers(self):
        """Register cleanup handlers for program exit"""
        # Register atexit handler for normal exit
        atexit.register(self._cleanup_on_exit)
        
        # Register signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            self._cleanup_on_exit()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
        signal.signal(signal.SIGTERM, signal_handler)  # Termination signal
    
    def _cleanup_on_exit(self):
        """Cleanup todos on program exit"""
        if not self.todos:
            return
        
        try:
            # Option 1: Clear all todos completely (aggressive cleanup)
            # self.todos = []
            # self._save_todos()
            
            # Option 2: Only clear completed todos (conservative cleanup)
            initial_count = len(self.todos)
            self.todos = [t for t in self.todos if t.status != "completed"]
            
            # Option 3: Clear todos based on age or pattern (smart cleanup)
            # Keep only in-progress and recent pending todos
            important_todos = [
                t for t in self.todos 
                if t.status == "in_progress" 
                or (t.status == "pending" and not self._is_atac_pipeline_todo(t.content))
            ]
            
            if len(important_todos) < initial_count:
                self.todos = important_todos
                self._save_todos()
                print(f"📝 Cleaned up {initial_count - len(important_todos)} completed/ATAC pipeline todos on exit")
        
        except Exception as e:
            # Don't let cleanup failures crash the program
            print(f"Warning: Todo cleanup failed: {e}")
    
    def _is_atac_pipeline_todo(self, content: str) -> bool:
        """Check if a todo is part of ATAC-seq pipeline"""
        atac_keywords = [
            "atac-seq", "fastqc", "trim galore", "bwa-mem", "bowtie2", 
            "macs2", "peak calling", "bam filtering", "coverage track"
        ]
        content_lower = content.lower()
        return any(keyword in content_lower for keyword in atac_keywords)
    
    def _load_todos(self):
        """Load todos from file"""
        if self.todo_file.exists():
            try:
                with open(self.todo_file, 'r') as f:
                    data = json.load(f)
                    self.todos = [Todo.from_dict(todo_data) for todo_data in data]
            except (json.JSONDecodeError, KeyError):
                self.todos = []
    
    def _save_todos(self):
        """Save todos to file"""
        with open(self.todo_file, 'w') as f:
            json.dump([todo.to_dict() for todo in self.todos], f, indent=2)
    
    def add_todo(self, content: str, status: str = "pending") -> str:
        """Add a new todo and return its ID"""
        todo = Todo(content, status)
        self.todos.append(todo)
        self._save_todos()
        return todo.id
    
    def update_todo(self, todo_id: str, status: str) -> bool:
        """Update todo status"""
        for todo in self.todos:
            if todo.id == todo_id:
                todo.status = status
                self._save_todos()
                return True
        return False
    
    def remove_todo(self, todo_id: str) -> bool:
        """Remove a todo"""
        for i, todo in enumerate(self.todos):
            if todo.id == todo_id:
                del self.todos[i]
                self._save_todos()
                return True
        return False
    
    def get_status_symbol(self, status: str) -> str:
        """Get Unicode symbol for todo status"""
        symbols = {
            "pending": "☐",      # Empty checkbox
            "in_progress": "◐",  # Half-filled circle  
            "completed": "☑"     # Checked checkbox
        }
        return symbols.get(status, "☐")
    
    def display_todos(self, show_completed: bool = True, force_display: bool = False):
        """Display todos in Claude Code style"""
        if not self.todos:
            return
        
        # Filter todos
        display_todos = self.todos
        if not show_completed:
            display_todos = [t for t in self.todos if t.status != "completed"]
        
        if not display_todos:
            return
        
        # Create a hash of the current display state to prevent duplicates
        todo_states = [(t.id, t.content, t.status) for t in display_todos]
        current_hash = hash(str(todo_states))
        
        # Skip display if same as last time (unless forced)
        if not force_display and current_hash == self._last_display_hash:
            return
        
        self._last_display_hash = current_hash
        
        # Create Claude Code style header
        logger.info("")
        logger.info("[bold]Update Todos[/bold]")
        
        # Display each todo with proper indentation and symbols
        for todo in display_todos:
            symbol = self.get_status_symbol(todo.status)
            
            # Color coding based on status
            if todo.status == "completed":
                color = "green"
                content_style = "dim"
            elif todo.status == "in_progress": 
                color = "yellow"
                content_style = "bold"
            else:  # pending
                color = "white"
                content_style = "normal"
            
            # Create the display line with proper indentation
            indent = "  ⎿  "  # Claude Code style indent
            
            # Build the line with color
            line = Text()
            line.append(indent, style="dim")
            line.append(symbol + " ", style=color)
            line.append(todo.content, style=content_style)
            
            logger.info("", rich=line)
        
        logger.info("")
    
    def get_todos_summary(self) -> Dict[str, int]:
        """Get summary of todos by status"""
        summary = {"pending": 0, "in_progress": 0, "completed": 0}
        for todo in self.todos:
            summary[todo.status] = summary.get(todo.status, 0) + 1
        return summary
    
    def clear_completed(self):
        """Remove all completed todos"""
        self.todos = [t for t in self.todos if t.status != "completed"]
        self._save_todos()

# Global todo manager instance (will be initialized by CLI)
todo_manager: Optional[TodoManager] = None

def init_todo_manager(workspace_path: Path):
    """Initialize the global todo manager"""
    global todo_manager
    todo_manager = TodoManager(workspace_path)

def add_todo(content: str, status: str = "pending") -> Optional[str]:
    """Add a todo using the global manager"""
    if todo_manager:
        return todo_manager.add_todo(content, status)
    return None

def update_todo_status(todo_id: str, status: str) -> bool:
    """Update todo status using the global manager"""
    if todo_manager:
        return todo_manager.update_todo(todo_id, status)
    return False

def display_todos(show_completed: bool = True):
    """Display todos using the global manager"""
    if todo_manager:
        todo_manager.display_todos(show_completed)

def get_todos_count() -> int:
    """Get total number of todos"""
    if todo_manager:
        return len(todo_manager.todos)
    return 0