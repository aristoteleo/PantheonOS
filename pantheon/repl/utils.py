"""Shared utilities for REPL UI components."""
import sys
from rich.console import Console
from rich.markdown import Markdown
from rich.box import Box
from typing import Any
from datetime import datetime


# Custom box style mimicking Claude Code (Rounded outer, disconnected inner)
# We replace T-junctions (├, ┤, ┬, ┴) with straight lines (│, ─)
CLAUDE_BOX = Box(
    "╭──╮\n"
    "│  │\n"
    "│──│\n"
    "│  │\n"
    "│──│\n"
    "│──│\n"
    "│  │\n"
    "╰──╯\n"
)

def get_animation_frames() -> list:
    """Get animation frames, with ASCII fallback for Windows.
    
    Returns:
        List of spinner frame characters.
    """
    fancy_frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    ascii_frames = ["-", "\\", "|", "/"]
    
    try:
        # Check if stdout can handle unicode
        test_char = fancy_frames[0]
        if sys.stdout.encoding:
            test_char.encode(sys.stdout.encoding)
        return fancy_frames
    except (UnicodeEncodeError, LookupError, AttributeError):
        return ascii_frames

def get_separator() -> str:
    """Get separator character, with ASCII fallback.
    
    Returns:
        Separator character.
    """
    try:
        sep = "•"
        if sys.stdout.encoding:
            sep.encode(sys.stdout.encoding)
        return sep
    except (UnicodeEncodeError, LookupError, AttributeError):
        return "|"

def format_tool_name(tool_name: str) -> str:
    """Format tool name for status display: 'toolset__function' → 'function'.
    
    Args:
        tool_name: Raw tool name (potentially namespaced).
        
    Returns:
        Simplified tool name for display.
    """
    if not tool_name:
        return ""
    if "__" in tool_name:
        _, function = tool_name.split("__", 1)
        return function
    return tool_name

def format_relative_time(iso_time: str | datetime | None) -> str:
    """Format ISO time string to relative/friendly format.
    
    Args:
        iso_time: ISO format string or datetime object
        
    Returns:
        Formatted string (e.g. "Today 12:00", "5m ago")
    """
    if not iso_time:
        return "-"
    try:
        if isinstance(iso_time, str):
            dt = datetime.fromisoformat(iso_time)
        else:
            dt = iso_time
            
        now = datetime.now()
        diff = now - dt

        # Sub-minute fidelity for very recent items
        if diff.days == 0 and diff.seconds < 60:
            return "just now"
            
        # Standard relative formatting
        if diff.days == 0:
            return f"Today {dt.strftime('%H:%M')}"
        elif diff.days == 1:
            return f"Yesterday {dt.strftime('%H:%M')}"
        elif diff.days < 7:
            return dt.strftime("%a %H:%M")
        else:
            return dt.strftime("%b %d %H:%M")
    except Exception:
        return "-"

# Wave effect brightness levels (grey scale gradient)
# Using Hex codes for compatibility between Rich and prompt_toolkit
WAVE_COLORS = [
    "#4d4d4d", "#6b6b6b", "#8a8a8a", "#a8a8a8", "#c7c7c7", "#e3e3e3", 
    "#ffffff", 
    "#e3e3e3", "#c7c7c7", "#a8a8a8", "#8a8a8a", "#6b6b6b", "#4d4d4d"
]

def get_wave_color(index: int, offset: int) -> str:
    """Get color for wave animation at specific character index.
    
    Args:
        index: Character index in the string.
        offset: Animation frame offset.
        
    Returns:
        Hex color string.
    """
    pos = (index + offset) % len(WAVE_COLORS)
    return WAVE_COLORS[pos]


