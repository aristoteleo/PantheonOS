Python Interpreter
==================

The Python Interpreter toolset provides agents with the ability to execute Python code in isolated interpreter sessions. It supports both stateless execution and persistent sessions with state management.

Overview
--------

The ``PythonInterpreterToolSet`` provides three main tools:

- **run_code**: Execute code in a new interpreter (stateless)
- **run_code_in_interpreter**: Execute code in a specific interpreter session
- **new_interpreter/delete_interpreter**: Manage interpreter lifecycle

Key Features:

- **Process Isolation**: Each interpreter runs in a separate process
- **Session Management**: Maintain state across multiple executions
- **Matplotlib Support**: Automatic plot capture and base64 encoding
- **Error Handling**: Graceful error recovery with full tracebacks
- **Echo Mode**: Expressions are automatically printed
- **Working Directory**: Configurable working directory for file operations

Basic Usage
-----------

Setting Up Python Interpreter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolsets.python import PythonInterpreterToolSet
   from pantheon.toolsets.utils.toolset import run_toolsets
   from pantheon.agent import Agent
   
   async def create_python_agent():
       # Create Python interpreter toolset
       python_tools = PythonInterpreterToolSet("python_interpreter")
       
       # Run toolset service
       async with run_toolsets([python_tools], log_level="WARNING"):
           # Create agent with Python capabilities
           agent = Agent(
               name="data_scientist",
               instructions="You are a data scientist who can analyze data with Python.",
               model="gpt-4o"
           )
           
           # Connect to Python toolset
           await agent.remote_toolset(python_tools.service_id)
           
           # Agent can now execute Python code
           await agent.chat()

Simple Code Execution
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Agent can execute Python code
   response = await agent.run([{
       "role": "user",
       "content": "Calculate the mean and standard deviation of [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]"
   }])
   
   # Agent will execute something like:
   # import numpy as np
   # data = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
   # mean = np.mean(data)
   # std = np.std(data)
   # print(f"Mean: {mean}, Std Dev: {std}")

Advanced Features
-----------------

Data Analysis
~~~~~~~~~~~~~

The interpreter includes common data science libraries:

.. code-block:: python

   # Example: Agent analyzing CSV data
   agent_instruction = """
   You are a data analyst. When given data:
   1. Load and explore the data
   2. Perform statistical analysis
   3. Create visualizations
   4. Provide insights
   """
   
   analyst = Agent(
       name="analyst",
       instructions=agent_instruction,
       model="gpt-4o"
   )
   await analyst.remote_toolset(python_tools.service_id)
   
   # Agent can now:
   # - Use pandas for data manipulation
   # - Use numpy for numerical computation
   # - Use matplotlib/seaborn for visualization
   # - Use scipy for statistical analysis

Visualization
~~~~~~~~~~~~~

Generate plots and charts:

.. code-block:: python

   response = await agent.run([{
       "role": "user",
       "content": """Create a visualization showing the relationship between 
       x = [1, 2, 3, 4, 5] and y = [2, 4, 6, 8, 10]"""
   }])
   
   # Agent executes:
   # import matplotlib.pyplot as plt
   # x = [1, 2, 3, 4, 5]
   # y = [2, 4, 6, 8, 10]
   # plt.figure(figsize=(8, 6))
   # plt.plot(x, y, 'b-o')
   # plt.xlabel('X values')
   # plt.ylabel('Y values')
   # plt.title('Linear Relationship')
   # plt.grid(True)
   # plt.show()

File Operations
~~~~~~~~~~~~~~~

Work with files in the sandbox:

.. code-block:: python

   # Writing files
   response = await agent.run([{
       "role": "user",
       "content": "Create a CSV file with sample sales data"
   }])
   
   # Agent executes:
   # import pandas as pd
   # import random
   # 
   # data = {
   #     'Date': pd.date_range('2024-01-01', periods=30),
   #     'Sales': [random.randint(100, 1000) for _ in range(30)],
   #     'Region': random.choices(['North', 'South', 'East', 'West'], k=30)
   # }
   # df = pd.DataFrame(data)
   # df.to_csv('sales_data.csv', index=False)
   # print("Sales data saved to sales_data.csv")

Session Management
------------------

Persistent State
~~~~~~~~~~~~~~~~

The interpreter maintains state between executions:

