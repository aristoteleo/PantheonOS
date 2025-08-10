# Universal Toolset Generation Prompt Template

## Template Structure

```markdown
# Toolset Generation Request: [TOOLSET_NAME]

## 1. CONTEXT AND PURPOSE
I need to create a new toolset called "[TOOLSET_NAME]" that will [PRIMARY_PURPOSE].

## 2. KNOWLEDGE SOURCES
- Architecture Reference: [PATH_TO_EXISTING_TOOLSET_EXAMPLES]
- Domain Documentation: [URLS_OR_PATHS_TO_DOMAIN_KNOWLEDGE]
- API/Tool Documentation: [OFFICIAL_DOCS_FOR_TOOLS_TO_WRAP]
- Usage Examples: [TUTORIALS_OR_WORKFLOW_EXAMPLES]

## 3. CORE REQUIREMENTS

### Tool Integration
- Primary tool/library: [MAIN_TOOL_NAME]
- Secondary dependencies: [LIST_OF_DEPENDENCIES]
- Installation method: [PACKAGE_MANAGER/DOWNLOAD_URL/BUILD_INSTRUCTIONS]

### Key Functionalities
1. [FUNCTION_1]: [DESCRIPTION]
2. [FUNCTION_2]: [DESCRIPTION]
3. [FUNCTION_3]: [DESCRIPTION]
...

### Smart Detection Features
- Auto-detect [WHAT_TO_DETECT] with priority levels
- Check existing installations before downloading
- Validate configurations and provide recommendations

## 4. IMPLEMENTATION STRATEGY

### Phase 1: Planning
- Review the existing toolset architecture at [REFERENCE_PATH]
- Identify patterns from similar toolsets
- Create a detailed implementation plan
- DO NOT generate files yet, only provide the plan

### Phase 2: Core Structure
Create the following components:
- `__init__.py`: Tool registration and exports
- `[main_module].py`: Core functionality implementation
- `utils.py`: Helper functions and validators (if needed)
- `prompts.py`: AI agent prompts for the toolset (if needed)

### Phase 3: Smart Features
Implement intelligent behaviors:
- Priority-based detection (like cellranger detection)
- Environment-aware path selection
- Automatic dependency resolution
- Progress tracking and status reporting

## 5. DESIGN PATTERNS TO FOLLOW

### Tool Methods Pattern
```python
@tool
def [action_name](self, [parameters]) -> Dict[str, Any]:
    """[Description of what this tool does]
    
    Args:
        [parameter]: [description]
    
    Returns:
        Dictionary with:
        - status: success/error/warning
        - data: [relevant data]
        - recommendation: [next steps]
        - message: [user-friendly message]
    """
```

### Smart Detection Pattern
```python
def check_[resource]_status(self, [parameters]) -> Dict[str, Any]:
    """Smart detection with priority levels"""
    # Priority 1: Check system-wide availability
    # Priority 2: Search common locations
    # Priority 3: Check workspace/cache
    # Priority 4: Recommend installation
