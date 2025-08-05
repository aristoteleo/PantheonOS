File Editor / Filesystem Access
================================

The File Editor toolset provides agents with safe, controlled access to the filesystem for reading, writing, and manipulating files. This enables data persistence, report generation, and file-based workflows.

Overview
--------

Key features:

* **File Operations**: Read, write, append, and delete files
* **Directory Management**: Create and navigate directories
* **Safe Access**: Sandboxed to specific directories
* **Format Support**: Handle various file formats
* **Atomic Operations**: Ensure data integrity

Basic Usage
-----------

File Reading and Writing
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolsets.filesystem import read_file, write_file, list_directory
   from pantheon.agent import Agent
   
   # Create agent with file access
   file_agent = Agent(
       name="file_manager",
       instructions="Manage files and directories safely.",
       model="gpt-4o-mini",
       tools=[read_file, write_file, list_directory]
   )
   
   # Read a file
   response = await file_agent.run([{
       "role": "user",
       "content": "Read the contents of config.yaml"
   }])
   
   # Write a file
   response = await file_agent.run([{
       "role": "user",
       "content": "Create a report.md file with the analysis results"
   }])

Directory Operations
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # List directory contents
   response = await file_agent.run([{
       "role": "user",
       "content": "Show all Python files in the src directory"
   }])
   
   # Agent executes:
   # files = list_directory("src", pattern="*.py")

Advanced Features
-----------------

File Manipulation
~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolsets.filesystem import (
       read_file, 
       write_file, 
       append_file,
       delete_file,
       move_file,
       copy_file
   )
   
   editor_agent = Agent(
       name="file_editor",
       instructions="Edit and manipulate files with precision.",
       tools=[read_file, write_file, append_file, move_file, copy_file]
   )
   
   # Append to log file
   await editor_agent.run([{
       "role": "user",
       "content": "Add timestamp entry to activity.log"
   }])
   
   # Reorganize files
   await editor_agent.run([{
       "role": "user",
       "content": "Move all .txt files from temp/ to archive/"
   }])

Format-Specific Operations
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # JSON manipulation
   json_agent = Agent(
       name="json_handler",
       instructions="Work with JSON files efficiently."
   )
   
   # Agent can:
   # 1. Read JSON file
   # 2. Parse and modify structure
   # 3. Write back with proper formatting
   
   # CSV processing
   csv_agent = Agent(
       name="csv_processor",
       instructions="Process CSV files and tabular data."
   )
   
   # YAML configuration
   yaml_agent = Agent(
       name="config_manager",
       instructions="Manage YAML configuration files."
   )

Safe File Access
~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolsets.filesystem import SafeFileSystem
   
   # Configure safe filesystem
   safe_fs = SafeFileSystem(
       root_directory="/workspace",
       allowed_paths=[
           "/workspace/data",
           "/workspace/output",
           "/workspace/temp"
       ],
       blocked_patterns=[
           "*.exe",
           "*.sh",
           ".*"  # Hidden files
       ],
       max_file_size=100 * 1024 * 1024  # 100MB limit
   )
   
   secure_agent = Agent(
       name="secure_file_agent",
       instructions="Work with files in a secure environment.",
       tools=safe_fs.get_tools()
   )

Common Patterns
---------------

Report Generation
~~~~~~~~~~~~~~~~~

.. code-block:: python

   report_generator = Agent(
       name="report_writer",
       instructions="""Generate comprehensive reports:
       1. Gather data from multiple sources
       2. Create structured markdown/HTML
       3. Include tables and formatting
       4. Save with timestamp""",
       tools=[read_file, write_file]
   )
   
   # Generate report
   await report_generator.run([{
       "role": "user",
       "content": "Create a monthly analysis report from the data files"
   }])
   
   # Agent creates:
   # # Monthly Report - January 2024
   # 
   # ## Executive Summary
   # ...
   # 
   # ## Data Analysis
   # | Metric | Value | Change |
   # |--------|-------|--------|
   # | Sales  | $100K | +15%   |

Data Pipeline
~~~~~~~~~~~~~

.. code-block:: python

   pipeline_agent = Agent(
       name="data_pipeline",
       instructions="""Process data files through pipeline:
       1. Read input files
       2. Transform and clean data
       3. Merge multiple sources
       4. Write output files"""
   )
   
   # Pipeline workflow
   # 1. Read: data/raw/*.csv
   # 2. Process: clean, transform, merge
   # 3. Write: data/processed/combined.csv
   # 4. Log: pipeline.log

Configuration Management
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   config_agent = Agent(
       name="config_manager",
       instructions="""Manage application configurations:
       1. Read current config
       2. Validate changes
       3. Backup before modifying
       4. Apply updates safely"""
   )
   
   # Safe config update
   await config_agent.run([{
       "role": "user",
       "content": "Update database connection string in config.yaml"
   }])
   
   # Agent workflow:
   # 1. backup: cp config.yaml config.yaml.bak
   # 2. read: current_config = read_file("config.yaml")
   # 3. modify: update connection string
   # 4. validate: check yaml syntax
   # 5. write: write_file("config.yaml", new_config)