.. code-block:: python

   # First execution
   await agent.run([{
       "role": "user",
       "content": "Create a function to calculate compound interest"
   }])
   
   # Agent defines:
   # def compound_interest(principal, rate, time, n=12):
   #     amount = principal * (1 + rate/n)**(n*time)
   #     return amount
   
   # Second execution - function is still available
   await agent.run([{
       "role": "user",
       "content": "Use the compound interest function to calculate returns on $1000 at 5% for 10 years"
   }])

Session Reset
~~~~~~~~~~~~~

Reset the interpreter state when needed:

.. code-block:: python

   class PythonAgent(Agent):
       async def reset_python_session(self):
           """Reset Python interpreter state."""
           await self.run([{
               "role": "user",
               "content": "Execute: globals().clear()"
           }])

Available Libraries
-------------------

Standard libraries included:

- **Data Analysis**: pandas, numpy, scipy
- **Visualization**: matplotlib, seaborn, plotly
- **Machine Learning**: scikit-learn, statsmodels
- **File Formats**: csv, json, pickle, h5py
- **Utilities**: datetime, collections, itertools

.. code-block:: python

   # Example: Machine Learning workflow
   ml_agent = Agent(
       name="ml_engineer",
       instructions="""You are a machine learning engineer. 
       Use scikit-learn to build and evaluate models."""
   )
   
   response = await ml_agent.run([{
       "role": "user",
       "content": "Create a simple linear regression model with sample data"
   }])
   
   # Agent implements:
   # from sklearn.model_selection import train_test_split
   # from sklearn.linear_model import LinearRegression
   # from sklearn.metrics import r2_score, mean_squared_error
   # import numpy as np
   # 
   # # Generate sample data
   # X = np.random.rand(100, 1) * 10
   # y = 2 * X + 1 + np.random.randn(100, 1) * 2
   # 
   # # Split and train
   # X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2)
   # model = LinearRegression()
   # model.fit(X_train, y_train)
   # 
   # # Evaluate
   # predictions = model.predict(X_test)
   # r2 = r2_score(y_test, predictions)
   # print(f"R² Score: {r2}")

Error Handling
--------------

Graceful Error Recovery
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   class RobustPythonAgent(Agent):
       async def execute_with_fallback(self, code: str):
           """Execute code with error handling."""
           try:
               response = await self.run([{
                   "role": "user",
                   "content": f"Execute this Python code: {code}"
               }])
               return response
           except Exception as e:
               # Fallback to simpler approach
               fallback_response = await self.run([{
                   "role": "user",
                   "content": f"The code failed with {e}. Try a simpler approach."
               }])
               return fallback_response

Security Considerations
-----------------------

The Python interpreter runs in a sandboxed environment with:

- **Resource Limits**: CPU and memory usage caps
- **Network Isolation**: No external network access
- **File System Restrictions**: Limited to sandbox directory
- **Import Restrictions**: Some modules are blocked
- **Execution Timeout**: Long-running code is terminated

Best Practices
--------------

1. **Clear Instructions**: Guide agents on coding style and approach
2. **Incremental Development**: Build complex solutions step by step
3. **Error Handling**: Implement try-except blocks for robustness
4. **Documentation**: Encourage agents to comment code
5. **Testing**: Verify outputs with test cases

Common Patterns
---------------

Data Processing Pipeline
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   pipeline_agent = Agent(
       name="pipeline_builder",
       instructions="""Create data processing pipelines:
       1. Load data
       2. Clean and preprocess
       3. Analyze
       4. Visualize
       5. Save results"""
   )
   
   # Agent builds complete pipelines
   response = await pipeline_agent.run([{
       "role": "user",
       "content": "Build a pipeline to analyze customer churn data"
   }])

Interactive Analysis
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   interactive_agent = Agent(
       name="interactive_analyst",
       instructions="""Perform interactive data analysis.
       Show intermediate results and ask for guidance."""
   )
   
   # Agent provides step-by-step analysis
   await interactive_agent.chat()

Report Generation
~~~~~~~~~~~~~~~~~

.. code-block:: python

   report_agent = Agent(
       name="report_generator",
       instructions="""Generate comprehensive reports with:
       - Statistical summaries
       - Visualizations
       - Key insights
       - Recommendations"""
   )
   
   response = await report_agent.run([{
       "role": "user",
       "content": "Analyze this sales data and create a monthly report"
   }])

Performance Tips
----------------

- Use vectorized operations with numpy/pandas
- Leverage built-in functions over loops
- Cache intermediate results
- Use appropriate data structures
- Profile code for bottlenecks
