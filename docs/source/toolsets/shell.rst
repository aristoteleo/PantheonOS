Shell
=====

The Shell toolset provides agents with the ability to execute system commands in a controlled environment. This enables file system operations, process management, and system administration tasks.

Overview
--------

Key features:

* **Command Execution**: Run shell commands safely
* **File Operations**: Navigate and manipulate the file system
* **Process Management**: Start, stop, and monitor processes
* **Environment Control**: Manage environment variables
* **Security**: Sandboxed execution with restrictions

Basic Usage
-----------

Setting Up Shell Toolset
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolsets.shell import ShellToolSet
   from pantheon.toolsets.utils.toolset import run_toolsets
   from pantheon.agent import Agent
   
   async def create_shell_agent():
       # Create shell toolset with restrictions
       shell_tools = ShellToolSet(
           "shell_tools",
           allowed_commands=["ls", "cd", "pwd", "echo", "cat", "grep", "find"],
           working_directory="/safe/workspace"
       )
       
       # Run toolset service
       async with run_toolsets([shell_tools]):
           agent = Agent(
               name="system_admin",
               instructions="You are a system administrator. Use shell commands safely.",
               model="gpt-4o-mini"
           )
           
           await agent.remote_toolset(shell_tools.service_id)
           await agent.chat()

Basic Commands
~~~~~~~~~~~~~~

.. code-block:: python

   # File listing
   response = await agent.run([{
       "role": "user",
       "content": "List all Python files in the current directory"
   }])
   
   # Agent executes: ls *.py
   
   # File search
   response = await agent.run([{
       "role": "user",
       "content": "Find all files modified in the last 24 hours"
   }])
   
   # Agent executes: find . -type f -mtime -1

Security Configuration
----------------------

Restricted Commands
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Whitelist specific commands
   safe_shell = ShellToolSet(
       "safe_shell",
       allowed_commands=[
           "ls", "cd", "pwd",          # Navigation
           "cat", "head", "tail",      # File reading
           "grep", "sed", "awk",       # Text processing
           "wc", "sort", "uniq",       # Text utilities
           "echo", "printf",           # Output
           "date", "whoami"            # System info
       ],
       blocked_patterns=[
           "rm -rf",                   # Dangerous deletions
           "sudo",                     # Privilege escalation
           ">", ">>",                  # Redirections
           "|",                        # Pipes (optional)
           "eval", "exec"              # Code execution
       ]
   )

Sandboxed Environment
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Create isolated environment
   sandboxed_shell = ShellToolSet(
       "sandbox",
       chroot_dir="/sandbox",              # Isolated filesystem
       max_execution_time=30,              # Timeout in seconds
       max_output_size=1024*1024,          # 1MB output limit
       environment_vars={
           "PATH": "/sandbox/bin:/sandbox/usr/bin",
           "HOME": "/sandbox/home",
           "TMPDIR": "/sandbox/tmp"
       }
   )

Common Use Cases
----------------

File Management
~~~~~~~~~~~~~~~

.. code-block:: python

   file_manager = Agent(
       name="file_manager",
       instructions="""Manage files and directories:
       - List and search files
       - Read file contents
       - Check file properties
       - Create directory structures"""
   )
   
   # Agent operations:
   # List files with details
   # ls -la
   
   # Search for files
   # find . -name "*.log" -size +1M
   
   # Check disk usage
   # du -sh *
   
   # File permissions
   # ls -l | grep "^-rw"

Log Analysis
~~~~~~~~~~~~

.. code-block:: python

   log_analyst = Agent(
       name="log_analyst",
       instructions="Analyze system and application logs."
   )
   
   # Agent can:
   # Count errors
   # grep -c "ERROR" application.log
   
   # Find recent entries
   # tail -n 100 system.log | grep "WARNING"
   
   # Extract patterns
   # awk '/pattern/ {print $2, $5}' access.log
   
   # Summary statistics
   # cat logs/*.log | grep "Status:" | sort | uniq -c

System Monitoring
~~~~~~~~~~~~~~~~~

.. code-block:: python

   monitor_agent = Agent(
       name="system_monitor",
       instructions="Monitor system resources and processes."
   )
   
   # Available commands:
   # Process list
   # ps aux | head -20
   
   # Disk space
   # df -h
   
   # Memory usage
   # free -h
   
   # Network connections
   # netstat -an | grep ESTABLISHED

Advanced Features
-----------------

Command Chaining
~~~~~~~~~~~~~~~~

.. code-block:: python

   # Safe command chaining
   response = await agent.run([{
       "role": "user",
       "content": "Find all Python files and count lines of code"
   }])
   
   # Agent executes:
   # find . -name "*.py" -exec wc -l {} \; | awk '{sum+=$1} END {print sum}'

