"""
Adapter for Single Cell Benchmark.

Wraps Pantheon Team to execute benchmark tasks and extract answers.
Reference: benchmarks/bixbench/adapter.py
"""
import json
import re
import uuid
import asyncio
from pathlib import Path
from typing import Optional, Any

def submit_answer(answer: str) -> dict:
    """Submit the final answer for the task.
    
    Call this tool ONCE after completing the analysis to submit your answer.
    
    Args:
        answer: The final answer to the task. Use the string representation for numbers, booleans, or strings (e.g., "1.23", "True", "Mast cells").
    
    Returns:
        Confirmation of submission with interrupt signal to terminate the session.
    """
    return {"status": "submitted", "answer": answer, "interrupt": True}

class SingleCellBenchmarkAdapter:
    """Adapter to run Single Cell benchmark tasks using Pantheon Team.
    
    Mimics PantheonBixBenchAdapter architecture.
    """
    
    def __init__(
        self,
        model_name: str = "gemini/gemini-3-flash-preview",
        enable_learning: bool = False,
        workspace_path: str = None,
        learning_config: dict = None,
        team: str = "default",
        injection_mode: str = "auto",
    ):
        self.model_name = model_name
        self.enable_learning = enable_learning
        self.workspace_path = workspace_path or str(Path.cwd())
        self.learning_config = learning_config
        self.team_name = team
        self.injection_mode = injection_mode
        self._team = None
        self._endpoint = None
    
    async def _ensure_endpoint(self):
        """Initialize endpoint for toolset access (like start.py)."""
        if self._endpoint is not None:
            return self._endpoint
        
        from pantheon.chatroom.start import _start_endpoint_embedded
        
        # Generate unique id_hash for this benchmark run
        endpoint_id_hash = str(uuid.uuid4())
        
        # Start endpoint in embedded mode at the specified workspace path
        self._endpoint = await _start_endpoint_embedded(
            endpoint_id_hash=endpoint_id_hash,
            workspace_path=self.workspace_path,
            log_level="WARNING",
        )
        
        return self._endpoint
    
    async def _create_fresh_team(self):
        """Create a fresh team instance for a task."""
        from pantheon.factory import create_team_from_template
        from pantheon.settings import get_settings
        
        # Ensure endpoint is ready
        endpoint = await self._ensure_endpoint()
        
        # Prepare learning config if enabled (Exact BixBench logic)
        learning_config = None
        if self.enable_learning or self.learning_config:
            settings = get_settings()
            learning_config = settings.get_learning_config().copy()
            
            # Apply injection mode settings
            if self.enable_learning:
                learning_config["enable_learning"] = False
                learning_config["enable_injection"] = True
                
                # Configure based on injection mode
                if self.injection_mode == "static":
                    learning_config["enable_injection"] = True
                    learning_config["enable_dynamic_injection"] = False
                    learning_config["static_injection_sections"] = ["*"]
                elif self.injection_mode == "dynamic":
                    learning_config["enable_injection"] = False
                    learning_config["enable_dynamic_injection"] = True
                    learning_config["static_injection_sections"] = []
                # For "auto" mode, use defaults from settings
            else:
                learning_config["enable_learning"] = False
                learning_config["enable_injection"] = False
                learning_config["enable_dynamic_injection"] = False

            # Merge user-provided config (takes precedence)
            if self.learning_config:
                learning_config.update(self.learning_config)

        # Create team using factory (Standard BixBench way)
        team = await create_team_from_template(
            endpoint_service=endpoint,
            template_id=self.team_name,
            learning_config=learning_config,
            enable_mcp=False, # Disable MCP for benchmark as per BixBench default
        )
        
        # --- Model Override Logic (Custom addition to support user request) ---
        # Note: BixBench does NOT natively use self.model_name in _create_fresh_team.
        # We perform post-creation patching to support the requested model override.
        if self.model_name:
            for agent in team.team_agents:
                agent.models = [self.model_name]
                agent.model = self.model_name
                
        # Register benchmark-specific tools on leader agent
        leader_agent = team.team_agents[0]
        leader_agent.tool(submit_answer)
        
        return team

    async def run_task(
        self,
        prompt: str,
        task_id: str,
        workspace_subdir: str = None,
        verbose: bool = True,
    ) -> dict:
        """Execute a benchmark task."""
        
        # Create fresh team
        team = await self._create_fresh_team()
        
        from pantheon.internal.memory import Memory
        
        memory = Memory(name=f"bench_{task_id}")
        
        # --- Visualization Helpers (Ported from BixBench) ---
        step_count = [0]

        def truncate_value(val, max_len: int = 100):
            if val is None: return "None"
            if isinstance(val, str):
                return val[:max_len] + "..." if len(val) > max_len else val
            elif isinstance(val, (int, float, bool)):
                return str(val)
            elif isinstance(val, dict):
                items = []
                for k, v in list(val.items())[:5]:
                    v_str = truncate_value(v, max_len=50)
                    items.append(f"{k}: {v_str}")
                result = "{" + ", ".join(items) + "}"
                return result if len(result) <= max_len else result[:max_len] + "...}"
            elif isinstance(val, (list, tuple)):
                if len(val) == 0: return "[]"
                items = [truncate_value(v, max_len=30) for v in val[:3]]
                result = "[" + ", ".join(items) + "]"
                return result if len(result) <= max_len else result[:max_len] + "...]"
            s = str(val)
            return s[:max_len] + "..." if len(s) > max_len else s

        def format_tool_args(args):
            if not args: return ""
            try:
                if isinstance(args, str):
                    import json
                    args = json.loads(args)
                if isinstance(args, dict):
                    parts = []
                    for k, v in list(args.items())[:4]:
                        v_str = truncate_value(v, max_len=60)
                        parts.append(f"  {k}={v_str}")
                    return "\n" + "\n".join(parts)
            except Exception:
                s = str(args)
                return "\n  " + (s[:100] + "..." if len(s) > 100 else s)
            return ""

        def format_tool_result(content):
            if content is None: return "(no result)"
            try:
                if isinstance(content, str):
                    import json
                    try:
                        content = json.loads(content)
                    except json.JSONDecodeError:
                        return content[:150] + "..." if len(content) > 150 else content
                if isinstance(content, dict):
                    if "error" in content: return f"ERROR: {truncate_value(content['error'], 100)}"
                    if "result" in content: return truncate_value(content['result'], 150)
                    if "output" in content: return truncate_value(content['output'], 150)
                    if "content" in content: return truncate_value(content['content'], 150)
                    return truncate_value(content, 150)
                return truncate_value(content, 150)
            except Exception:
                s = str(content)
                return s[:150] + "..." if len(s) > 150 else s

        def process_step_message(msg: dict):
            step_count[0] += 1
            if not verbose: return
            
            agent_name = msg.get("agent_name", "Agent") or "Agent"
            role = msg.get("role", "") or ""
            
            if role == "tool_call":
                tool_name = msg.get("tool_name", "tool") or msg.get("name", "tool") or "tool"
                args = msg.get("arguments") or msg.get("args") or msg.get("input")
                args_str = format_tool_args(args)
                print(f"    [{step_count[0]:02d}] 🔧 {agent_name} → {tool_name}{args_str}")
            elif role == "tool_result":
                tool_name = msg.get("tool_name", "") or msg.get("name", "")
                status = "✓" if not msg.get("error") else "✗"
                content = msg.get("content") or msg.get("result") or msg.get("raw_content")
                result_str = format_tool_result(content)
                tool_label = f"{tool_name} " if tool_name else ""
                print(f"    [{step_count[0]:02d}] {status} {tool_label}→ {result_str}")
            elif role == "tool":
                tool_name = msg.get("tool_name", "") or msg.get("name", "") or "tool"
                content = msg.get("content") or msg.get("raw_content")
                result_str = format_tool_result(content)
                print(f"    [{step_count[0]:02d}] 🔧 {agent_name} → {tool_name}: {result_str}")
            elif role == "assistant":
                tool_calls = msg.get("tool_calls") or []
                if tool_calls:
                    for tc in tool_calls:
                        if isinstance(tc, dict):
                            func = tc.get("function", tc)
                            tc_name = func.get("name", "tool") or "tool"
                            tc_args = func.get("arguments")
                            args_str = format_tool_args(tc_args)
                            print(f"    [{step_count[0]:02d}] 🔧 {agent_name} → {tc_name}{args_str}")
                else:
                    content = msg.get("content") or ""
                    if content:
                        preview = content[:80].replace("\n", " ") + "..." if len(content) > 80 else content.replace("\n", " ")
                        print(f"    [{step_count[0]:02d}] 💬 {agent_name}: {preview}")
                    else:
                        print(f"    [{step_count[0]:02d}] 💬 {agent_name}: (no content)")
            else:
                print(f"    [{step_count[0]:02d}] 📝 {agent_name} ({role})")

        # Run with callback
        result = await team.run(
            msg=prompt, 
            memory=memory,
            process_step_message=process_step_message
        )
        
        # Extract answer
        messages = memory.get_messages()
        answer = self._extract_answer(messages, result)
        
        if not answer:
             # Retry logic (BixBench style)
             print("    ⚠️  No answers submitted. Triggering retry turn...")
             retry_msg = "You have verified the task but have not submitted the final answer via the `submit_answer` tool. Please submit your conclusion immediately."
             result = await team.run(
                 msg=retry_msg,
                 memory=memory,
                 process_step_message=process_step_message,
             )
             messages = memory.get_messages()
             answer = self._extract_answer(messages, result)

        return {
            "answer": answer,
            "messages": messages,
            "raw_result": str(result)[:2000]
        }

    def _extract_answer(self, messages, result):
        """Extract answer from submit_answer tool call."""
        final_answer = None
        
        # Reverse search for submit_answer tool call
        for msg in reversed(messages):
            # Check for tool_calls attribute (AgnetMessage object) or dict
            tool_calls = []
            if isinstance(msg, dict):
                tool_calls = msg.get("tool_calls") or []
            elif hasattr(msg, "tool_calls"):
                tool_calls = msg.tool_calls or []
            elif hasattr(msg, "model_dump"):
                tool_calls = msg.model_dump().get("tool_calls") or []
                
            for tc in tool_calls:
                 # Helper to access dictionary or object attributes
                 func_name = ""
                 func_args = ""
                 
                 if isinstance(tc, dict):
                     func = tc.get("function", tc)
                     func_name = func.get("name", "")
                     func_args = func.get("arguments", "{}")
                 elif hasattr(tc, "function"):
                     func_name = tc.function.name
                     func_args = tc.function.arguments
                     
                 if func_name == 'submit_answer':
                     try:
                         if isinstance(func_args, str):
                             args = json.loads(func_args)
                         else:
                             args = func_args
                         final_answer = args.get('answer')
                         if final_answer: break
                     except:
                         pass
            if final_answer: break
            
        return final_answer

    async def cleanup_notebook_sessions(self):
        """Cleanup all notebook kernel sessions to free memory.
        Reference: benchmarks/bixbench/adapter.py:cleanup_notebook_sessions
        """
        if self._endpoint is None:
            return 0
        
        try:
            notebook_toolset = None
            for service_id, service_info in self._endpoint.toolset_manager.services.items():
                if service_info.get("name") == "integrated_notebook":
                    notebook_toolset = service_info.get("instance")
                    break
            
            if notebook_toolset and hasattr(notebook_toolset, 'kernel_toolset') and notebook_toolset.kernel_toolset:
                kernel_toolset = notebook_toolset.kernel_toolset
                session_count = len(kernel_toolset.sessions)
                
                if session_count == 0: return 0
                
                for session_id in list(kernel_toolset.sessions.keys()):
                    try:
                        await kernel_toolset.shutdown_session(session_id)
                    except Exception as e:
                        print(f"    ⚠️ Failed to shutdown session {session_id[:8]}: {e}")
                
                notebook_toolset.notebook_contexts.clear()
                if hasattr(notebook_toolset, '_save_contexts'):
                    await notebook_toolset._save_contexts()
                
                return session_count
            return 0
        except Exception as e:
            print(f"    ⚠️ Failed to cleanup notebook sessions: {e}")
            return 0

    async def cleanup(self):
        """Cleanup resources."""
        if self._endpoint:
            await self._endpoint.cleanup()
            self._endpoint = None
