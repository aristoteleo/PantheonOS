Contribution Guide
==================

We welcome contributions to Pantheon! This guide will help you get started with contributing to the project.

Getting Started
---------------

Setting Up Development Environment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Fork the Repository**

   Visit https://github.com/aristoteleo/PantheonOS and click "Fork"

2. **Clone Your Fork**

   .. code-block:: bash

      git clone https://github.com/YOUR_USERNAME/pantheon-agents.git
      cd PantheonOS
      git remote add upstream https://github.com/aristoteleo/PantheonOS.git

3. **Create Development Environment**

   .. code-block:: bash

      # Create virtual environment
      python -m venv venv
      source venv/bin/activate  # On Windows: venv\Scripts\activate
      
      # Install in development mode
      pip install -e ".[dev]"
      
      # Install pre-commit hooks
      pre-commit install

4. **Verify Installation**

   .. code-block:: bash

      # Run tests
      pytest
      
      # Check code style
      black --check pantheon
      isort --check-only pantheon
      mypy pantheon

Development Workflow
--------------------

Creating a Feature Branch
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Update main branch
   git checkout main
   git pull upstream main
   
   # Create feature branch
   git checkout -b feature/your-feature-name
   
   # Make your changes
   # ...
   
   # Commit changes
   git add .
   git commit -m "feat: add new feature"
   
   # Push to your fork
   git push origin feature/your-feature-name

Commit Message Convention
~~~~~~~~~~~~~~~~~~~~~~~~~

We follow conventional commits:

- ``feat:`` New feature
- ``fix:`` Bug fix
- ``docs:`` Documentation changes
- ``style:`` Code style changes (formatting, etc)
- ``refactor:`` Code refactoring
- ``test:`` Test additions or changes
- ``chore:`` Build process or auxiliary tool changes

Examples:

.. code-block:: text

   feat: add support for Gemini models
   fix: handle timeout in remote toolsets
   docs: update team collaboration examples
   test: add tests for PantheonTeam agent mode

Code Standards
--------------

Python Style Guide
~~~~~~~~~~~~~~~~~~

We follow PEP 8 with these additions:

1. **Type Hints**: Use type hints for all public functions

   .. code-block:: python

      def process_data(
          data: List[Dict[str, Any]], 
          options: Optional[ProcessOptions] = None
      ) -> ProcessResult:
          """Process data with given options."""
          pass

2. **Docstrings**: Use Google-style docstrings

   .. code-block:: python

      def complex_function(param1: str, param2: int) -> dict:
          """Brief description of function.
          
          More detailed explanation if needed.
          
          Args:
              param1: Description of param1
              param2: Description of param2
              
          Returns:
              Description of return value
              
          Raises:
              ValueError: When invalid input provided
          """
          pass

3. **Async/Await**: Prefer async for I/O operations

   .. code-block:: python

      async def fetch_data(url: str) -> dict:
          async with aiohttp.ClientSession() as session:
              async with session.get(url) as response:
                  return await response.json()

Testing Guidelines
~~~~~~~~~~~~~~~~~~

1. **Test Structure**

   .. code-block:: python

      import pytest
      from pantheon.agent import Agent
      
      class TestAgent:
          @pytest.fixture
          def agent(self):
              return Agent("test", "Test agent")
          
          async def test_basic_functionality(self, agent):
              response = await agent.run([{"role": "user", "content": "test"}])
              assert response.messages
          
          async def test_error_handling(self, agent):
              with pytest.raises(ValueError):
                  await agent.run([])

2. **Test Coverage**: Aim for >80% coverage

   .. code-block:: bash

      pytest --cov=pantheon --cov-report=html

3. **Integration Tests**: Mark with appropriate decorators

   .. code-block:: python

      @pytest.mark.integration
      @pytest.mark.requires_api_key
      async def test_openai_integration():
          # Test that requires OpenAI API
          pass

Areas for Contribution
----------------------

High Priority Areas
~~~~~~~~~~~~~~~~~~~

1. **Documentation**
   - Add more examples
   - Improve API documentation
   - Create tutorials
   - Translate documentation

2. **Testing**
   - Increase test coverage
   - Add integration tests
   - Performance benchmarks
   - Edge case testing

3. **Features**
   - New team collaboration patterns
   - Additional toolsets
   - Enhanced memory systems
   - Better error handling

4. **Bug Fixes**
   - Check GitHub issues
   - Fix reported problems
   - Improve error messages

Example Contributions
~~~~~~~~~~~~~~~~~~~~~

**Adding a New Toolset:**

.. code-block:: python

   # pantheon/tools/new_tool.py
   from pantheon.toolsets.utils.toolset import Toolset
   
   class NewToolSet(Toolset):
       def __init__(self, name: str = "new_tools"):
           super().__init__(name)
           
       def get_tools(self):
           return [self.tool1, self.tool2]
           
       async def tool1(self, param: str) -> str:
           """Tool description."""
           # Implementation
           pass

**Adding a New Team Pattern:**

.. code-block:: python

   # apps/agent/pantheon/team/new_team.py
   from pantheon.team.base import BaseTeam
   
   class NewTeam(BaseTeam):
       def __init__(self, agents: List[Agent]):
           super().__init__(agents)
           
       async def run(self, messages: List[dict]) -> AgentResponse:
           # Team logic implementation
           pass

Submitting Changes
------------------

Pull Request Process
~~~~~~~~~~~~~~~~~~~~

1. **Ensure Quality**
   
   .. code-block:: bash
   
      # Format code
      black pantheon
      isort pantheon
      
      # Type check
      mypy pantheon
      
      # Run tests
      pytest
      
      # Check documentation
      cd docs
      make html

2. **Create Pull Request**
   
   - Use descriptive title
   - Fill out PR template
   - Link related issues
   - Add screenshots if UI changes

3. **PR Description Template**
   
   .. code-block:: text
   
      ## Description
      Brief description of changes
      
      ## Type of Change
      - [ ] Bug fix
      - [ ] New feature
      - [ ] Documentation update
      - [ ] Performance improvement
      
      ## Testing
      - [ ] Tests pass locally
      - [ ] Added new tests
      - [ ] Updated documentation
      
      ## Related Issues
      Fixes #123

Review Process
~~~~~~~~~~~~~~

1. **Automated Checks**: CI/CD runs tests and linting
2. **Code Review**: Maintainers review code
3. **Feedback**: Address review comments
4. **Merge**: Once approved, changes are merged

Community Guidelines
--------------------

Code of Conduct
~~~~~~~~~~~~~~~

- Be respectful and inclusive
- Welcome newcomers
- Provide constructive feedback
- Focus on what's best for the community

Getting Help
~~~~~~~~~~~~

- **Discord**: Join our community server
- **GitHub Discussions**: Ask questions
- **Issues**: Report bugs or request features
- **Email**: pantheon-dev@example.com

Recognition
~~~~~~~~~~~

Contributors are recognized in:
- README.md contributors section
- Release notes
- Annual contributor report

License
-------

By contributing, you agree that your contributions will be licensed under the same license as the project (MIT License).

Thank You!
----------

Thank you for contributing to Pantheon! Your efforts help make the project better for everyone.