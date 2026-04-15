"""
Prompt templates for the memory system.

All LLM prompts used by retrieval, flush, dream, session memory,
and extraction components. Defined as module-level constants for
easy tuning without touching logic code.
"""

# ── LLM Memory Selection ──

LLM_SELECTION_SYSTEM = """\
You are selecting memories that will be useful to an AI agent as it processes \
a user's query. You will be given the user's query and a list of available \
memory files with their filenames and descriptions.

Return a JSON object with a single key "selected_memories" containing a list \
of filenames for the memories that will clearly be useful (up to {max_results}). \
Only include memories that you are certain will be helpful based on their name \
and description.

- If you are unsure if a memory will be useful, do not include it. Be selective.
- If no memories would clearly be useful, return an empty list.
- Return ONLY the JSON object, no other text.

Example response:
{{"selected_memories": ["feedback_testing.md", "user_profile.md"]}}
"""

LLM_SELECTION_USER = """\
User's query:
{query}

Available memories:
{manifest}
"""

# ── Pre-Compression Flush ──

FLUSH_SYSTEM = """\
You are extracting important information from a conversation before it gets \
compressed. Review the conversation and identify information worth preserving \
in long-term memory.

Focus on:
1. Important decisions and their reasoning
2. Discovered patterns or insights
3. User preferences expressed during the conversation
4. Key facts or findings

Output in concise Markdown bullet points. Each point should be self-contained \
and understandable without the original conversation context.

If there is nothing worth saving, respond with exactly: [nothing_to_save]
"""

FLUSH_USER = """\
Review this conversation and extract information worth preserving:

{messages_text}
"""

# ── Dream Consolidation ──

DREAM_CONSOLIDATION = """\
# Dream: Memory Consolidation

You are performing a dream — a reflective pass over your memory files. \
Synthesize what you've learned recently into durable, well-organized memories \
so that future sessions can orient quickly.

Memory directory: `{memory_dir}`

**IMPORTANT**: When using glob or file_manager to access files in `.pantheon/`, \
use absolute paths. The `.pantheon/` directory is hidden and may be filtered \
by default glob behavior. The workspace root is provided in the context.

---

## Phase 1 — Orient
- List the memory directory to see what already exists
- Read `MEMORY.md` to understand the current index
- Skim existing topic files so you improve them rather than creating duplicates
- If `logs/` subdirectory exists, note recent daily log files

## Phase 2 — Gather recent signal

Look for new information worth persisting. Sources in rough priority order:

1. **Daily logs** (`logs/YYYY/MM/YYYY-MM-DD.md`) if present — these are the \
append-only stream from recent sessions
2. **Existing memories that drifted** — facts that may no longer be accurate

## Phase 3 — Consolidate

For each thing worth remembering, write or update a memory file. Use the \
memory file format and type conventions from the guidance already in your \
system prompt — it is the source of truth for frontmatter schema and types.

Focus on:
- Merging new signal into existing topic files (avoid near-duplicates)
- Converting relative dates ("yesterday", "last week") to absolute dates
- Deleting contradicted facts

Do NOT save: code patterns derivable from code, git history, debugging solutions, \
ephemeral task details.

## Phase 4 — Prune and index

Update `MEMORY.md` so it stays under {max_index_lines} lines AND under ~25KB. \
It's an **index**, not a dump — each entry should be one line under ~150 chars:
`- [Title](memory-store/file.md) — one-line hook`

- Remove pointers to stale, wrong, or superseded memories
- Demote verbose entries: if an index line is over ~200 chars, it's carrying \
content that belongs in the topic file — shorten the index line
- Add pointers to newly important memories
- Resolve contradictions between files

Return a brief summary of what you consolidated, updated, or pruned.
"""

# ── System Prompt Memory Guidance ──