class _Utf8StdoutProxy:
    """Wrapper around StdoutProxy that ensures UTF-8 compatible output.

    On Windows, prompt_toolkit's StdoutProxy.encoding returns the system
    codepage (e.g. GBK/CP936) from Win32Output. Rich Console uses this
    encoding, causing UnicodeEncodeError on characters like • (U+2022)
    from Markdown rendering. This wrapper:
    1. Reports UTF-8 encoding so Rich doesn't attempt GBK encoding
    2. Catches any remaining UnicodeEncodeError from downstream writers
       and replaces unencodable chars with safe alternatives
    """

    # Characters Rich Markdown produces that aren't in GBK/CP936
    _SAFE_REPLACEMENTS = {
        "\u2022": "\u00b7",  # • BULLET → · MIDDLE DOT (GBK-safe)
        "\u2023": ">",       # ‣ TRIANGULAR BULLET
        "\u2043": "-",       # ⁃ HYPHEN BULLET
        "\u25cf": "*",       # ● BLACK CIRCLE
        "\u2013": "-",       # – EN DASH
        "\u2014": "--",      # — EM DASH
        "\u2018": "'",       # ' LEFT SINGLE QUOTE
        "\u2019": "'",       # ' RIGHT SINGLE QUOTE
        "\u201c": '"',       # " LEFT DOUBLE QUOTE
        "\u201d": '"',       # " RIGHT DOUBLE QUOTE
    }

    def __init__(self, wrapped):
        self._wrapped = wrapped

    @property
    def encoding(self):
        return "utf-8"

    def write(self, s):
        try:
            return self._wrapped.write(s)
        except UnicodeEncodeError:
            # Replace known problematic chars with GBK-safe alternatives
            for old, new in self._SAFE_REPLACEMENTS.items():
                s = s.replace(old, new)
            try:
                return self._wrapped.write(s)
            except UnicodeEncodeError:
                # Last resort: replace any remaining unencodable chars
                return self._wrapped.write(
                    s.encode("gbk", errors="replace").decode("gbk")
                )

    def flush(self):
        return self._wrapped.flush()

    def isatty(self):
        return True

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


class OutputAdapter:
    """Unified output adapter for prompt_toolkit + Rich integration.

    When inside patch_stdout(raw=True) context, all output must go through
    sys.stdout to be correctly captured and rendered above the prompt.
    This adapter automatically switches between default Console and
    stdout-bound Console based on context.
    """

    def __init__(self):
        # Default Console for non-patch_stdout context (e.g., banner)
        self._default_console = Console()
        # Patched Console, created when entering patch_stdout context
        self._patched_console: Console | None = None
        # Whether we're inside patch_stdout context
        self._in_patch_context = False

    @property
    def console(self) -> Console:
        """Get the appropriate Console based on current context.

        Returns:
            Console bound to sys.stdout when in patch_stdout context,
            otherwise returns the default Console.
        """
        if self._in_patch_context and self._patched_console is not None:
            return self._patched_console
        return self._default_console

    def enter_patch_context(self):
        """Called when entering patch_stdout context."""
        self._in_patch_context = True
        # Wrap the patched sys.stdout with UTF-8 encoding declaration.
        # This prevents Rich from using the system codepage (GBK on Chinese
        # Windows) which can't encode Unicode chars like • from Markdown.
        self._patched_console = Console(
            file=_Utf8StdoutProxy(sys.stdout),
            force_terminal=True,
            legacy_windows=False,
        )

    def exit_patch_context(self):
        """Called when exiting patch_stdout context."""
        self._in_patch_context = False
        self._patched_console = None
    
    def print(self, *args, **kwargs):
        """Print with automatic context-aware console selection."""
        self.console.print(*args, **kwargs)
    
    def print_markdown(self, content: str):
        """Print markdown content."""
        self.console.print(Markdown(content))


def format_token_count(count: int) -> str:
    """Format token count with dynamic K/M units."""
    if count >= 1_000_000:
        return f"{count/1_000_000:.1f}M"
    if count >= 1000:
        return f"{count // 1000}K"
    return str(count)



