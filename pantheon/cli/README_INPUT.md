# Pantheon CLI: Natural Language to Tool Mapping Guide

This guide helps you understand which tools to call based on your natural language requests in the Pantheon CLI. Simply describe what you want to do, and find the corresponding tool function.

## How to Use This Guide

When you have a task in mind, find it in the **"What You Want to Say"** sections below, then use the corresponding **Tool Call** in the CLI.

---

## Task Management & Planning

### What You Want to Say → Tool to Call

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "I need to track my project progress" | `add_todo("Complete data analysis project")` | Creates a new task with automatic breakdown into subtasks |
| "Show me what I'm working on" | `show_todos()` | Displays all current tasks with status and progress |
| "What should I do next?" | `execute_current_task()` | Provides intelligent guidance for the current active task |
| "I finished this step" | `mark_task_done("Completed data preprocessing")` | Marks current task as complete and moves to next task |
| "Clear all my tasks" | `clear_all_todos()` | Removes all tasks to start fresh |
| "Remove completed tasks" | `clear_completed_todos()` | Cleans up finished tasks while keeping active ones |
| "Update task status" | `update_todo_status("task_id", "completed")` | Changes status of specific task |
| "Mark specific task complete" | `complete_todo("task_id")` | Marks specific todo item as completed |
| "Start working on task" | `start_todo("task_id")` | Sets specific task as in progress |
| "Delete a task" | `remove_todo("task_id")` | Removes specific todo from list |
| "Get next task" | `get_next_todo()` | Returns the next pending task to work on |
| "Work on next item" | `work_on_next_todo()` | Starts working on the next pending task |

---

## Bioinformatics & ATAC-seq Analysis

**For comprehensive bioinformatics and ATAC-seq analysis tools, see the detailed guide:**

📖 **[Bioinformatics Tools Guide](README_BIOINFORMATICS.md)**

**Quick Overview:**
- **ATAC-seq Analysis**: `/atac init` (enter mode), `/atac upstream <folder>` (run analysis)
- **Single-Cell ATAC**: Cell Ranger ATAC toolset for 10x Genomics data
- **Core Functions**: Species detection, genome setup, quality control, alignment, peak calling
- **Key Tools**: `atac.scan_folder()`, `atac.auto_detect_species()`, `atac.setup_genome_resources()`, `cellranger_atac.count()`

The bioinformatics guide includes detailed mappings for:
- Project setup and species detection
- Genome resource management
- Quality control and preprocessing  
- Alignment and BAM processing
- Peak calling and analysis
- Visualization and reporting
- Single-cell ATAC-seq workflows
- Tool installation and management

---

## Python Code Execution

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Run this Python code" | `run_python("print('Hello World')")` | Executes Python code with full package support |
| "Run code in specific interpreter" | `run_code_in_interpreter("interpreter_id", "print('test')")` | Executes code in specific Python interpreter session |
| "Create new Python interpreter" | `new_interpreter("my_python_session")` | Creates new isolated Python interpreter session |
| "Delete Python interpreter" | `delete_interpreter("interpreter_id")` | Removes Python interpreter session |

---

## R Statistical Computing

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Run R statistical analysis" | `run_r("summary(mtcars)")` | Executes R code for statistical computing |
| "Run R code in specific session" | `run_code_in_interpreter("r_session", "plot(iris)")` | Executes R code in specific interpreter session |
| "Create new R interpreter" | `new_interpreter("my_r_session")` | Creates new isolated R interpreter session |
| "Delete R interpreter" | `delete_interpreter("r_session_id")` | Removes R interpreter session |
| "Get R interpreter output" | `get_interpreter_output("r_session_id")` | Retrieves output from R interpreter session |

---

## File Operations & Management