Script Execution
~~~~~~~~~~~~~~~~

.. code-block:: python

   script_runner = Agent(
       name="script_runner",
       instructions="Execute shell scripts safely."
   )
   
   # Create and run scripts
   response = await script_runner.run([{
       "role": "user",
       "content": """Create a script to backup configuration files"""
   }])
   
   # Agent creates:
   # #!/bin/bash
   # BACKUP_DIR="./backups/$(date +%Y%m%d)"
   # mkdir -p "$BACKUP_DIR"
   # find . -name "*.conf" -o -name "*.cfg" | while read file; do
   #     cp "$file" "$BACKUP_DIR/"
   # done
   # echo "Backup completed to $BACKUP_DIR"

Environment Management
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   env_manager = Agent(
       name="env_manager",
       instructions="Manage environment variables and settings."
   )
   
   # Agent can:
   # Show environment
   # env | grep -E "(PATH|HOME|USER)"
   
   # Export variables (within session)
   # export MY_VAR="value"
   # echo $MY_VAR

Error Handling
--------------

Graceful Failure
~~~~~~~~~~~~~~~~

.. code-block:: python

   class RobustShellAgent(Agent):
       async def execute_command_safely(self, command: str):
           """Execute command with error handling."""
           try:
               response = await self.run([{
                   "role": "user",
                   "content": f"Execute: {command}"
               }])
               return response
           except Exception as e:
               # Fallback or alternative approach
               safer_command = self.make_command_safer(command)
               return await self.run([{
                   "role": "user",
                   "content": f"Execute safely: {safer_command}"
               }])

Command Validation
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class ValidatedShellToolSet(ShellToolSet):
       def validate_command(self, command: str) -> bool:
           """Validate command before execution."""
           # Check against whitelist
           if not any(command.startswith(allowed) for allowed in self.allowed_commands):
               return False
           
           # Check for dangerous patterns
           dangerous_patterns = ["rm -rf /", ":(){ :|:& };:", "dd if="]
           if any(pattern in command for pattern in dangerous_patterns):
               return False
           
           return True

Integration Patterns
--------------------

With File Tools
~~~~~~~~~~~~~~~

.. code-block:: python

   # Combine shell and file operations
   file_processor = Agent(
       name="file_processor",
       instructions="Process files using shell and Python tools.",
       tools=[read_file, write_file]
   )
   await file_processor.remote_toolset(shell_tools.service_id)
   
   # Agent can:
   # 1. Find files with shell
   # 2. Read content with file tools
   # 3. Process data
   # 4. Write results

With Python Tools
~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Shell + Python workflow
   data_pipeline_agent = Agent(
       name="pipeline_agent",
       instructions="Use shell for file operations and Python for analysis."
   )
   
   # Workflow:
   # Shell: find ./data -name "*.csv" | head -10
   # Python: pandas.read_csv() for each file
   # Python: analyze and combine data
   # Shell: mv processed_*.csv ./output/

Best Practices
--------------

1. **Whitelist Commands**: Only allow necessary commands
2. **Validate Input**: Check commands before execution
3. **Limit Resources**: Set timeouts and output limits
4. **Avoid Pipes**: Be cautious with command chaining
5. **Log Operations**: Track all executed commands
6. **Test Thoroughly**: Test in safe environments first

Common Patterns
---------------

Batch Operations
~~~~~~~~~~~~~~~~

.. code-block:: python

   batch_agent = Agent(
       name="batch_processor",
       instructions="Perform batch file operations efficiently."
   )
   
   # Agent implements:
   # for file in $(find . -name "*.txt"); do
   #     echo "Processing $file"
   #     # Process each file
   # done

System Maintenance
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   maintenance_agent = Agent(
       name="maintenance",
       instructions="""Perform system maintenance tasks:
       - Clean temporary files
       - Archive old logs
       - Check disk space
       - Monitor services"""
   )

Automation Scripts
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   automation_agent = Agent(
       name="automator",
       instructions="Create automation scripts for repetitive tasks."
   )
   
   # Agent creates scripts for:
   # - Daily backups
   # - Log rotation
   # - Service health checks
   # - Report generation

Performance Tips
----------------

- Use built-in commands over external scripts
- Minimize subprocess spawning
- Cache command outputs when appropriate
- Use efficient text processing (awk/sed vs multiple greps)
- Batch operations to reduce overhead

Limitations
-----------

- No interactive commands (like vi, less)
- No background processes
- Limited to allowed commands
- No network operations (unless explicitly allowed)
- No system modifications