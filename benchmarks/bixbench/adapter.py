"""
Pantheon adapter for BixBench benchmark.

Wraps Pantheon Default Team to execute BixBench tasks and extract answers.
"""
import json
import re
import uuid
from pathlib import Path
from typing import Any, Optional


def submit_answer(answers: dict) -> dict:
    """Submit final answers for all BixBench questions.
    
    Call this tool ONCE after completing all analysis to submit your answers.
    
    Args:
        answers: A dictionary mapping question IDs to answers.
                 Example: {"q1": "0.0002", "q2": "BRCA1", "q3": "GO:0006955"}
                 
                 Answer format guidelines:
                 - Numbers: Use plain format (e.g., "0.0002" not "2E-04")
                 - Gene names: Use official symbols (e.g., "BRCA1")
                 - GO terms: Include prefix (e.g., "GO:0006955")
                 - Percentages: Can include % (e.g., "35.5%") or decimal
    
    Returns:
        Confirmation of submission with all answers recorded.
    """
    # Validate keys for extra quotes (common LLM mistake)
    malformed_keys = []
    for key in answers.keys():
        if key.startswith('"') or key.startswith("'") or key.endswith('"') or key.endswith("'"):
            malformed_keys.append(key)
    
    if malformed_keys:
        return {
            "status": "error",
            "message": f"Invalid answer format. Keys should not be quoted strings inside the JSON object. Found malformed keys: {malformed_keys}. Please resubmit with clean keys (e.g., 'q1' instead of '\"q1\"')."
        }

    return {"status": "submitted", "answers": answers, "count": len(answers), "interrupt": True}