```

### Installation Pattern
```python
def install_[tool](self, install_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Intelligent installation with path optimization"""
    # Check if already installed
    # Select optimal installation path
    # Download and setup
    # Verify installation
    # Update PATH if needed
```

## 6. TESTING AND VALIDATION

### Test Scenarios
1. Fresh installation scenario
2. Existing installation detection
3. Partial installation recovery
4. Error handling and recovery
5. Multi-platform compatibility

### Validation Checklist
- [ ] All tools have proper @tool decorators
- [ ] Methods return consistent dictionary structures
- [ ] Error handling provides actionable recommendations
- [ ] Progress is tracked and reported clearly
- [ ] Installation is idempotent and resumable

## 7. INTEGRATION REQUIREMENTS

### With Shell Tool
- Use shell.run_command() for system commands
- Handle heredoc syntax properly
- Provide clear command logging

### With Auto-Installer
- Integrate with universal_installer for missing dependencies
- Provide tool detection hints
- Support interactive installation prompts

### With TODO Management
- Generate appropriate todos for workflows
- Support task tracking and completion
- Provide smart task execution hints

## 8. DOCUMENTATION NEEDS

### README Structure
1. Overview and purpose
2. Installation instructions
3. Quick start guide
4. API reference
5. Common workflows
6. Troubleshooting

### Inline Documentation
- Comprehensive docstrings
- Type hints for all parameters
- Return value documentation
- Usage examples in comments

## 9. SPECIFIC INSTRUCTIONS

[ADD ANY DOMAIN-SPECIFIC REQUIREMENTS HERE]

## 10. DELIVERABLES

First, provide a detailed plan including:
1. File structure
2. Key methods and their purposes
3. Detection and installation strategy
4. Integration points with existing tools
5. Testing approach

DO NOT generate actual code files until the plan is reviewed and approved.
```

---

## Example Usage for Different Domains

### Example 1: Web Scraping Toolset
```markdown
# Toolset Generation Request: WebScraper

## 1. CONTEXT AND PURPOSE
I need to create a new toolset called "webscraper" that will provide intelligent web scraping capabilities with automatic rate limiting and content extraction.

## 2. KNOWLEDGE SOURCES
- Architecture Reference: /path/to/pantheon/toolsets/
- Domain Documentation: https://docs.python-requests.org/, https://beautiful-soup-4.readthedocs.io/
- Usage Examples: https://realpython.com/beautiful-soup-web-scraper-python/

[Continue with template...]
```

### Example 2: Database Management Toolset
```markdown
# Toolset Generation Request: DBManager

## 1. CONTEXT AND PURPOSE
I need to create a new toolset called "dbmanager" that will handle database connections, migrations, and query optimization across multiple database engines.

## 2. KNOWLEDGE SOURCES
- Architecture Reference: /path/to/pantheon/toolsets/
- Domain Documentation: https://www.sqlalchemy.org/, https://alembic.sqlalchemy.org/
- Usage Examples: https://docs.sqlalchemy.org/en/20/tutorial/

[Continue with template...]
```

### Example 3: Machine Learning Pipeline Toolset
```markdown
# Toolset Generation Request: MLPipeline

## 1. CONTEXT AND PURPOSE
I need to create a new toolset called "mlpipeline" that will automate machine learning workflows including data preprocessing, model training, and deployment.

## 2. KNOWLEDGE SOURCES
- Architecture Reference: /path/to/pantheon/toolsets/
- Domain Documentation: https://scikit-learn.org/, https://mlflow.org/
- Usage Examples: https://mlflow.org/docs/latest/tutorials-and-examples/

[Continue with template...]
```

---

## Key Success Factors

1. **Clear Separation of Concerns**: Each tool method should do one thing well
2. **Consistent Return Patterns**: All methods return similar dictionary structures
3. **Smart Detection**: Always check before installing/downloading
4. **Progressive Enhancement**: Start simple, add intelligence gradually
5. **Error Recovery**: Provide clear next steps when things fail
6. **User Feedback**: Show progress and status at each step
7. **Idempotent Operations**: Running twice should not cause problems
8. **Platform Awareness**: Adapt to different operating systems
9. **Resource Optimization**: Choose best paths for installations/downloads
10. **Integration First**: Design for seamless integration with existing tools

---

## Template Variables Reference

- `[TOOLSET_NAME]`: Name of the new toolset (e.g., "scatac", "webscraper", "dbmanager")
- `[PRIMARY_PURPOSE]`: Main goal of the toolset
- `[PATH_TO_EXISTING_TOOLSET_EXAMPLES]`: Reference implementations to follow
- `[URLS_OR_PATHS_TO_DOMAIN_KNOWLEDGE]`: Documentation for the domain
- `[MAIN_TOOL_NAME]`: Primary tool being wrapped (e.g., "cellranger-atac", "scrapy", "sqlalchemy")
- `[LIST_OF_DEPENDENCIES]`: Required libraries or tools
- `[WHAT_TO_DETECT]`: Resources to auto-detect (e.g., "software installations", "configuration files", "data directories")

Use this template by replacing the bracketed placeholders with your specific requirements.