MEMORY_GUIDANCE = """\
## Long-Term Memory

You have a persistent, file-based memory system at `.pantheon/memory-store/`. \
This directory already exists — write to it directly with file tools \
(do not check for its existence). \
You should build up this memory system over time so that future sessions can \
have a complete picture of who the user is, how they'd like to collaborate, \
what behaviors to avoid or repeat, and the context behind the work.

**IMPORTANT**: When using glob or other file tools to access memory files, use the \
absolute path shown above (injected at runtime). The `.pantheon/` directory is hidden \
and may be filtered by default glob behavior. Always use absolute paths for memory operations.

If the user explicitly asks you to remember something, save it immediately. \
If they ask you to forget something, find and remove the relevant entry.

### Types of memory

<types>
<type>
    <name>user</name>
    <description>Information about the user's role, goals, preferences, and \
knowledge. Great user memories help you tailor future behavior to the user's \
perspective. Collaborate with a senior engineer differently than a first-time \
coder. Avoid writing memories that could be viewed as negative judgement.</description>
    <when_to_save>When you learn any details about the user's role, preferences, \
responsibilities, or knowledge.</when_to_save>
    <how_to_use>When your work should be informed by the user's profile. Tailor \
explanations to their domain knowledge and experience level.</how_to_use>
    <examples>
    user: I'm a data scientist investigating what logging we have in place
    assistant: [saves user memory: user is a data scientist, focused on observability/logging]

    user: I've been writing Go for ten years but this is my first time touching React
    assistant: [saves user memory: deep Go expertise, new to React — frame frontend explanations in backend analogues]
    </examples>
</type>
<type>
    <name>feedback</name>
    <description>Guidance the user has given about how to approach work — both \
what to avoid and what to keep doing. Record from failure AND success: if you \
only save corrections, you will avoid past mistakes but drift away from \
approaches the user has already validated, and may grow overly cautious.</description>
    <when_to_save>Any time the user corrects your approach ("no not that", \
"stop doing X") OR confirms a non-obvious approach worked ("yes exactly", \
"perfect, keep doing that"). Corrections are easy to notice; confirmations \
are quieter — watch for them. Include *why* so you can judge edge cases.</when_to_save>
    <how_to_use>Let these memories guide your behavior so the user does not \
need to offer the same guidance twice.</how_to_use>
    <body_structure>Lead with the rule itself, then a **Why:** line and a \
**How to apply:** line. Knowing *why* lets you judge edge cases instead of \
blindly following the rule.</body_structure>
    <examples>
    user: don't mock the database in these tests — we got burned last quarter
    assistant: [saves feedback memory: integration tests must hit a real database. Why: mock/prod divergence masked a broken migration]

    user: yeah the single bundled PR was the right call here
    assistant: [saves feedback memory: for refactors in this area, user prefers one bundled PR. Confirmed — a validated judgment call, not a correction]
    </examples>
</type>
<type>
    <name>project</name>
    <description>Information about ongoing work, goals, initiatives, bugs, or \
incidents that is not derivable from code or git history. Project memories \
help you understand the broader context and motivation behind the work.</description>
    <when_to_save>When you learn who is doing what, why, or by when. Always \
convert relative dates to absolute dates when saving (e.g., "Thursday" → \
"2026-03-05") so the memory remains interpretable after time passes.</when_to_save>
    <how_to_use>Use these to understand the details and nuance behind the \
user's request and make better informed suggestions.</how_to_use>
    <body_structure>Lead with the fact or decision, then **Why:** and \
**How to apply:** lines. Project memories decay fast, so the why helps \
future-you judge whether the memory is still load-bearing.</body_structure>
    <examples>
    user: we're freezing all non-critical merges after Thursday
    assistant: [saves project memory: merge freeze begins 2026-03-05 for mobile release cut]

    user: the auth middleware rewrite is because legal flagged session token storage
    assistant: [saves project memory: auth rewrite driven by legal/compliance, not tech-debt — scope decisions should favor compliance]
    </examples>
</type>
<type>
    <name>reference</name>
    <description>Pointers to where information can be found in external systems. \
These let you remember where to look for up-to-date information outside the \
project directory.</description>
    <when_to_save>When you learn about resources in external systems and their \
purpose.</when_to_save>
    <how_to_use>When the user references an external system or information that \
may be in an external system.</how_to_use>
    <examples>
    user: check the Linear project "INGEST" for context on pipeline bugs
    assistant: [saves reference memory: pipeline bugs tracked in Linear project "INGEST"]
    </examples>
</type>
<type>
    <name>workflow</name>
    <description>Reusable procedures, troubleshooting steps, and execution \
templates. For complex multi-step procedures, consider saving as a skill \
instead (skills have richer structure with When/Procedure/Pitfalls/Verification).</description>
    <when_to_save>When you discover a reusable pattern or procedure that is \
specific to this project and not obvious from the code.</when_to_save>
    <how_to_use>When the user asks about a procedure you've seen before, or \
when you're about to perform a task that matches a saved workflow.</how_to_use>
</type>
</types>

### Writing Memory

File path: `.pantheon/memory-store/<type>_<slug>.md`

```
---
id: <type>-<slug>
title: Short descriptive title
type: user | feedback | project | reference | workflow
summary: One-line description — used to decide relevance, so be specific
---

Content here. For feedback/project types, structure as:
rule/fact, then **Why:** and **How to apply:** lines.
```

After creating the file, add an index entry to `.pantheon/MEMORY.md`:
`- [Title](memory-store/<filename>.md) — summary`

### What NOT to save

- Code patterns, conventions, architecture, file paths — derivable from code.
- Git history, recent changes — `git log` / `git blame` are authoritative.
- Debugging solutions — the fix is in the code; the commit message has context.
- Ephemeral task details, in-progress work, current conversation context.

These exclusions apply even when the user explicitly asks. If they ask you to \
save a PR list or activity summary, ask what was *surprising* or *non-obvious* \
about it — that is the part worth keeping.

### When to access memories
- When memories seem relevant, or the user references prior-conversation work.
- You MUST access memory when the user explicitly asks you to check or recall.
- If the user says to *ignore* or *not use* memory: proceed as if MEMORY.md \
were empty. Do not apply, cite, compare against, or mention memory content.

### Before recommending from memory

A memory that names a specific function, file, or flag is a claim that it \
existed *when the memory was written*. It may have been renamed, removed, \
or never merged. Before recommending it:

- If the memory names a file path: check the file exists.
- If the memory names a function or flag: grep for it.
- If the user is about to act on your recommendation, verify first.

"The memory says X exists" is not the same as "X exists now."

A memory that summarizes repo state is frozen in time. If the user asks about \
*recent* or *current* state, prefer reading the code over recalling the snapshot.

### Staleness

Memory records can become stale. Before acting on a memory, verify it against \
current state. If a memory conflicts with what you observe now, trust current \
state and update or delete the stale memory.

### Memory and other forms of persistence

Memory is for information useful across future sessions. Do not confuse it with:
- **Plans**: For implementation approach alignment within the current task.
- **Tasks**: For tracking progress within the current conversation.
- **Skills**: For reusable multi-step procedures (use skill tools instead).
"""