async def get_detailed_token_stats(chatroom, chat_id, team, fallback: dict) -> dict:
    """Gather detailed token statistics (async) including tools and system prompt."""
    from pantheon.utils.llm import count_tokens_in_messages, process_messages_for_model
    from pantheon.utils.log import logger

    tools = []
    messages = []
    raw_messages = []
    model = "unknown"
    system_prompt = None

    # Get agent/model/tools/instructions
    if team and team.agents:
        # Default to first agent unless we can determine active one
        agent = list(team.agents.values())[0]
        
        model = (agent.models[0] if isinstance(getattr(agent, 'models', None), list) 
                 else getattr(agent, 'models', None) or getattr(agent, 'model', 'unknown'))
        
        # Resolve model tags (e.g. "high", "normal") to actual model names
        # so litellm.get_model_info() can look up the correct context window
        try:
            from pantheon.agent import _is_model_tag, _resolve_model_tag
            if isinstance(model, str) and _is_model_tag(model):
                resolved = _resolve_model_tag(model)
                if resolved:
                    model = resolved[0]
        except Exception:
            pass
        
        system_prompt = getattr(agent, 'instructions', None)
        
        try:
             tools = await agent.get_tools_for_llm()
        except Exception as e:
             logger.warning(f"Failed to get tools: {e}")

    tool_names = [t["function"]["name"] for t in tools] if tools else []

    # Try to get messages from chatroom via memory_manager
    if chatroom and chat_id:
        try:
            if hasattr(chatroom, 'memory_manager'):
                # Read-only: getting token/context statistics for display, no need to fix
                memory = chatroom.memory_manager.get_memory(chat_id)
                if memory:
                    # ✅ Get root agent messages for token/context statistics
                    # This prevents context% from being inflated by sub-agent calls
                    raw_messages = memory.get_messages(execution_context_id=None) or []
                    messages = process_messages_for_model(raw_messages, model)
                    
                    # ✅ Get ALL messages for cost calculation (including sub-agents)
                    # Cost should include all LLM calls, not just root agent
                    all_messages_for_cost = memory.get_messages() or []
        except Exception as e:
            logger.warning(f"Failed to get messages for token stats: {e}")

    # Prepend system prompt if not present
    if system_prompt:
        if not messages or messages[0].get("role") != "system":
             messages.insert(0, {"role": "system", "content": system_prompt})

    if messages:
        try:
            # Calculate token statistics (root agent only)
            info = count_tokens_in_messages(
                messages,
                model,
                tools=tools
            )
            
            # ✅ Fix max_tokens fallback: if count_tokens_in_messages returned
            # the generic 200K default (litellm doesn't recognize the model),
            # read the runtime-recorded max_tokens from message metadata instead.
            # This is the same approach the REPL fast path uses (core.py:597).
            if info.get("max_tokens", 0) <= 200_000 and raw_messages:
                # Find last message with runtime metadata (written by collect_message_stats_lightweight)
                for msg in reversed(raw_messages):
                    meta = msg.get("_metadata", {})
                    runtime_max = meta.get("max_tokens", 0)
                    if runtime_max > 200_000:
                        # Override with runtime value and recalculate derived fields
                        info["max_tokens"] = runtime_max
                        info["remaining"] = max(0, runtime_max - info.get("total", 0))
                        total = info.get("total", 0)
                        info["usage_percent"] = round(total / runtime_max * 100, 1) if runtime_max > 0 else 0
                        info["warning_90"] = info["usage_percent"] >= 90
                        info["critical_95"] = info["usage_percent"] >= 95
                        break

            # ✅ Recalculate total_cost from ALL messages (including compressed)
            # Use for_llm=False to get full message history
            all_messages_for_cost = memory.get_messages(for_llm=False)
            
            from pantheon.utils.llm import calculate_total_cost_from_messages
            total_cost = calculate_total_cost_from_messages(all_messages_for_cost)
            
            # Override the cost from count_tokens_in_messages
            info["total_cost"] = total_cost
            
            info["model"] = model
            info["leader_tools"] = tool_names
            return info
        except Exception as e:
            logger.warning(f"Failed to count tokens: {e}")

    # Fallback if calculation failed — also try to read max_tokens from metadata
    runtime_max_tokens = 200_000
    if raw_messages:
        for msg in reversed(raw_messages):
            meta = msg.get("_metadata", {})
            if meta.get("max_tokens", 0) > 0:
                runtime_max_tokens = meta["max_tokens"]
                break

    total = fallback.get("total_input_tokens", 0) + fallback.get("total_output_tokens", 0)
    return {
        "total": total, "max_tokens": runtime_max_tokens, "remaining": runtime_max_tokens - total,
        "usage_percent": round(total / runtime_max_tokens * 100, 1) if total else 0,
        "by_role": {"user": fallback.get("total_input_tokens", 0), "assistant": fallback.get("total_output_tokens", 0)},
        "message_counts": {"user": fallback.get("message_count", 0), "assistant": fallback.get("message_count", 0)},
        "warning_90": False, "critical_95": False, "current_cost": 0, "model": model,
        "system_prompt": 0, "tools_definition": 0, "error": None, "leader_tools": tool_names
    }



