
import argparse
import asyncio
import json
import logging
import time
from pathlib import Path
from datetime import datetime
import shutil

from .adapter import SingleCellBenchmarkAdapter

# Configure logging (BixBench style)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("benchmark_runner")


async def run_benchmark(
    round_name: str,
    limit: int = None,
    team: str = "default",
    model: str = "gemini/gemini-3-flash-preview",
    enable_learning: bool = False,
    injection_mode: str = "auto",
    skillbook_path: str = None,
):
    base_dir = Path(__file__).parent.resolve()
    jsonl_path = base_dir / "benchmark.jsonl"
    
    if not jsonl_path.exists():
        logger.error(f"Benchmark file not found: {jsonl_path}")
        return

    # Directories
    results_dir = base_dir / "results" / round_name
    workspaces_dir = base_dir / "workspaces" / round_name
    
    results_dir.mkdir(parents=True, exist_ok=True)
    workspaces_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info(f"Starting Benchmark Round: {round_name}")
    logger.info(f"Results Dir: {results_dir}")
    logger.info(f"Workspaces Dir: {workspaces_dir}")
    if model:
        logger.info(f"Model Override: {model}")

    # Load tasks
    tasks = []
    with open(jsonl_path, 'r') as f:
        for line in f:
            if line.strip():
                tasks.append(json.loads(line))
                
    if limit:
        tasks = tasks[:limit]
        
    logger.info(f"Found {len(tasks)} tasks.")
    
    # Check completed
    completed_ids = set()
    for f in results_dir.glob("*.json"):
        if f.name == "report.json": continue
        try:
            with open(f, 'r') as rf:
                data = json.load(rf)
                if data.get('status') == 'completed':
                    completed_ids.add(f.stem)
        except Exception:
            # If file is corrupt or unreadable, treat as not completed
            pass
        
    logger.info(f"Already completed (success): {len(completed_ids)}")

    # Initialize Adapter
    # Use PWD as the shared endpoint workspace to distinguish from benchmark root logic.
    adapter = SingleCellBenchmarkAdapter(
        model_name=model,
        team=team,
        workspace_path=str(Path.cwd()),
    )

    results = []
    
    try:
        for task in tasks:
            task_id = task['id']
            
            if task_id in completed_ids:
                # Load existing result for report
                with open(results_dir / f"{task_id}.json") as f:
                    results.append(json.load(f))
                continue
                
            task_type = task.get('type', 'General')
            logger.info(f"▶ [{task_id}] Starting task: {task_type}")
            
            # Workspace setup
            task_workspace = workspaces_dir / task_id
            task_workspace.mkdir(parents=True, exist_ok=True)
            
            # Prepare Prompt
            # Structure follows BixBench test_prompt.txt for consistency
            
            datasets_text = ""
            if 'dataset' in task:
                for ds in task['dataset']:
                    abs_path = (base_dir / ds['path']).resolve()
                    datasets_text += f"\n- {ds['desc']}: {abs_path}"
            
            prompt = f"""
You are an expert bioinformatician. Analyze the data and answer the research questions.

## IMPORTANT: Execution Rules

1. **NON-INTERACTIVE**: Do NOT ask the user any questions or request approval/feedback during the analysis. Work autonomously.
2. **NO USER INTERACTION**: Complete the entire analysis independently without requesting feedback.
3. **FINAL ANSWER ONLY**: Only communicate with the user when providing your final answer using the `submit_answer` tool.
4. **LOOP TERMINATION**: After submitting your answer via `submit_answer`, you MUST end the session. Simply STOP generating any further output (no text, no tool calls). The tool will automatically terminate the session.

## Environment

- You have access to a standard single-cell analysis environment (Python/Scanpy/Scanorama or R/Seurat as appropriate).
- Prefer using Jupyter Notebooks for analysis.

## Paths (ABSOLUTE - USE THESE EXACT PATHS)

**Data files location (READ ONLY):**
{datasets_text}

**Your workspace (ALL output files go here):**
{task_workspace}

CRITICAL: 
- Read data from the data paths above
- Write ALL output files to the workspace path
- Do NOT create files anywhere else

## Task
{task['task']}

## Instructions
1. List files in the data directories to understand the structure.
2. Create a Jupyter Notebook in your workspace for analysis.
3. Perform the necessary analysis steps (QC, integration, clustering, etc.) via code.
4. Answer the task question precisely.
5. After completing analysis, use the `submit_answer` tool to submit your final answer:
   ```python
   submit_answer(answer="your answer here")
   ```

Begin now. Work autonomously without asking questions.
"""

            start_time = time.time()
            try:
                # Run Task via Adapter
                # We pass keys to help adapter if it needs them, but prompt does the heavy lifting
                adapter_result = await adapter.run_task(
                    prompt=prompt,
                    task_id=task_id,
                    workspace_subdir=task_id
                )
                
                duration = time.time() - start_time
                final_answer = adapter_result["answer"]
                messages = adapter_result.get("messages", [])
                
                # Save memory logs
                memory_file = results_dir / f"{task_id}_memory.json"
                try:
                    serializable_messages = []
                    for msg in messages:
                        if hasattr(msg, "model_dump"):
                            serializable_messages.append(msg.model_dump())
                        elif hasattr(msg, "to_dict"):
                            serializable_messages.append(msg.to_dict())
                        elif isinstance(msg, dict):
                            serializable_messages.append(msg)
                        else:
                            serializable_messages.append(str(msg))
                            
                    with open(memory_file, "w") as f:
                        json.dump({
                            "task_id": task_id,
                            "messages": serializable_messages
                        }, f, indent=2, ensure_ascii=False, default=str)
                except Exception as e:
                    logger.warning(f"Failed to save memory logs: {e}")

                output = {
                    "id": task_id,
                    "task": task['task'],
                    "type": task_type,
                    "agent_answer": final_answer,
                    "ground_truth": task.get('ground_truth'),
                    "tolerance": task.get('tol'),
                    "duration": duration,
                    "status": "completed" if final_answer else "failed_no_answer",
                    "messages_count": len(messages),
                    "memory_file": str(memory_file),
                    "enable_learning": enable_learning,
                    "injection_mode": injection_mode,
                }
                
                # Add skillbook_path if provided
                if skillbook_path:
                    output["skillbook_path"] = skillbook_path
                
            except Exception as e:
                logger.error(f"❌ [{task_id}] Failed: {e}")
                output = {
                     "id": task_id,
                     "task": task['task'],
                     "status": "error",
                     "error": str(e),
                     "ground_truth": task.get('ground_truth')
                }
            
            # Save result
            out_file = results_dir / f"{task_id}.json"
            with open(out_file, 'w') as f:
                json.dump(output, f, indent=2, ensure_ascii=False)
                
            results.append(output)
            logger.info(f"✓ [{task_id}] Finished. Status: {output['status']}")
            
            # Update Report (Real-time)
            generate_report(results, results_dir, round_name, skillbook_path)
            
    finally:
        await adapter.cleanup()

    # Final Report verification
    generate_report(results, results_dir, round_name, skillbook_path)