### Basic File Operations

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "List files in directory" | `list_files("/path/to/directory")` | Lists all files and directories in specified path |
| "Read this file" | `read_file("/path/to/file.txt")` | Reads file content with syntax highlighting |
| "Write content to file" | `write_file("/path/to/output.txt", "content")` | Writes text content to specified file |
| "Create directory" | `create_directory("/path/to/new/folder")` | Creates directory structure recursively |
| "Delete file" | `delete_file("/path/to/unwanted.txt")` | Removes specified file |
| "Delete directory" | `delete_directory("/path/to/folder")` | Removes directory and all contents |
| "Move file" | `move_file("/old/path/file.txt", "/new/path/file.txt")` | Moves file to new location |
| "Show file tree" | `list_file_tree("/project")` | Displays hierarchical directory structure |
| "View images" | `observe_images("/path/to/images")` | Displays image files in directory |
| "Read PDF file" | `read_pdf("/path/to/document.pdf")` | Extracts and displays PDF content |

---

## Text Editing & File Modification

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Edit specific line in file" | `edit_file("/path/file.py", line_number=10, new_content="updated line")` | Modifies specific lines in text files |
| "Search for text in file" | `search_in_file("/path/file.txt", "search_term")` | Searches for specific text within a file |
| "Insert text at line" | `insert_at_line("/path/file.py", line_number=5, content="import pandas")` | Inserts new content at specified line number |
| "Delete lines from file" | `delete_lines("/path/file.txt", start_line=10, end_line=15)` | Removes specified range of lines from file |
| "Create new file" | `create_file("/path/to/new_file.txt", "initial content")` | Creates new file with specified content |

---

## Code Search & Analysis

### Basic Search Operations

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Search files by pattern" | `glob("/project", "*.py")` | Finds files matching glob pattern |
| "Search text in files" | `grep("/project", "TODO", "*.py")` | Searches for text patterns in files |
| "List directory contents" | `ls("/path/to/directory")` | Lists files and directories with details |

---

## Code Quality & Validation

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Check Python syntax" | `validate_python_code("/path/script.py")` | Validates Python code syntax and structure |
| "Validate shell command" | `validate_command("complex_command")` | Validates shell command syntax |
| "Find common code errors" | `detect_common_errors("/path/script.py")` | Detects common programming errors |
| "Suggest function alternatives" | `suggest_function_alternatives("old_function")` | Recommends modern alternatives to functions |
| "Validate function call" | `validate_function_call("function_name", ["arg1", "arg2"])` | Checks function call syntax and arguments |
| "Check import statements" | `validate_imports("/path/script.py")` | Validates import statements in Python files |
| "Check code style" | `check_code_style("/path/script.py")` | Analyzes code style and formatting |

---

## Jupyter Notebook Operations

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Show notebook contents" | `read_notebook("/path/analysis.ipynb")` | Displays notebook with beautiful formatting |
| "Edit notebook cell" | `edit_notebook_cell("/path/notebook.ipynb", cell_index=2, new_content="code")` | Modifies specific notebook cells |
| "Add new cell" | `add_notebook_cell("/path/notebook.ipynb", content="# Analysis", cell_type="markdown")` | Inserts new cells at specified positions |
| "Delete notebook cell" | `delete_notebook_cell("/path/notebook.ipynb", cell_index=3)` | Removes specific cell from notebook |
| "Create new notebook" | `create_notebook("/path/new_analysis.ipynb")` | Creates new empty Jupyter notebook |
| "Copy notebook cell" | `copy_notebook_cell("/path/notebook.ipynb", source_index=1, target_index=5)` | Copies cell to different position |
| "Move notebook cell" | `move_notebook_cell("/path/notebook.ipynb", from_index=2, to_index=8)` | Moves cell to different position |
| "Add notebook template" | `add_notebook_template("/path/notebook.ipynb", template_type="data_analysis")` | Adds predefined template cells |

---

## Web Operations

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Fetch web page content" | `web_fetch("https://example.com")` | Retrieves and displays web page content |
| "Search the internet" | `web_search("Python data analysis tutorials")` | Searches web using search engine |
| "Google search for information" | `google_search("ATAC-seq analysis methods")` | Performs Google search and returns results |
| "Scrape web page data" | `fetch_web_page("https://site.com")` | Extracts structured data from web pages |

---

## System & Shell Operations

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Create new shell session" | `new_shell("my_shell_session")` | Creates new isolated shell environment |
| "Close shell session" | `close_shell("shell_session_id")` | Terminates specific shell session |
| "Run command in shell" | `run_command_in_shell("shell_id", "ls -la")` | Executes command in specific shell session |
| "Get shell output" | `get_shell_output("shell_session_id")` | Retrieves output from shell session |
| "Run single command" | `run_command("ps aux")` | Executes single shell command |

