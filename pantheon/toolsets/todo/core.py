"""Todo toolset for Pantheon"""

from typing import Optional, List, Dict
from pathlib import Path
from ...toolset import ToolSet, tool
from ...utils.log import logger
from .todo_manager import TodoManager

class TodoToolSet(ToolSet):
    """Todo management toolset for Pantheon"""
    
    def __init__(self, name: str, workspace_path: Path = None, auto_cleanup_on_exit: bool = True, **kwargs):
        super().__init__(name, **kwargs)
        self.workspace_path = workspace_path or Path.cwd()
        self.todo_manager = TodoManager(self.workspace_path, auto_cleanup_on_exit=auto_cleanup_on_exit)
    
    def _check_for_similar_todos(self, content: str) -> list:
        """Check for similar existing todos to prevent duplicates"""
        similar_todos = []
        content_lower = content.lower()
        
        for todo in self.todo_manager.todos:
            todo_content_lower = todo.content.lower()
            
            # Check for exact match
            if todo.content == content or f"[MAIN] {content}" == todo.content:
                similar_todos.append(todo)
                continue
                
            # Check for similar ATAC-seq tasks
            if "atac" in content_lower and "atac" in todo_content_lower:
                # Extract key words from both contents
                content_words = set(content_lower.split())
                todo_words = set(todo_content_lower.split())
                
                # Check for high similarity (common words)
                common_words = content_words.intersection(todo_words)
                similarity_ratio = len(common_words) / max(len(content_words), len(todo_words), 1)
                
                if similarity_ratio > 0.7:  # 70% similarity threshold
                    similar_todos.append(todo)
        
        return similar_todos

    @tool
    async def add_todo(self, content: str, status: str = "pending", auto_break_down: bool = False, auto_start: bool = True, prevent_duplicates: bool = True) -> dict:
        """Add a new todo item, optionally breaking it down into steps and starting work.
        
        Args:
            content: Description of the todo item
            status: Status of the todo (pending, in_progress, completed)
            auto_break_down: Whether to break down complex tasks into subtasks (default: True)
            auto_start: Whether to automatically start the first task (default: True)
            prevent_duplicates: Whether to check for and prevent duplicate todos (default: True)
        
        Returns:
            Dictionary with success status and todo ID
        """
        if not self.todo_manager:
            return {"success": False, "error": "Todo manager not initialized"}
        
        # Check for duplicates if enabled
        if prevent_duplicates:
            similar_todos = self._check_for_similar_todos(content)
            if similar_todos:
                # Found similar todos, don't create a duplicate
                self.todo_manager.display_todos(show_completed=True)
                return {
                    "success": True,
                    "todo_id": similar_todos[0].id,
                    "message": f"Similar task already exists: {similar_todos[0].content}. Skipping duplicate creation.",
                    "duplicate_prevented": True
                }
        
        # Auto-break down complex tasks (enabled by default)
        if auto_break_down and self._is_complex_task(content):
            # Check if this main task already exists to avoid duplicates
            main_task_content = f"[MAIN] {content}"
            existing_main = None
            for todo in self.todo_manager.todos:
                if todo.content == main_task_content:
                    existing_main = todo
                    break
            
            if existing_main:
                # Task already exists, just display current state
                self.todo_manager.display_todos(show_completed=True)
                return {
                    "success": True,
                    "todo_id": existing_main.id,
                    "message": f"Task already exists: {content}. Showing current progress."
                }
            
            subtasks = self._break_down_task(content)
            main_todo_id = self.todo_manager.add_todo(main_task_content, "pending")
            
            subtask_ids = []
            for i, subtask in enumerate(subtasks, 1):
                subtask_id = self.todo_manager.add_todo(f"  └ Step {i}: {subtask}", "pending")
                subtask_ids.append(subtask_id)
            
            result = {
                "success": True,
                "todo_id": main_todo_id,
                "subtask_ids": subtask_ids,
                "message": f"Added main task with {len(subtasks)} subtasks: {content}"
            }
            
            # Auto-start the first subtask if enabled
            if auto_start and subtask_ids:
                first_subtask_id = subtask_ids[0]
                self.todo_manager.update_todo(first_subtask_id, "in_progress")
                first_subtask = next(t for t in self.todo_manager.todos if t.id == first_subtask_id)
                
                result["started_task"] = {
                    "id": first_subtask_id,
                    "content": first_subtask.content,
                    "instructions": self._get_task_instructions(first_subtask.content)
                }
                result["message"] += f"\n🚀 Started working on: {first_subtask.content}"
                result["message"] += f"\n📝 Instructions: {self._get_task_instructions(first_subtask.content)}"
            
            # Display updated todos only once at the end (show completed to see checkmarks)
            self.todo_manager.display_todos(show_completed=True)
            
            return result
        else:
            todo_id = self.todo_manager.add_todo(content, status)
            
            # Display updated todos (show completed to see checkmarks)
            self.todo_manager.display_todos(show_completed=True)
            
            result = {
                "success": True,
                "todo_id": todo_id,
                "message": f"Added todo: {content}"
            }
            
            # Auto-start if it's the only pending task and auto_start is enabled
            if auto_start and status == "pending":
                pending_count = len([t for t in self.todo_manager.todos if t.status == "pending"])
                if pending_count == 1:  # Only this new task is pending
                    self.todo_manager.update_todo(todo_id, "in_progress")
                    self.todo_manager.display_todos(show_completed=True)
                    
                    result["started_task"] = {
                        "id": todo_id,
                        "content": content,
                        "instructions": self._get_task_instructions(content)
                    }
                    result["message"] += f"\n🚀 Started working on: {content}"
                    result["message"] += f"\n📝 Instructions: {self._get_task_instructions(content)}"
            
            return result
    
    def _is_complex_task(self, content: str) -> bool:
        """Check if a task is complex and should be broken down"""
        # Disabled automatic task breakdown detection
        return False
    
    def _break_down_task(self, content: str) -> list:
        """Break down a complex task into subtasks"""
        content_lower = content.lower()
        
        if "generate figure" in content_lower or "create plot" in content_lower:
            if "python" in content_lower:
                return [
                    "Import libraries and create sample data",
                    "Generate basic plot/visualization", 
                    "Add titles, labels and formatting",
                    "Save figure to file and display"
                ]
            else:
                return [
                    "Load and prepare data",
                    "Choose appropriate visualization type", 
                    "Create the plot/figure",
                    "Add labels, titles, and formatting",
                    "Save the figure"
                ]
        elif "analyze data" in content_lower:
            return [
                "Load and inspect the data",
                "Clean and preprocess data",
                "Perform exploratory analysis",
                "Run statistical analysis",
                "Generate summary report"
            ]
        elif "seurat" in content_lower:
            return [
                "Load single-cell data",
                "Quality control and filtering", 
                "Normalization and scaling",
                "Find variable features and run PCA",
                "Clustering and UMAP visualization",
                "Find marker genes"
            ]
        else:
            # Generic breakdown - make steps more actionable
            task_words = content_lower.split()
            if any(word in task_words for word in ['analyze', 'analysis', 'examine']):
                return [
                    "Load or prepare the data",
                    "Perform exploratory data analysis", 
                    "Run statistical analysis",
                    "Generate summary report"
                ]
            elif any(word in task_words for word in ['create', 'build', 'generate', 'make']):
                return [
                    "Set up the environment and tools",
                    "Create the main component/output",
                    "Test and validate the result",
                    "Document and finalize"
                ]
            else:
                # Last resort - more actionable generic steps
                return [
                    "Set up and prepare environment",
                    "Execute the main task",
                    "Validate and test results", 
                    "Complete and document"
                ]
    
    @tool
    async def update_todo_status(self, todo_id: str, status: str) -> dict:
        """Update the status of a todo item.
        
        Args:
            todo_id: ID of the todo to update
            status: New status (pending, in_progress, completed)
        
        Returns:
            Dictionary with success status
        """
        if not self.todo_manager:
            return {"success": False, "error": "Todo manager not initialized"}
        
        success = self.todo_manager.update_todo(todo_id, status)
        
        if success:
            # Display updated todos (show completed to see checkmarks)
            self.todo_manager.display_todos(show_completed=True, force_display=True)
            return {
                "success": True,
                "message": f"Updated todo {todo_id} to {status}"
            }
        else:
            return {
                "success": False,
                "error": f"Todo {todo_id} not found"
            }
    
    @tool
    async def show_todos(self, show_completed: bool = False) -> dict:
        """Display all todos in Claude Code style.
        
        Args:
            show_completed: Whether to show completed todos
        
        Returns:
            Dictionary with todos summary
        """
        if not self.todo_manager:
            return {"success": False, "error": "Todo manager not initialized"}
        
        self.todo_manager.display_todos(show_completed)
        summary = self.todo_manager.get_todos_summary()
        
        return {
            "success": True,
            "summary": summary,
            "total_todos": len(self.todo_manager.todos)
        }
    
    @tool
    async def complete_todo(self, todo_id: str) -> dict:
        """Mark a todo as completed.
        
        Args:
            todo_id: ID of the todo to complete
        
        Returns:
            Dictionary with success status
        """
        return await self.update_todo_status(todo_id, "completed")
    
    @tool
    async def start_todo(self, todo_id: str) -> dict:
        """Mark a todo as in progress.
        
        Args:
            todo_id: ID of the todo to start
        
        Returns:
            Dictionary with success status
        """
        return await self.update_todo_status(todo_id, "in_progress")
    
    @tool
    async def remove_todo(self, todo_id: str) -> dict:
        """Remove a todo completely.
        
        Args:
            todo_id: ID of the todo to remove
        
        Returns:
            Dictionary with success status
        """
        if not self.todo_manager:
            return {"success": False, "error": "Todo manager not initialized"}
        
        success = self.todo_manager.remove_todo(todo_id)
        
        if success:
            # Display updated todos (show completed to see checkmarks)
            self.todo_manager.display_todos(show_completed=True)
            return {
                "success": True,
                "message": f"Removed todo {todo_id}"
            }
        else:
            return {
                "success": False, 
                "error": f"Todo {todo_id} not found"
            }
    
    @tool
    async def get_next_todo(self) -> dict:
        """Get the next pending todo to work on.
        
        Returns:
            Dictionary with the next todo information
        """
        if not self.todo_manager:
            return {"success": False, "error": "Todo manager not initialized"}
        
        # Find first pending todo
        for todo in self.todo_manager.todos:
            if todo.status == "pending":
                return {
                    "success": True,
                    "todo_id": todo.id,
                    "content": todo.content,
                    "message": f"Next todo: {todo.content}"
                }
        
        # No pending todos
        return {
            "success": False,
            "message": "No pending todos found"
        }
    
    @tool
    async def work_on_next_todo(self) -> dict:
        """Start working on the next pending todo.
        
        Returns:
            Dictionary with the started todo information
        """
        if not self.todo_manager:
            return {"success": False, "error": "Todo manager not initialized"}
        
        # Find first pending todo and mark as in progress
        for todo in self.todo_manager.todos:
            if todo.status == "pending":
                self.todo_manager.update_todo(todo.id, "in_progress")
                self.todo_manager.display_todos(show_completed=True)
                
                return {
                    "success": True,
                    "todo_id": todo.id,
                    "content": todo.content,
                    "message": f"Now working on: {todo.content}",
                    "instructions": self._get_task_instructions(todo.content)
                }
        
        # No pending todos
        return {
            "success": False,
            "message": "No pending todos to work on"
        }
    
    def _get_task_instructions(self, content: str) -> str:
        """Get specific instructions for a task"""
        content_lower = content.lower()
        
        if "setup reference genome" in content_lower and ("hg38" in content_lower or "bowtie2" in content_lower):
            return "Use atac.check_genome_setup('human', 'hg38') to verify status, then atac.setup_genome_resources('human', 'hg38') if needed."
        elif "load and prepare data" in content_lower:
            return "Use run_python or run_r to load your data. Check data shape, types, and look for missing values."
        elif "quality control" in content_lower and "fastqc" in content_lower:
            return "Use atac.run_fastqc() to perform quality control analysis on FASTQ files."
        elif "adapter trimming" in content_lower:
            return "Use atac.trim_adapters() to remove adapter sequences from FASTQ files."
        elif "genome alignment" in content_lower and "bowtie2" in content_lower:
            return "Use atac.auto_align_fastq() for fully automated alignment, or atac.align_bowtie2() for manual control."
        elif "bam filtering" in content_lower:
            return "Use atac.process_bam_smart() for automated BAM filtering (duplicate removal step has been disabled by default), or manually use atac.filter_bam()."
        elif "peak calling" in content_lower and "macs2" in content_lower:
            return "Use atac.call_peaks_macs2() to identify ATAC-seq peaks in the filtered BAM file."
        elif "coverage track" in content_lower:
            return "Use atac.bam_to_bigwig() to generate coverage visualization tracks."
        elif "qc report" in content_lower:
            return "Use atac.generate_atac_qc_report() to create comprehensive quality control report."
        elif "create plot" in content_lower or "generate figure" in content_lower:
            return "Use run_python (matplotlib/seaborn) or run_r (ggplot2) to create your visualization."
        elif "normalization" in content_lower:
            return "Normalize the data using appropriate methods (log normalization, SCTransform for Seurat)."
        elif "clustering" in content_lower:
            return "Find neighbors, perform clustering, and run dimensionality reduction (UMAP/t-SNE)."
        else:
            return f"Work on: {content}. Use appropriate tools (run_python, run_r, shell, atac.*) as needed."
    
    def _should_auto_execute(self, content: str) -> bool:
        """Check if a task should be automatically executed based on keywords"""
        content_lower = content.lower()
        
        # Action verbs that suggest executable tasks
        action_verbs = ['load', 'create', 'generate', 'run', 'execute', 'import', 'save', 
                       'search', 'find', 'read', 'write', 'analyze', 'process', 'install',
                       'setup', 'configure', 'fetch', 'download', 'plot', 'visualize']
        
        # Check if content contains actionable verbs
        words = content_lower.split()
        has_action = any(verb in words for verb in action_verbs)
        
        # Avoid abstract planning tasks
        abstract_indicators = ['plan', 'approach', 'strategy', 'review', 'validate', 
                              'consider', 'think', 'decide', 'choose']
        is_abstract = any(indicator in words for indicator in abstract_indicators)
        
        return has_action and not is_abstract
    
    def _get_auto_execution_code(self, content: str) -> str:
        """Generate intelligent auto-execution code based on task content"""
        content_lower = content.lower()
        words = content_lower.split()
        
        # Return suggestion message instead of hardcoded execution
        # Let the AI system decide what to execute based on the task
        execution_suggestions = []
        
        # Detect task type and suggest appropriate tools
        if any(word in words for word in ['import', 'load', 'create', 'generate']) and any(word in words for word in ['data', 'dataset']):
            execution_suggestions.append("SUGGESTED_TOOL: run_python - for data loading/creation")
            
        if any(word in words for word in ['plot', 'figure', 'visualize', 'chart', 'graph']):
            execution_suggestions.append("SUGGESTED_TOOL: run_python - matplotlib/seaborn for plotting")
            
        if any(word in words for word in ['search', 'find']) and any(word in words for word in ['file', 'files']):
            execution_suggestions.append("SUGGESTED_TOOL: grep or glob - for file searching")
            
        if any(word in words for word in ['list', 'show']) and any(word in words for word in ['directory', 'folder', 'files']):
            execution_suggestions.append("SUGGESTED_TOOL: ls - for directory listing")
            
        if any(word in words for word in ['fetch', 'get', 'download']) and any(word in words for word in ['web', 'url', 'website']):
            execution_suggestions.append("SUGGESTED_TOOL: web_fetch - for web content")
            
        if any(word in words for word in ['seurat', 'scanpy', 'single-cell']):
            execution_suggestions.append("SUGGESTED_TOOL: run_r - for single-cell analysis")
            
        # Return as instruction rather than hardcoded execution
        if execution_suggestions:
            return f"TASK_ANALYSIS: {' | '.join(execution_suggestions)}\nACTION_NEEDED: Use appropriate tool to accomplish this task"
        
        return ""
    
    @tool 
    async def mark_task_done(self, task_description: str = "") -> dict:
        """Quickly mark the current in-progress task as completed and move to next.
        This is simpler than complete_current_todo for manual use.
        
        Args:
            task_description: Optional description of what was completed
        
        Returns:
            Dictionary with completion status
        """
        if not self.todo_manager:
            return {"success": False, "error": "Todo manager not initialized"}
        
        # Find the current in-progress task
        current_task = None
        for todo in self.todo_manager.todos:
            if todo.status == "in_progress":
                current_task = todo
                break
        
        if not current_task:
            logger.info("[yellow]No task currently in progress[/yellow]")
            return {"success": False, "message": "No task currently in progress"}
        
        # Mark it as completed
        self.todo_manager.update_todo(current_task.id, "completed")
        
        # Show completion message
        completion_msg = f"Completed: {current_task.content}"
        if task_description:
            completion_msg += f" ({task_description})"
        logger.info(f"[green]✅ {completion_msg}[/green]")
        
        # Find and start next pending task
        next_task = None
        for todo in self.todo_manager.todos:
            if todo.status == "pending":
                next_task = todo
                break
        
        result = {
            "success": True,
            "completed_task": current_task.content,
            "message": f"\n✅ Completed: {current_task.content}"
        }
        
        if next_task:
            # Auto-start the next task
            self.todo_manager.update_todo(next_task.id, "in_progress")
            result["next_task"] = next_task.content
            result["message"] += f"\n🚀 Started next: {next_task.content}"
            logger.info(f"[cyan]🚀 Started next: {next_task.content}[/cyan]")
        else:
            result["message"] += "\n🎉 All tasks completed!"
            logger.info("[green]🎉 All tasks completed![/green]")
        
        # Force display the updated todos
        self.todo_manager.display_todos(show_completed=True, force_display=True)
        
        return result
    
    @tool
    async def execute_current_task(self) -> dict:
        """Analyze the current task and provide execution guidance.
        
        Returns:
            Dictionary with execution analysis and suggested actions
        """
        if not self.todo_manager:
            return {"success": False, "error": "Todo manager not initialized"}
        
        # Find the current in-progress task
        current_task = None
        for todo in self.todo_manager.todos:
            if todo.status == "in_progress":
                current_task = todo
                break
        
        if not current_task:
            return {"success": False, "message": "No task currently in progress"}
        
        # Analyze the task and provide suggestions
        task_content = current_task.content
        suggestions = self._get_auto_execution_code(task_content)
        instructions = self._get_task_instructions(task_content)
        
        if suggestions and "SUGGESTED_TOOL" in suggestions:
            return {
                "success": True,
                "task_id": current_task.id,
                "task_content": task_content,
                "analysis": suggestions,
                "instructions": instructions,
                "message": f"Task analysis complete for: {task_content}",
                "recommendation": "Use the suggested tools to accomplish this task, then call mark_task_done() when finished."
            }
        else:
            return {
                "success": True,
                "task_id": current_task.id,
                "task_content": task_content,
                "instructions": instructions,
                "message": f"Manual execution needed for: {task_content}",
                "recommendation": "Complete this task manually using appropriate tools, then call mark_task_done() when finished."
            }
    
    @tool
    async def complete_current_todo(self) -> dict:
        """Complete the currently in-progress todo and move to next.
        
        Returns:
            Dictionary with completion status and next todo
        """
        if not self.todo_manager:
            return {"success": False, "error": "Todo manager not initialized"}
        
        # Find in-progress todo and complete it
        completed_todo = None
        for todo in self.todo_manager.todos:
            if todo.status == "in_progress":
                self.todo_manager.update_todo(todo.id, "completed")
                completed_todo = todo
                break
        
        if not completed_todo:
            return {"success": False, "message": "No in-progress todo found"}
        
        # Check for next pending todo and automatically start it
        next_todo = None
        for todo in self.todo_manager.todos:
            if todo.status == "pending":
                next_todo = todo
                break
        
        result = {
            "success": True,
            "completed_todo": completed_todo.content,
            "message": f"\n✅ Completed: {completed_todo.content}"
        }
        
        if next_todo:
            # Automatically start the next task
            self.todo_manager.update_todo(next_todo.id, "in_progress")
            
            result["next_todo"] = {
                "id": next_todo.id,
                "content": next_todo.content,
                "instructions": self._get_task_instructions(next_todo.content)
            }
            result["message"] += f"\n🚀 Started next task: {next_todo.content}"
            result["message"] += f"\n📝 Instructions: {self._get_task_instructions(next_todo.content)}"
        else:
            result["message"] += "\n🎉 All todos completed!"
        
        self.todo_manager.display_todos(show_completed=True, force_display=True)
        
        return result
    
    @tool
    async def clear_all_todos(self) -> dict:
        """Remove all todos (useful for starting fresh).
        
        Returns:
            Dictionary with success status and count of removed todos
        """
        if not self.todo_manager:
            return {"success": False, "error": "Todo manager not initialized"}
        
        total_count = len(self.todo_manager.todos)
        self.todo_manager.todos = []
        self.todo_manager._save_todos()
        
        # Display updated todos (should be empty now)
        self.todo_manager.display_todos(show_completed=True)
        
        return {
            "success": True,
            "message": f"Removed all {total_count} todos. Starting fresh!"
        }
    
    @tool
    async def clear_completed_todos(self) -> dict:
        """Remove all completed todos.
        
        Returns:
            Dictionary with success status and count of removed todos
        """
        if not self.todo_manager:
            return {"success": False, "error": "Todo manager not initialized"}
        
        completed_count = len([t for t in self.todo_manager.todos if t.status == "completed"])
        self.todo_manager.clear_completed()
        
        # Display updated todos
        self.todo_manager.display_todos(show_completed=True)
        
        return {
            "success": True,
            "message": f"Removed {completed_count} completed todos"
        }
    
    @tool
    async def clear_atac_pipeline_todos(self) -> dict:
        """Remove ATAC-seq pipeline todos to prevent duplicates.
        
        Returns:
            Dictionary with success status and count of removed todos
        """
        if not self.todo_manager:
            return {"success": False, "error": "Todo manager not initialized"}
        
        initial_count = len(self.todo_manager.todos)
        atac_todos = [
            t for t in self.todo_manager.todos 
            if self.todo_manager._is_atac_pipeline_todo(t.content)
        ]
        
        # Remove ATAC pipeline todos
        self.todo_manager.todos = [
            t for t in self.todo_manager.todos 
            if not self.todo_manager._is_atac_pipeline_todo(t.content)
        ]
        self.todo_manager._save_todos()
        
        # Display updated todos
        self.todo_manager.display_todos(show_completed=True)
        
        return {
            "success": True,
            "message": f"Removed {len(atac_todos)} ATAC-seq pipeline todos",
            "removed_count": len(atac_todos)
        }
    
    @tool
    async def configure_auto_cleanup(self, enable: bool = True) -> dict:
        """Configure automatic todo cleanup on program exit.
        
        Args:
            enable: Whether to enable auto cleanup on exit
        
        Returns:
            Dictionary with success status
        """
        if not self.todo_manager:
            return {"success": False, "error": "Todo manager not initialized"}
        
        self.todo_manager.auto_cleanup_on_exit = enable
        
        if enable and not hasattr(self.todo_manager, '_cleanup_registered'):
            self.todo_manager._register_cleanup_handlers()
            self.todo_manager._cleanup_registered = True
        
        return {
            "success": True,
            "message": f"Auto cleanup on exit {'enabled' if enable else 'disabled'}"
        }