def render_token_panel(console: Console, info: dict, session_start: datetime):
    """Render Claude Code-style token analysis panel."""
    total = info.get("total", 0)
    by_role = info.get("by_role", {})
    system_prompt_tokens = info.get("system_prompt", 0)
    tools_definition_tokens = info.get("tools_definition", 0)
    tools_count = info.get("tools_count", 0)
    
    msg_counts = info.get("message_counts", {})
    max_tok = info.get("max_tokens", 200000)
    usage_pct = info.get("usage_percent", 0)
    
    B = "[bold blue]"  # Box border color
    role_colors = {
        "system_prompt": "blue", 
        "tools_definition": "cyan",
        "system": "blue", 
        "user": "green", 
        "assistant": "yellow", 
        "tool": "magenta"
    }
    
    console.print()
    console.print(f"{B}── Context ─────────────────────────────────────────────────────────[/]")
    
    if total == 0:
        console.print(f"  [dim]No token usage data yet[/]")
        console.print(f"{B}────────────────────────────────────────────────────────────────────[/]")
        return
    
    # Build multi-color progress bar by role
    bar_w = 50
    used_ratio = total / max_tok if max_tok > 0 else 0
    used_width = max(1, round(used_ratio * bar_w)) if total > 0 else 0  # At least 1 block if any usage
    
    # Segments in order
    segments_data = [
        ("system_prompt", system_prompt_tokens),
        ("tools_definition", tools_definition_tokens),
        ("system", by_role.get("system", 0)),
        ("user", by_role.get("user", 0)),
        ("assistant", by_role.get("assistant", 0)),
        ("tool", by_role.get("tool", 0)),
    ]
    
    # Build colored segments
    bar_segments = []
    for name, count in segments_data:
        if count > 0 and total > 0:
            seg_width = max(1, round((count / total) * used_width))
            color = role_colors.get(name, "white")
            bar_segments.append(f"[{color}]{'█' * seg_width}[/]")
    
    # Combine segments and add remaining empty space
    bar = "".join(bar_segments)
    # Calculate actual filled width from segments (may exceed due to rounding)
    actual_filled = sum(max(1, round((count / total) * used_width)) 
                        for name, count in segments_data 
                        if count > 0 and total > 0)
    remaining_width = max(0, bar_w - actual_filled)
    bar += f"[dim]{'░' * remaining_width}[/]"
    
    max_disp = format_token_count(max_tok)
    console.print(f"  {bar} {usage_pct}% of {max_disp}")
    console.print(f"  [dim]Used:[/] {format_token_count(total)} [dim]• Remaining:[/] {format_token_count(info.get('remaining', 0))}")
    console.print()
    
    def print_legend_item(name, label, count, msg_count=None, unit="msgs"):
        if count > 0:
            pct = count / total * 100
            color = role_colors.get(name, "white")
            msg_info = f"[dim]{msg_count} {unit}[/]" if msg_count is not None else ""
            console.print(f"  [{color}]●[/] {label:<24} {format_token_count(count):>8} ({pct:4.1f}%) {msg_info}")

    print_legend_item("system_prompt", "System Prompt", system_prompt_tokens)
    print_legend_item("tools_definition", "Tools Definition", tools_definition_tokens, tools_count, "tools")
    print_legend_item("system", "System Msgs", by_role.get("system", 0), msg_counts.get("system", 0))
    print_legend_item("user", "User", by_role.get("user", 0), msg_counts.get("user", 0))
    print_legend_item("assistant", "Assistant", by_role.get("assistant", 0), msg_counts.get("assistant", 0))
    print_legend_item("tool", "Tool", by_role.get("tool", 0), msg_counts.get("tool", 0))
    
    # Session stats
    console.print()
    dur = int((datetime.now() - session_start).total_seconds() / 60)
    model = info.get("model", "unknown")[:30]
    total_msgs = sum(msg_counts.values())
    console.print(f"  [dim]Messages:[/] {total_msgs} [dim]• Duration:[/] {dur}m [dim]• Model:[/] {model}")
    
    if (total_c := info.get("total_cost")) is not None and total_c > 0:
         console.print(f"  [dim]Total Cost:[/] ${total_c:.4f}")
    
    # Warning
    if info.get("warning_90") or info.get("critical_95"):
        console.print()
        if info.get("critical_95"):
            console.print(f"  [bold red]⚠ CRITICAL:[/] Context nearly full!")
        else:
            console.print(f"  [bold yellow]⚠ WARNING:[/] Context usage over 90%")
    
    console.print(f"{B}────────────────────────────────────────────────────────────────────[/]")