---

## File Transfer & Synchronization

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Open file for writing" | `open_file_for_write("/path/to/file.txt")` | Opens file handle for streaming write operations |
| "Write data chunk" | `write_chunk("file_handle", "data_chunk")` | Writes data chunk to open file handle |
| "Close file handle" | `close_file("file_handle")` | Closes file handle and finalizes write |

---

## Vector Database & RAG Operations

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "Search vector database" | `query_vector_db("database_name", "search query")` | Performs semantic search in vector database |
| "Get database information" | `get_vector_db_info("database_name")` | Retrieves metadata and statistics about vector database |

---

## Service & Endpoint Management

| **Natural Language Request** | **Tool Call** | **Function Description** |
|------------------------------|---------------|--------------------------|
| "List all services" | `list_services()` | Shows all registered service endpoints |
| "Get image as base64" | `fetch_image_base64("image_url")` | Retrieves image and converts to base64 format |
| "Add new service" | `add_service("service_name", "http://localhost:8080")` | Registers new service endpoint |
| "Get service details" | `get_service("service_name")` | Retrieves information about specific service |
| "Check services status" | `services_ready()` | Checks if all registered services are available |

---

## Common Workflow Patterns

### Pattern 1: Complete Data Analysis Workflow
```
User: "I want to do a complete RNA-seq analysis"
CLI: add_todo("Complete RNA-seq analysis from FASTQ to results")
CLI: execute_current_task()  # Get guidance for first step
CLI: run_python("# preprocessing script")  # Execute analysis
CLI: mark_task_done("Preprocessing completed")  # Mark progress
```

### Pattern 2: ATAC-seq Analysis from Start
```
User: "Analyze ATAC-seq data in my folder"
CLI: /atac upstream ./data_folder  # Start upstream analysis
CLI: atac.scan_folder("./data_folder")  # Scan contents
CLI: atac.auto_detect_species("./data_folder")  # Detect species
CLI: atac.setup_genome_resources("human", "hg38")  # Setup genome
CLI: atac.auto_align_fastq("./data_folder")  # Align reads
```

### Pattern 3: Code Quality & Validation
```
User: "Check and fix my Python code"
CLI: validate_python_code("/path/script.py")  # Check syntax
CLI: check_code_style("/path/script.py")  # Quality check
CLI: edit_file("/path/script.py", line_number=10, new_content="fixed_line")  # Fix issues
CLI: validate_python_code("/path/script.py")  # Re-validate
```

### Pattern 4: Research & Documentation
```
User: "Research best practices for my analysis"
CLI: web_search("ATAC-seq analysis best practices 2024")  # Search web
CLI: google_search("quality control methods genomics")  # Google search
CLI: query_vector_db("research_docs", "QC metrics importance")  # Search docs
CLI: write_file("/notes/research.md", "findings")  # Save notes
```

### Pattern 5: Multi-Session Development
```
User: "Set up development environment"
CLI: new_interpreter("data_analysis_python")  # Create Python session
CLI: new_interpreter("stats_r_session")  # Create R session  
CLI: new_shell("system_commands")  # Create shell session
CLI: run_code_in_interpreter("data_analysis_python", "import pandas")  # Setup Python
CLI: run_code_in_interpreter("stats_r_session", "library(ggplot2)")  # Setup R
```

## Pro Tips

1. **Start with TODO** - Use `add_todo()` for complex multi-step tasks
2. **Get Guidance** - Use `execute_current_task()` when unsure what to do next  
3. **Validate First** - Check syntax and files before running expensive operations
4. **Track Progress** - Always use `mark_task_done()` to track completion
5. **Use Sessions** - Create interpreter/shell sessions for complex workflows
6. **Combine Tools** - Chain multiple tools together for powerful workflows
7. **Search Smart** - Use `glob()` for files, `grep()` for content, `web_search()` for research
8. **Manage Quality** - Validate code with quality tools before execution

This guide maps your natural language intentions to specific tool calls, making it easy to accomplish any task in the Pantheon CLI efficiently.