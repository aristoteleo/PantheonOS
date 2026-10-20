<!--
  SKILLS.md - User-Defined Skills
  
  This file is for defining simple, quick skills (rules, strategies, patterns).
  For complex workflows with detailed instructions, create a separate .md file
  with YAML front matter in this directory or subdirectories.
  
  ============================================================================
  USAGE GUIDE
  ============================================================================
  
  ## Simple Skills (in this file)
  
  Add rules as list items under any "## Section Name" header:
  
    ## User Rules          → Highest priority, MUST FOLLOW rules
    - Always use uv for Python project management
  
    ## Strategies          → Learned approaches
    - Use type hints in all Python functions
  
    ## Patterns            → Common patterns
    - Follow the repository pattern for data access
  
    ## My Custom Section   → Custom sections are supported!
    - Any section name works (becomes "my_custom_section")
  
  ## File-Based Skills (separate .md files)
  
  For detailed workflows, create a .md file with YAML front matter:
  
    ---
    id: my-workflow           # Required: unique ID, used for /my-workflow trigger
    description: A workflow   # Required: short description shown in skillbook
    section: workflows        # Optional: user_rules | strategies | patterns | workflows | custom
    tags: [example, demo]     # Optional: categorization tags
    ---
    
    # Detailed Content
    
    Your full workflow instructions, code examples, etc.
  
  The agent can load these skills by typing /skill-id (e.g., /my-workflow).
  
  ============================================================================
-->

## User Rules

<!-- Add your personal rules here. These have highest priority. -->
<!-- Example: -->
<!-- - Always activate .venv before running Python scripts -->
<!-- - Use uv for Python dependency management -->


## Strategies

<!-- Add your preferred strategies here. -->
<!-- Example: -->
<!-- - Use polars for dataframes larger than 1GB -->
<!-- - Prefer async I/O for network operations -->

## Patterns

<!-- Add your common patterns here. -->
<!-- Example: -->
<!-- - Use dependency injection for testability -->
<!-- - Follow the factory pattern for object creation -->