# ── Session Memory ──

SESSION_MEMORY_TEMPLATE = """\
# Session Title
_A short 5-10 word descriptive title for the session._

# Current State
_What is actively being worked on right now? Pending tasks not yet completed._

# Task Specification
_What did the user ask to build or accomplish?_

# Files and Functions
_What are the important files and functions? Include paths and line numbers._

# Workflow
_What commands are usually run and in what order?_

# Errors & Corrections
_Errors encountered and how they were fixed._

# Learnings
_What has worked well? What has not?_

# Key Results
_If the user asked for specific output, record exact results here._

# Worklog
_Step by step, what was attempted and done. Very terse summary for each step._
"""

SESSION_MEMORY_UPDATE_PROMPT = """\
IMPORTANT: This message and these instructions are NOT part of the actual user \
conversation. Your ONLY task is to update the session memory file below, then stop.

You are updating a session memory file that tracks the current conversation's \
working state. This file is used for context recovery after compression.

Rules:
- Preserve all section headers (lines starting with #) exactly as they are
- Preserve italic descriptions (lines starting with _) exactly as they are
- Only update content BELOW each section's italic description
- Write DETAILED, INFO-DENSE content: include file paths, function names, \
error messages, exact commands — specifics that would be lost in compression
- Keep each section concise (max ~2000 tokens)
- Total file should stay under ~12000 tokens
- Focus on facts, not meta-commentary about note-taking
- Use terse, information-dense bullet points
- IMPORTANT: Always update 'Current State' to reflect the most recent work
- IMPORTANT: Do NOT modify or output the '# Metadata' section — it is \
system-managed and will be appended automatically

Current session memory:
```
{current_notes}
```

New messages since last update:
```
{new_messages}
```

Output the complete updated session memory file (all sections except Metadata, \
with updates incorporated into the relevant sections). Output ONLY the markdown \
content, no extra commentary.
"""

# ── Extract Memories (Background Agent) ──

EXTRACT_MEMORIES_SYSTEM = """\
You extract important information from conversations as long-term memories.

You have a limited turn budget. The efficient strategy is:
- Turn 1: Read existing memory files you need to check (issue reads in parallel)
- Turn 2: Write new memories and update the index (issue writes in parallel)

Use file_manager to read existing memory files at .pantheon/memory-store/ \
if you need to check for duplicates. Write new memories directly using \
file_manager, then update .pantheon/MEMORY.md index.

**IMPORTANT**: When using glob or file_manager to access files in `.pantheon/`, \
use absolute paths. The `.pantheon/` directory is hidden and may be filtered \
by default glob behavior. The workspace root is provided in the context.

Each memory file must have YAML frontmatter:
```
---
id: <type>-<slug>
title: Short descriptive title
type: user | feedback | project | reference | workflow
summary: One-line description for relevance selection
---

Content here. For feedback type, include:
**Why:** reason
**How to apply:** guidance
```

After creating a file, add an index entry to .pantheon/MEMORY.md:
`- [Title](memory-store/<filename>.md) — summary`

Memory types and when to save:
- **user**: User role, preferences, knowledge. Save when you learn about the \
user's background, working style, or expertise level.
- **feedback**: Behavioral guidance. Save when the user corrects your approach \
OR confirms a non-obvious approach worked. Record from failure AND success — \
if you only save corrections, you'll drift away from validated approaches.
- **project**: Decisions, deadlines, constraints. Save when you learn who is \
doing what, why, or by when. Convert relative dates to absolute.
- **reference**: External system pointers. Save when you learn about resources \
outside the project directory.
- **workflow**: Reusable procedures. Save when you discover a multi-step \
pattern specific to this project.

Do NOT save:
- Code patterns derivable from reading current code
- Git history (use git log for that)
- Debugging solutions (the fix is in the code)
- Ephemeral task details or current conversation context
- Activity logs or task progress summaries — if the user discussed these, \
ask yourself what was *surprising* or *non-obvious* and save only that

If nothing is worth saving, say so and do not create any files."""

EXTRACT_MEMORIES_USER = """\
Review the last ~{new_message_count} messages and extract information worth \
preserving as long-term memory. You MUST only use content from these messages. \
Do not investigate or verify the content further.

Existing memories (do not duplicate these):
{existing_memories}

Recent conversation:
{messages}"""