class PantheonBixBenchAdapter:
    """Adapter to run BixBench tasks using Pantheon Team."""
    
    def __init__(
        self,
        model_name: str = "gemini/gemini-3-flash-preview",
        workspace_path: str = None,
        team: str = "default",
    ):
        self.model_name = model_name
        self.workspace_path = workspace_path or str(Path.cwd())
        self.team_name = team
        self._team = None
        self._endpoint = None
    
    async def _ensure_endpoint(self):
        """Initialize endpoint for toolset access (like start.py)."""
        if self._endpoint is not None:
            return self._endpoint
        
        from pantheon.chatroom.start import _start_endpoint_embedded
        
        # Generate unique id_hash for this benchmark run
        endpoint_id_hash = str(uuid.uuid4())
        
        # Start endpoint in embedded mode (streaming disabled by default)
        self._endpoint = await _start_endpoint_embedded(
            endpoint_id_hash=endpoint_id_hash,
            workspace_path=self.workspace_path,
            log_level="WARNING",  # Reduce noise during benchmark
        )
        
        return self._endpoint
    
    async def _create_fresh_team(self):
        """Create a fresh team instance for a task."""
        from pantheon.factory import create_team_from_template
        from pantheon.settings import get_settings
        
        # Ensure endpoint is ready (reused across tasks)
        endpoint = await self._ensure_endpoint()
        
        # Create team with endpoint_service
        team = await create_team_from_template(
            endpoint_service=endpoint,
            template_id=self.team_name,
            enable_mcp=False,
        )
        
        # Override model directly on agents
        if self.model_name:
            for agent in team.team_agents:
                agent.models = [self.model_name]
        
        # Register BixBench-specific tools on leader agent
        leader_agent = team.team_agents[0]
        leader_agent.tool(submit_answer)
        
        return team
    
    async def run_task(
        self,
        prompt: str,
        capsule_info: dict,
        max_steps: int = 40,
        verbose: bool = True,
        workspace_path: str = None,
    ) -> dict:
        """Execute a BixBench task.
        
        Args:
            prompt: The task prompt
            capsule_info: Capsule metadata (questions, data_folder, etc.)
            max_steps: Maximum steps for the agent
            verbose: Whether to print progress messages
            workspace_path: Path to workspace directory for this capsule
            
        Returns:
            Result dict with answers, trajectory info, and official-compatible format
        """
        if workspace_path:
            self.workspace_path = workspace_path
        
        # Create fresh team for this task to ensure isolation
        team = await self._create_fresh_team()
        
        from pantheon.internal.memory import Memory
        from pantheon.utils.display import print_agent_message
        
        # Create fresh memory for this task
        short_id = capsule_info['short_id']
        memory = Memory(name=f"bixbench_{short_id}")
        
        # Progress callback for real-time monitoring
        step_count = [0]  # Use list to allow mutation in closure
        
        def truncate_value(val, max_len: int = 100):
            """Truncate a value for display."""
            if val is None:
                return "None"
            if isinstance(val, str):
                if len(val) > max_len:
                    return val[:max_len] + "..."
                return val
            elif isinstance(val, (int, float, bool)):
                return str(val)
            elif isinstance(val, dict):
                items = []
                for k, v in list(val.items())[:5]:  # Max 5 keys
                    v_str = truncate_value(v, max_len=50)
                    items.append(f"{k}: {v_str}")
                result = "{" + ", ".join(items) + "}"
                if len(val) > 5:
                    result = result[:-1] + ", ...}"
                return result if len(result) <= max_len else result[:max_len] + "...}"
            elif isinstance(val, (list, tuple)):
                if len(val) == 0:
                    return "[]"
                items = [truncate_value(v, max_len=30) for v in val[:3]]
                result = "[" + ", ".join(items) + "]"
                if len(val) > 3:
                    result = result[:-1] + ", ...]"
                return result if len(result) <= max_len else result[:max_len] + "...]"
            else:
                s = str(val)
                return s[:max_len] + "..." if len(s) > max_len else s
        
        def format_tool_args(args):
            """Format tool arguments for display."""
            if not args:
                return ""
            try:
                if isinstance(args, str):
                    import json
                    args = json.loads(args)
                if isinstance(args, dict):
                    parts = []
                    for k, v in list(args.items())[:4]:  # Max 4 args
                        v_str = truncate_value(v, max_len=60)
                        parts.append(f"  {k}={v_str}")
                    return "\n" + "\n".join(parts)
            except Exception:
                s = str(args)
                return "\n  " + (s[:100] + "..." if len(s) > 100 else s)
            return ""
        
        def format_tool_result(content):
            """Format tool result for display."""
            if content is None:
                return "(no result)"
            try:
                if isinstance(content, str):
                    # Try to parse as JSON
                    import json
                    try:
                        content = json.loads(content)
                    except json.JSONDecodeError:
                        return content[:150] + "..." if len(content) > 150 else content
                
                if isinstance(content, dict):
                    # Look for common result fields
                    if "error" in content:
                        return f"ERROR: {truncate_value(content['error'], 100)}"
                    if "result" in content:
                        return truncate_value(content["result"], 150)
                    if "output" in content:
                        return truncate_value(content["output"], 150)
                    if "content" in content:
                        return truncate_value(content["content"], 150)
                    # Show first few keys
                    return truncate_value(content, 150)
                
                return truncate_value(content, 150)
            except Exception:
                s = str(content)
                return s[:150] + "..." if len(s) > 150 else s
        
        def process_step_message(msg: dict):
            """Print progress for each agent message/tool call."""
            step_count[0] += 1
            if not verbose:
                return
            
            agent_name = msg.get("agent_name", "Agent") or "Agent"
            role = msg.get("role", "") or ""
            
            # Compact progress indicator
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
                # Check for tool_calls in assistant message
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
                    # Regular assistant message with content
                    content = msg.get("content") or ""
                    if content:
                        preview = content[:80].replace("\n", " ") + "..." if len(content) > 80 else content.replace("\n", " ")
                        print(f"    [{step_count[0]:02d}] 💬 {agent_name}: {preview}")
                    else:
                        print(f"    [{step_count[0]:02d}] 💬 {agent_name}: (no content)")
                        # Debug: print other fields if content is missing
                        keys_to_show = [k for k in msg.keys() if k not in ["role", "content", "agent_name", "type"]]
                        if keys_to_show:
                            print(f"      Debugging keys: {keys_to_show}")
                            for k in keys_to_show:
                                val = msg.get(k)
                                val_str = truncate_value(val, max_len=100)
                                print(f"      - {k}: {val_str}")
            else:
                print(f"    [{step_count[0]:02d}] 📝 {agent_name} ({role})")
        
        # Run the team with progress callback
        result = await team.run(
            msg=prompt,
            memory=memory,
            process_step_message=process_step_message,
        )
        
        # Extract answers from result content
        content = result.content if hasattr(result, "content") else str(result)
        messages = memory.get_messages()
        
        # Prefer tool-based extraction, fallback to text extraction
        answers = self._extract_answers_from_tool_calls(messages, capsule_info["questions"])
        
        # If no answers found, give the agent ONE chance to fix it
        if not answers:
            print("    ⚠️  No answers submitted. Triggering retry turn...")
            retry_msg = "You have verified the task but have not submitted the final answer via the `submit_answer` tool. Please submit your conclusion immediately."
            
            # Run one more turn with the same memory
            result = await team.run(
                msg=retry_msg,
                memory=memory,
                process_step_message=process_step_message,
            )
            
            # Refresh messages and try extracting again
            messages = memory.get_messages()
            answers = self._extract_answers_from_tool_calls(messages, capsule_info["questions"])

        if not answers:
            # content can be None if agent returns no content; ensure string for regex
            safe_content = content or ""
            answers = self._extract_answers(safe_content, capsule_info["questions"])
        
        # Get trajectory info
        messages = memory.get_messages()
        
        return {
            "answers": answers,
            "trajectory_length": len(messages),
            "raw_result": str(result)[:2000],  # Truncate for storage
            "messages": messages,  # Full trajectory for analysis
        }
    
    async def cleanup_notebook_sessions(self):
        """Cleanup all notebook kernel sessions to free memory.
        
        Should be called after each capsule completes to prevent memory accumulation.
        This shuts down all Jupyter kernels and clears notebook contexts.
        """
        if self._endpoint is None:
            return 0
        
        try:
            # Find notebook toolset by iterating through services (key is local_{name}_{uuid})
            notebook_toolset = None
            for service_id, service_info in self._endpoint.toolset_manager.services.items():
                if service_info.get("name") == "integrated_notebook":
                    notebook_toolset = service_info.get("instance")
                    break
            
            if notebook_toolset and notebook_toolset.kernel_toolset:
                kernel_toolset = notebook_toolset.kernel_toolset
                
                # Count sessions before cleanup
                session_count = len(kernel_toolset.sessions)
                
                if session_count == 0:
                    return 0
                
                # Shutdown all kernel sessions
                for session_id in list(kernel_toolset.sessions.keys()):
                    try:
                        await kernel_toolset.shutdown_session(session_id)
                    except Exception as e:
                        print(f"    ⚠️ Failed to shutdown session {session_id[:8]}: {e}")
                
                # Clear notebook contexts
                notebook_toolset.notebook_contexts.clear()
                await notebook_toolset._save_contexts()
                
                return session_count
            return 0
        except Exception as e:
            print(f"    ⚠️ Failed to cleanup notebook sessions: {e}")
            return 0
    
    async def cleanup(self):
        """Cleanup endpoint resources."""
        if self._endpoint is not None:
            try:
                await self._endpoint.cleanup()
            except Exception as e:
                print(f"Warning: Failed to cleanup endpoint: {e}")
        self._endpoint = None
        # No need to cleanup self._team as it's local now
    
    def _extract_answers_from_tool_calls(self, messages: list, questions: list) -> dict:
        """Extract answers from the LAST submit_answer tool call.
        
        Args:
            messages: List of conversation messages
            questions: List of question dicts with 'id' field
            
        Returns:
            Dict mapping question IDs to answers
        """
        answers = {}
        question_ids = {q["id"] for q in questions}
        
        last_valid_submitted_answers = None
        
        for msg in messages:
            # Handle dict format
            if isinstance(msg, dict):
                tool_calls = msg.get("tool_calls") or []
            elif hasattr(msg, "tool_calls"):
                tool_calls = msg.tool_calls or []
            elif hasattr(msg, "model_dump"):
                tool_calls = msg.model_dump().get("tool_calls") or []
            else:
                continue
            
            for tc in tool_calls:
                # Extract function info
                if isinstance(tc, dict):
                    func = tc.get("function", tc)
                    func_name = func.get("name", "")
                    func_args = func.get("arguments", {})
                elif hasattr(tc, "function"):
                    func_name = tc.function.name if hasattr(tc.function, "name") else ""
                    func_args = tc.function.arguments if hasattr(tc.function, "arguments") else {}
                else:
                    continue
                
                if func_name != "submit_answer":
                    continue
                
                # Parse arguments
                if isinstance(func_args, str):
                    try:
                        func_args = json.loads(func_args)
                    except json.JSONDecodeError:
                        continue
                
                submitted = func_args.get("answers", {})
                if isinstance(submitted, dict) and submitted:
                    # Found a valid submission, update our candidate
                    # This overwrites any previous candidate, so we end up with the last one
                    last_valid_submitted_answers = submitted
        
        # If we found at least one valid submission, process the last one
        if last_valid_submitted_answers:
            for qid, ans in last_valid_submitted_answers.items():
                # Sanitize key: remove surrounding quotes and escaped quotes
                # e.g. "\"q1\"" -> "q1", "'q1'" -> "q1"
                clean_qid = qid.strip("\"'").replace('\\"', '').replace("\\'", "")
                
                for full_id in question_ids:
                    # Match full ID or short ID (e.g., "q1" matches "bix-1-q1")
                    if clean_qid == full_id or full_id.endswith(f"-{clean_qid}"):
                        answers[full_id] = str(ans)
                        break
        
        return answers
    
    def _extract_answers(self, result: str, questions: list) -> dict:
        """
        Extract answers from agent result.
        
        Tries multiple extraction strategies:
        1. JSON block extraction
        2. Key-value pattern matching
        3. Final answer section parsing
        """
        answers = {}
        question_ids = [q["id"] for q in questions]
        
        # Strategy 1: Try to find JSON block
        json_answers = self._extract_json_answers(result)
        if json_answers:
            for qid in question_ids:
                if qid in json_answers:
                    answers[qid] = json_answers[qid]
                # Also try short form (e.g., "q1" instead of "bix-1-q1")
                short_qid = qid.split("-")[-1] if "-" in qid else qid
                if short_qid in json_answers:
                    answers[qid] = json_answers[short_qid]
        
        # Strategy 2: Pattern matching for each question
        for q in questions:
            qid = q["id"]
            if qid not in answers:
                answer = self._extract_answer_for_question(result, qid)
                if answer:
                    answers[qid] = answer
        
        return answers
    
    def _extract_json_answers(self, text: str) -> Optional[dict]:
        """Extract answers from JSON block in text."""
        # Try to find JSON blocks
        json_patterns = [
            r'```json\s*\n?(.*?)\n?```',  # ```json ... ```
            r'```\s*\n?(\{.*?\})\n?```',    # ``` {...} ```
            r'\{[^{}]*"[^"]+"\s*:\s*"[^"]*"[^{}]*\}',  # Simple JSON object
        ]
        
        for pattern in json_patterns:
            matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
            for match in matches:
                try:
                    # Clean up the match
                    json_str = match.strip()
                    if not json_str.startswith("{"):
                        continue
                    data = json.loads(json_str)
                    if isinstance(data, dict):
                        return data
                except json.JSONDecodeError:
                    continue
        
        return None
    
    def _extract_answer_for_question(self, text: str, question_id: str) -> Optional[str]:
        """Extract answer for a specific question ID from text."""
        # Patterns to try
        patterns = [
            # "bix-1-q1: 0.0002" or "bix-1-q1 = 0.0002"
            rf'{re.escape(question_id)}\s*[:=]\s*["\']?([^"\'\n,}}]+)["\']?',
            # "q1: 0.0002" (short form)
            rf'{re.escape(question_id.split("-")[-1])}\s*[:=]\s*["\']?([^"\'\n,}}]+)["\']?',
            # "Answer for bix-1-q1: 0.0002"
            rf'[Aa]nswer\s+(?:for\s+)?{re.escape(question_id)}\s*[:=]\s*["\']?([^"\'\n,}}]+)["\']?',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        return None
    
    def generate_trajectory_record(
        self,
        capsule_info: dict,
        question: dict,
        agent_answer: str,
        run_name: str = "baseline",
        notebook_json: dict = None,
        team: str = "default",
        injection_mode: str = "auto",
    ) -> dict:
        """
        Generate a trajectory record in official BixBench format.
        
        Args:
            capsule_info: Capsule metadata
            question: Question dict with id, question, ideal, eval_mode
            agent_answer: The agent's extracted answer
            run_name: Run identifier (baseline, with_learning, etc.)
            notebook_json: Optional notebook JSON if available
            team: Team used for the run
            injection_mode: Injection mode used for the run
            
        Returns:
            Dict in official BixBench trajectory format
        """
        return {
            "problem_id": question["id"],
            "question": question["question"],
            "agent_answer": agent_answer,
            "ideal_answer": question.get("ideal", ""),
            "eval_mode": question.get("eval_mode", "str_verifier"),
            "capsule_uuid": capsule_info.get("capsule_uuid", ""),
            "hypothesis": capsule_info.get("hypothesis", ""),
            "run_name": run_name,
            "nb": notebook_json,
            "team": team,
            "injection_mode": injection_mode,
        }
