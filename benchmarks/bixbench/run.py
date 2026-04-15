"""
BixBench runner script for Pantheon.

Runs BixBench evaluation, grades answers, and outputs official-compatible format.

Usage:
    python -m benchmarks.bixbench.run --capsule-limit 3 --enable-learning
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from datetime import datetime

from pantheon.utils.log import setup_file_logging, logger

from .adapter import PantheonBixBenchAdapter
from .grader import BixBenchGrader, grade_capsule_answers


def calculate_total_cost(messages: list) -> float:
    """Calculate total cost from message metadata.
    
    Sums up the 'current_cost' field from _metadata in assistant messages.
    """
    total_cost = 0.0
    for msg in messages:
        # Handle both dict and object formats
        if isinstance(msg, dict):
            metadata = msg.get("_metadata", {})
        elif hasattr(msg, "_metadata"):
            metadata = msg._metadata if isinstance(msg._metadata, dict) else {}
        else:
            continue
        
        if metadata and "current_cost" in metadata:
            total_cost += metadata.get("current_cost", 0.0)
    
    return total_cost


def setup_logging(log_dir: str = ".pantheon/logs/benchmark", log_level: str = "INFO"):
    """Setup file logging using Pantheon's logging infrastructure.
    
    This uses pantheon.utils.log.setup_file_logging() to add a file handler
    while preserving existing console/other handlers. This ensures all Pantheon
    internal logs are captured in the benchmark log file.
    """
    log_path = Path(log_dir)
    log_file = setup_file_logging(
        log_dir=log_path,
        level=log_level,
        session_name="benchmark",
    )
    
    logger.info(f"Benchmark logging started: {log_file}")
    return log_file



def attempt_recover_result(
    output_dir: Path,
    capsule_id: str,
    capsule_info: dict,
) -> dict | None:
    """Attempt to reconstruct a result object from disk files."""
    try:
        # 1. Recover Cost & Messages from Memory File
        memory_file = output_dir / f"{capsule_id}_memory.json"
        
        # Initialize defaults
        cost = 0.0
        message_count = 0
        transcript_len = 0
        
        if memory_file.exists():
            with open(memory_file) as f:
                mem_data = json.load(f)
                message_count = mem_data.get("message_count", 0)
                # Recalculate cost from messages if possible
                messages = mem_data.get("messages", [])
                cost = calculate_total_cost(messages)
                transcript_len = len(messages)
        
        # 2. Recover Answers & Grading from Question Files
        answers = {}
        correct_count = 0
        total_questions = len(capsule_info["questions"])
        question_details = {}
        
        found_q_files = False
        for q in capsule_info["questions"]:
            qid = q["id"]
            traj_file = output_dir / f"{qid}.json"
            
            if traj_file.exists():
                found_q_files = True
                with open(traj_file) as f:
                    traj = json.load(f)
                    
                # Extract answer
                ans = traj.get("agent_answer") or traj.get("answer", "")
                answers[qid] = ans
                
                # Extract grading
                is_correct = traj.get("correct", False)
                if is_correct:
                    correct_count += 1
                
                # Include predicted and target for supervised learning
                question_details[qid] = {
                    "predicted": traj.get("predicted", ans),
                    "target": traj.get("target", ""),
                    "correct": is_correct,
                    "score": 1.0 if is_correct else 0.0,
                    "grade_type": traj.get("grade_type", "correct" if is_correct else "incorrect"),
                }
            else:
                # Missing question file means incomplete run
                answers[qid] = ""
                question_details[qid] = {
                    "predicted": "",
                    "target": "",
                    "correct": False, 
                    "score": 0.0,
                }
        
        if not found_q_files and not memory_file.exists():
            return None
            
            # 3. Construct Result Object
        recovered_team = getattr(question_details.get(list(question_details.keys())[0], {}), "team", None)
        # If we couldn't get it from memory (not implemented there yet) or question files, default to None?
        # Actually, let's look mainly at the question file as that's where we save the final record.
        
        # Try to find team in the first valid question file we parsed
        team = "default"
        if found_q_files:
             # Iterate through available trajectory files to find team
             for qid in capsule_info.get("questions", []):
                 q_id = qid["id"]
                 t_file = output_dir / f"{q_id}.json"
                 if t_file.exists():
                     with open(t_file) as f:
                        data = json.load(f)
                        if "team" in data:
                            team = data["team"]
                            break
        
        return {
            "capsule_id": capsule_id,
            "status": "completed",
            "answers": answers,
            "grading": {
                "correct": correct_count,
                "total": total_questions,
                "accuracy": correct_count / total_questions if total_questions else 0,
                "questions": question_details,
            },
            "trajectory_length": transcript_len,
            "cost": cost,
            "duration": 0.0, # Cannot recover duration easily, set to 0
            "message_count": message_count,
            "memory_file": str(memory_file) if memory_file.exists() else None,
            "team": team,
        }
        
    except Exception as e:
        print(f"  ⚠️  Recovery failed for {capsule_id}: {e}")
        return None


async def run_benchmark(
    capsule_limit: int = 3,
    enable_learning: bool = False,
    output_dir: str = "benchmarks/bixbench/results",
    log_level: str = "INFO",
    continue_from: str = None,
    skillbook_path: str = None,
    learning_config: dict = None,
    team: str = "default",
    injection_mode: str = "auto",
    model_name: str = "gemini/gemini-3-flash-preview",
    capsules_filter: str = None,
):
    """Run BixBench benchmark with Pantheon agent.
    
    Args:
        capsule_limit: Number of capsules to evaluate
        enable_learning: Whether to enable Learning module
        output_dir: Directory to save results
        log_level: Log level for benchmark (INFO, DEBUG, WARNING)
        continue_from: Path to previous run directory to continue from (skips completed capsules)
        skillbook_path: Path to skillbook.json for injection (overrides settings)
        learning_config: Full learning config dict (overrides all settings)
        team: Team template ID to use (default: default)
        injection_mode: Injection mode (static, dynamic, auto)
        model_name: Model name to use (e.g., gemini/gemini-3-flash-preview)
        capsules_filter: Comma-separated list of capsule IDs to run (e.g. "bix-13,bix-14")
    """
    # Setup logging to file
    log_file = setup_logging(log_level=log_level)
    print(f"📝 Logs: {log_file}")
    
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Load prepared capsules
    # Use absolute path to ensure we can find files even if CWD changes
    capsules_dir = Path("benchmarks/bixbench/test_capsules").resolve()
    index_file = capsules_dir / "index.json"
    
    if not index_file.exists():
        print("❌ No capsules prepared. Run prepare_capsules.py first.")
        return
    
    with open(index_file) as f:
        index = json.load(f)
    
    # Load all capsules (don't apply limit yet in continue mode)
    all_capsules = index["capsules"]
    all_capsules = index["capsules"]
    
    # Apply capsule filter if specified
    if capsules_filter:
        target_ids = [id.strip() for id in capsules_filter.split(",")]
        all_capsules = [c for c in all_capsules if c["short_id"] in target_ids]
        if not all_capsules:
            print(f"❌ No capsules found matching filter: {capsules_filter}")
            return
            
    run_name = "with_learning" if enable_learning else "baseline"
    
    # Handle continue mode
    completed_capsules = set()
    run_output_dir = None
    previous_results = []
    
    if continue_from:
        continue_path = Path(continue_from)
        
        # If path doesn't exist, try relative to results directory
        if not continue_path.exists():
            continue_path = output_path / continue_from
        
        if continue_path.exists():
            run_output_dir = continue_path
            print(f"📂 Continuing from: {continue_path}")
            
            # Find completed capsules by checking trajectory files
            for traj_file in continue_path.glob("*.json"):
                if traj_file.name.startswith("bix-") and "-q" in traj_file.name:
                    # Extract capsule short_id (e.g., "bix-1-q1.json" -> "bix-1")
                    parts = traj_file.stem.rsplit("-q", 1)
                    if parts:
                        completed_capsules.add(parts[0])
            
            # Load existing summary if present
            summary_file = continue_path / "summary.json"
            if summary_file.exists():
                with open(summary_file) as f:
                    prev_summary = json.load(f)
                    previous_results = prev_summary.get("results", [])
            
            print(f"✓ Found {len(completed_capsules)} completed capsules")
        else:
            print(f"⚠️ Continue path not found: {continue_from}, starting fresh")
    
    # In continue mode, prioritize untried capsules over retrying failed ones
    if continue_from:
        # Find capsules that were attempted but failed
        attempted_capsules = {r.get("capsule_id") for r in previous_results}
        failed_capsules = {r.get("capsule_id") for r in previous_results if r.get("status") == "error"}
        
        # Remove failed capsules from completed_capsules so they are re-run
        completed_capsules -= failed_capsules
        
        # Separate capsules into untried and failed
        untried = [c for c in all_capsules if c["short_id"] not in attempted_capsules]
        to_retry = [c for c in all_capsules if c["short_id"] in failed_capsules]
        
        # Reorder: untried first, then retries
        all_capsules = untried + to_retry
        
        if untried:
            print(f"📋 Prioritizing {len(untried)} untried capsules")
        if to_retry:
            print(f"🔄 Will retry {len(to_retry)} failed capsules after untried ones")
    
    # NOW apply capsule_limit after continue mode filtering
    capsules = all_capsules[:capsule_limit]
    
    print(f"📋 Running benchmark on {len(capsules)} capsules")
    print(f"📝 Run name: {run_name}")
    print(f"👥 Team: {team}")

    # Initialize adapter and grader
    adapter = PantheonBixBenchAdapter(
        model_name=model_name,
        team=team,
    )
    grader = BixBenchGrader()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Create run-specific output directory (unless continuing)
    if run_output_dir is None:
        run_output_dir = output_path / f"{run_name}_{timestamp}"
        run_output_dir.mkdir(parents=True, exist_ok=True)
    
    # Create workspace root directory
    # In continue mode, derive workspace from results directory name
    if continue_from and run_output_dir:
        # Extract run name from directory (e.g., "baseline_20260108_130351")
        run_dir_name = run_output_dir.name
        workspace_root = Path("benchmarks/bixbench/workspaces") / run_dir_name
        # Reuse existing workspace if it exists
        if not workspace_root.exists():
            workspace_root.mkdir(parents=True, exist_ok=True)
            print(f"📁 Creating workspace for continue: {workspace_root}")
        else:
            print(f"📁 Reusing workspace: {workspace_root}")
    else:
        # New run: create timestamped workspace
        workspace_root = Path("benchmarks/bixbench/workspaces") / f"{run_name}_{timestamp}"
        workspace_root.mkdir(parents=True, exist_ok=True)
        print(f"📁 Created new workspace: {workspace_root}")
    
    all_results = list(previous_results)  # Start with previous results
    total_correct = sum(r.get("grading", {}).get("correct", 0) for r in previous_results)
    total_questions = sum(r.get("grading", {}).get("total", 0) for r in previous_results)
    total_cost = sum(r.get("cost", 0.0) for r in previous_results)
    total_duration = sum(r.get("duration", 0.0) for r in previous_results)
    total_messages = sum(r.get("message_count", 0) for r in previous_results)


    for capsule_info in capsules:
        short_id = capsule_info["short_id"]
        capsule_dir = capsules_dir / short_id
        
        # Load capsule info first to support recovery
        print(f"DEBUG: Checking {capsule_dir} (cwd={Path.cwd()})")
        if not (capsule_dir / "info.json").exists():
            print(f"DEBUG: FATAL - File not found: {(capsule_dir / 'info.json').absolute()}")
            
        with open(capsule_dir / "info.json") as f:
            info = json.load(f)

        # Check for orphan files (completed on disk but missing from results)
        if short_id in completed_capsules and short_id not in [r["capsule_id"] for r in all_results]:
             print(f"⚠️  Found orphan files for {short_id}. Attempting recovery from disk...")
             recovered_result = attempt_recover_result(run_output_dir, short_id, info)
             
             if recovered_result:
                 all_results.append(recovered_result)
                 
                 # Add to totals
                 total_correct += recovered_result["grading"]["correct"]
                 total_questions += recovered_result["grading"]["total"]
                 total_cost += recovered_result["cost"]
                 total_duration += recovered_result["duration"]
                 total_messages += recovered_result["message_count"]
                 
                 print(f"  ✅ Recovered result: {short_id} (Acc: {recovered_result['grading']['correct']}/{recovered_result['grading']['total']})")
                 continue
             else:
                 print(f"  ❌ Recovery failed. Will re-run capsule.")
                 
        elif short_id in completed_capsules:
            print(f"\n{'='*60}")
            print(f"⏭️  Skipping completed capsule: {short_id}")
            continue
        
        print(f"\n{'='*60}")
        print(f"▶ Running capsule: {short_id}")
        capsule_start_time = time.time()
        
        # Load capsule data
        with open(capsule_dir / "info.json") as f:
            info = json.load(f)
        
        with open(capsule_dir / "test_prompt.txt") as f:
            prompt_template = f.read()
        
        # Create capsule-specific workspace
        capsule_workspace = workspace_root / short_id
        capsule_workspace.mkdir(parents=True, exist_ok=True)
        
        # Substitute workspace path placeholder in prompt
        # Use absolute path to ensure agent can find it regardless of CWD changes
        abs_workspace_path = str(capsule_workspace.resolve())
        prompt = prompt_template.replace("{WORKSPACE_PATH}", abs_workspace_path)
        
        # Run adapter
        try:
            result = await adapter.run_task(
                prompt=prompt,
                capsule_info=info,
                # workspace_path argument removed to keep endpoint static at CWD
            )
            
            # Grade answers
            grade_result = await grade_capsule_answers(
                answers=result["answers"],
                questions=info["questions"],
                grader=grader,
            )
            
            print(f"  📊 Accuracy: {grade_result['correct']}/{grade_result['total']} ({grade_result['accuracy']:.1%})")
            
            # Generate trajectory records for each question
            trajectories = []
            for q in info["questions"]:
                qid = q["id"]
                agent_answer = result["answers"].get(qid, "")
                
                trajectory = adapter.generate_trajectory_record(
                    capsule_info=info,
                    question=q,
                    agent_answer=agent_answer,
                    run_name=run_name,
                    team=team,
                    injection_mode=injection_mode,
                )
                
                # Add skillbook_path if specified
                if skillbook_path:
                    trajectory["skillbook_path"] = skillbook_path
                
                # Add grading result
                if qid in grade_result["questions"]:
                    trajectory.update(grade_result["questions"][qid])
                
                trajectories.append(trajectory)
                
                # Save individual trajectory (official format)
                traj_file = run_output_dir / f"{qid}.json"
                with open(traj_file, "w") as f:
                    json.dump(trajectory, f, indent=2, ensure_ascii=False)
            
            # Save memory/conversation file for debugging
            memory_file = run_output_dir / f"{short_id}_memory.json"
            try:
                messages = result.get("messages", [])
                # Convert messages to serializable format
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
                        "capsule_id": short_id,
                        "run_name": run_name,
                        "message_count": len(serializable_messages),
                        "messages": serializable_messages,
                    }, f, indent=2, ensure_ascii=False, default=str)
                print(f"  💾 Memory saved: {memory_file.name}")
            except Exception as mem_err:
                print(f"  ⚠️  Failed to save memory: {mem_err}")
            
            # Calculate cost from messages
            messages = result.get("messages", [])
            capsule_cost = calculate_total_cost(messages)
            capsule_duration = time.time() - capsule_start_time
            capsule_message_count = len(messages)
            
            print(f"  💰 Cost: ${capsule_cost:.6f}")
            print(f"  ⏱️  Duration: {capsule_duration:.1f}s")
            print(f"  💬 Messages: {capsule_message_count}")
            
            capsule_result = {
                "capsule_id": short_id,
                "status": "completed",
                "answers": result["answers"],
                "grading": grade_result,
                "trajectory_length": result["trajectory_length"],
                "cost": capsule_cost,
                "duration": capsule_duration,
                "message_count": capsule_message_count,
                "memory_file": str(memory_file) if memory_file.exists() else None,
                "team": team,
                "injection_mode": injection_mode,
            }
            
            total_correct += grade_result["correct"]
            total_questions += grade_result["total"]
            total_cost += capsule_cost
            total_duration += capsule_duration
            total_messages += capsule_message_count
            
        except Exception as e:
            print(f"  ❌ Error: {e}")
            capsule_result = {
                "capsule_id": short_id,
                "status": "error",
                "error": str(e),
            }
        
        
        # In continue mode, remove old error record if this capsule is being retried
        if continue_from:
            all_results = [r for r in all_results if r.get("capsule_id") != short_id]
        
        all_results.append(capsule_result)
        print(f"  ✓ Status: {capsule_result['status']}")
        
        # Cleanup notebook sessions to free memory
        cleaned_sessions = await adapter.cleanup_notebook_sessions()
        if cleaned_sessions > 0:
            print(f"  🧹 Cleaned up {cleaned_sessions} notebook session(s)")
            
    # Deduplicate results (keep latest)
    # This cleans up any ghosts from previous runs (e.g. bix-61 duplicates)
    unique_results = {r['capsule_id']: r for r in all_results}
    all_results = list(unique_results.values())
    # Sort by ID for tidiness
    try:
        all_results.sort(key=lambda x: int(x['capsule_id'].split('-')[1]) if x['capsule_id'].startswith("bix-") and '-' in x['capsule_id'] else x['capsule_id'])
    except:
        all_results.sort(key=lambda x: x['capsule_id'])

    # Save summary results
    summary = {
        "timestamp": timestamp,
        "updated_at": datetime.now().strftime("%Y%m%d_%H%M%S"),  # Track update time
        "run_name": run_name,
        "team": team,
        "enable_learning": enable_learning,
        "injection_mode": injection_mode,
        "capsule_count": len(all_results),
        "total_questions": total_questions,
        "total_correct": total_correct,
        "overall_accuracy": total_correct / total_questions if total_questions else 0,
        "total_cost": total_cost,
        "total_duration": total_duration,
        "total_messages": total_messages,
        "avg_cost_per_capsule": total_cost / len(all_results) if all_results else 0,
        "avg_duration_per_capsule": total_duration / len(all_results) if all_results else 0,
        "avg_messages_per_capsule": total_messages / len(all_results) if all_results else 0,
        "results": all_results,
    }
    
    # Add skillbook_path if specified
    if skillbook_path:
        summary["skillbook_path"] = skillbook_path
    
    summary_file = run_output_dir / "summary.json"
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    # Also save/update to main results dir
    if continue_from and run_output_dir:
        # In continue mode, update the EXISTING file corresponding to this run dir
        # e.g., results/baseline_20260108.json
        run_dirname = run_output_dir.name  # like baseline_20260108_130351
        results_file = output_path / f"results_{run_dirname}.json"
        # Always use this filename to maintain consistency, avoiding new timestamped files
    else:
        # New run: create new file
        results_file = output_path / f"results_{run_name}_{timestamp}.json"

    with open(results_file, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    
    # Cleanup adapter resources
    await adapter.cleanup()
    
    print(f"\n{'='*60}")
    print(f"✅ Benchmark complete: {len(all_results)} capsules")
    print(f"📊 Overall accuracy: {total_correct}/{total_questions} ({summary['overall_accuracy']:.1%})")
    print(f"💰 Total cost: ${total_cost:.6f}")
    print(f"⏱️  Total duration: {total_duration:.1f}s ({total_duration/60:.1f}min)")
    print(f"💬 Total messages: {total_messages}")
    print(f"📂 Results saved to: {run_output_dir}")
    print(f"📂 Summary: {results_file}")
    
    return summary


def main():
    parser = argparse.ArgumentParser(description="Run BixBench benchmark")
    parser.add_argument("--capsule-limit", type=int, default=3,
                        help="Number of capsules to evaluate")
    parser.add_argument("--enable-learning", action="store_true",
                        help="Enable Learning module")
    parser.add_argument("--output-dir", default="benchmarks/bixbench/results",
                        help="Output directory for results")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING"],
                        help="Log level for benchmark")
    parser.add_argument("--continue", dest="continue_from", default=None,
                        help="Path to previous run directory to continue from")
    parser.add_argument("--skillbook-path", default=None,
                        help="Path to skillbook.json for skill injection (overrides settings)")
    parser.add_argument("--team", default="default",
                        help="Team template ID to use (default: default)")
    parser.add_argument("--injection-mode", default="auto",
                        choices=["static", "dynamic", "auto"],
                        help="Injection mode: static (all skills upfront), dynamic (context-based), auto (config defaults)")
    parser.add_argument("--model", default="gemini/gemini-3-flash-preview",
                        help="Model name to use (default: gemini/gemini-3-flash-preview)")
    parser.add_argument("--capsules", default=None,
                        help="Comma-separated list of capsule IDs to run (e.g. bix-13)")
    
    args = parser.parse_args()
    
    asyncio.run(run_benchmark(
        capsule_limit=args.capsule_limit,
        enable_learning=args.enable_learning,
        output_dir=args.output_dir,
        log_level=args.log_level,
        continue_from=args.continue_from,
        skillbook_path=args.skillbook_path,
        team=args.team,
        injection_mode=args.injection_mode,
        model_name=args.model,
        capsules_filter=args.capsules,
    ))


if __name__ == "__main__":
    main()