Advanced Operations
-------------------

Batch File Processing
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   batch_processor = Agent(
       name="batch_processor",
       instructions="Process multiple files efficiently."
   )
   
   async def process_batch(self, file_pattern: str, operation: str):
       """Process multiple files matching pattern."""
       files = list_directory(".", pattern=file_pattern)
       results = []
       
       for file in files:
           try:
               content = read_file(file)
               processed = await self.process_content(content, operation)
               output_file = f"processed_{file}"
               write_file(output_file, processed)
               results.append({"file": file, "status": "success"})
           except Exception as e:
               results.append({"file": file, "status": "error", "error": str(e)})
       
       return results

File Watching
~~~~~~~~~~~~~

.. code-block:: python

   monitor_agent = Agent(
       name="file_monitor",
       instructions="""Monitor files for changes:
       1. Track file modifications
       2. Detect new files
       3. Process changes
       4. Log activity"""
   )
   
   # Monitor implementation
   async def watch_directory(self, path: str, callback):
       """Watch directory for changes."""
       last_state = self.get_directory_state(path)
       
       while True:
           current_state = self.get_directory_state(path)
           changes = self.detect_changes(last_state, current_state)
           
           if changes:
               await callback(changes)
           
           last_state = current_state
           await asyncio.sleep(5)  # Check every 5 seconds

Atomic Operations
~~~~~~~~~~~~~~~~~

.. code-block:: python

   class AtomicFileAgent(Agent):
       async def atomic_write(self, filepath: str, content: str):
           """Write file atomically to prevent corruption."""
           temp_file = f"{filepath}.tmp"
           
           try:
               # Write to temporary file
               write_file(temp_file, content)
               
               # Verify write succeeded
               if read_file(temp_file) == content:
                   # Atomic rename
                   move_file(temp_file, filepath)
               else:
                   raise ValueError("Write verification failed")
           finally:
               # Cleanup
               if os.path.exists(temp_file):
                   delete_file(temp_file)

Error Handling
--------------

Graceful Failures
~~~~~~~~~~~~~~~~~

.. code-block:: python

   class RobustFileAgent(Agent):
       async def safe_read(self, filepath: str, default=None):
           """Read file with fallback."""
           try:
               return read_file(filepath)
           except FileNotFoundError:
               if default is not None:
                   return default
               # Try alternative locations
               alternatives = [
                   f"backup/{filepath}",
                   f"archive/{filepath}"
               ]
               for alt in alternatives:
                   try:
                       return read_file(alt)
                   except:
                       continue
               raise

Permission Handling
~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class PermissionAwareAgent(Agent):
       async def write_with_fallback(self, filepath: str, content: str):
           """Handle permission errors gracefully."""
           try:
               write_file(filepath, content)
           except PermissionError:
               # Try alternative location
               alt_path = f"temp/{os.path.basename(filepath)}"
               write_file(alt_path, content)
               print(f"Saved to alternative location: {alt_path}")

Best Practices
--------------

1. **Path Validation**: Always validate file paths
2. **Backup Important Files**: Create backups before modifications
3. **Use Atomic Operations**: Prevent partial writes
4. **Handle Encodings**: Specify encoding for text files
5. **Resource Cleanup**: Close files and clean temporary files
6. **Security**: Validate file contents and names

Performance Tips
----------------

File Caching
~~~~~~~~~~~~

.. code-block:: python

   class CachedFileAgent(Agent):
       def __init__(self):
           super().__init__()
           self.file_cache = {}
           
       async def read_cached(self, filepath: str):
           """Read file with caching."""
           if filepath in self.file_cache:
               cached_time, content = self.file_cache[filepath]
               if time.time() - cached_time < 300:  # 5 min cache
                   return content
           
           content = read_file(filepath)
           self.file_cache[filepath] = (time.time(), content)
           return content

Streaming Large Files
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   async def process_large_file(self, filepath: str, chunk_size: int = 8192):
       """Process large files in chunks."""
       with open(filepath, 'r') as f:
           while True:
               chunk = f.read(chunk_size)
               if not chunk:
                   break
               await self.process_chunk(chunk)

Integration Examples
--------------------

With Data Analysis
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # File + Python analysis
   analyst = Agent(
       name="file_analyst",
       instructions="Read files and analyze with Python.",
       tools=[read_file, write_file]
   )
   await analyst.remote_toolset(python_tools.service_id)

With Web Data
~~~~~~~~~~~~~

.. code-block:: python

   # Web scraping to files
   scraper = Agent(
       name="web_to_file",
       instructions="Fetch web data and save to files.",
       tools=[web_crawl, write_file]
   )