def generate_report(results, results_dir, round_name, skillbook_path=None):
    timestamp = datetime.now().isoformat()
    # Summary Report
    completed = [r for r in results if r.get('status') == 'completed']
    errors = [r for r in results if r.get('status') == 'error']
    
    # Extract skillbook_path from results if not provided (for continue mode)
    if skillbook_path is None and results:
        for r in results:
            if 'skillbook_path' in r:
                skillbook_path = r['skillbook_path']
                break
    
    report = {
        "round": round_name,
        "timestamp": timestamp,
        "total_tasks": len(results),
        "completed_count": len(completed),
        "error_count": len(errors),
        "enable_learning": results[0].get("enable_learning", False) if results else False,
        "injection_mode": results[0].get("injection_mode", "auto") if results else "auto",
        "results": results
    }
    
    # Add skillbook_path if provided or extracted
    if skillbook_path:
        report["skillbook_path"] = skillbook_path
    
    with open(results_dir / "report.json", 'w') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        
    # Markdown Report
    md = f"# Benchmark Report - Round: {round_name}\n\n"
    md += f"- **Date**: {timestamp}\n"
    md += f"- **Total**: {len(results)}\n"
    md += f"- **Completed**: {len(completed)}\n"
    md += f"- **Errors**: {len(errors)}\n"
    
    # Add skillbook info if available
    if skillbook_path:
        md += f"- **Skillbook**: {skillbook_path}\n"
    
    md += "\n"
    
    md += "## Details\n\n"
    md += "| ID | Type | Status | Answer | Ground Truth | Duration (s) |\n"
    md += "|----|------|--------|--------|--------------|--------------|\n"
    
    for r in results:
        aid = r.get('id', '')
        atype = r.get('type', 'Unknown')
        status = r.get('status', 'Unknown')
        ans = str(r.get('agent_answer', 'N/A')).replace('\n', ' ')[:50]
        gt = str(r.get('ground_truth', 'N/A'))
        dur = f"{r.get('duration', 0):.2f}"
        
        md += f"| {aid} | {atype} | {status} | {ans} | {gt} | {dur} |\n"
        
    with open(results_dir / "report.md", 'w') as f:
        f.write(md)
        
    logger.info(f"Report generated at {results_dir / 'report.json'}")

async def main():
    parser = argparse.ArgumentParser(description="Single Cell Benchmark Runner")
    parser.add_argument("--round", default=None, help="Name of the benchmark round. If not provided, a timestamped name is generated.")
    parser.add_argument("--limit", type=int, help="Limit number of tasks to run")
    parser.add_argument("--team", default="default", help="Team template to use")
    parser.add_argument("--model", default="gemini/gemini-3-flash-preview", help="Model override")
    parser.add_argument("--enable-learning", action="store_true", help="Enable Learning module")
    parser.add_argument("--injection-mode", default="auto",
                        choices=["static", "dynamic", "auto"],
                        help="Injection mode: static (all skills upfront), dynamic (context-based), auto (config defaults)")
    parser.add_argument("--skillbook-path", default=None, help="Path to skillbook.json for skill injection")
    
    args = parser.parse_args()
    
    if args.round:
        round_name = args.round
    else:
        round_name = f"round_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
    await run_benchmark(
        round_name, 
        args.limit, 
        args.team, 
        args.model,
        args.enable_learning,
        args.injection_mode,
        args.skillbook_path,
    )

if __name__ == "__main__":
    asyncio.run(main())
