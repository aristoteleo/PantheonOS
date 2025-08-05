R Interpreter
=============

The R Interpreter toolset provides agents with the ability to execute R code for statistical analysis, data visualization, and scientific computing in a secure environment.

Overview
--------

Key features:

* **Statistical Computing**: Full R environment for statistics
* **Advanced Visualization**: ggplot2 and other R plotting libraries
* **Package Ecosystem**: Access to CRAN packages
* **Data Manipulation**: tidyverse tools for data wrangling
* **Scientific Computing**: Specialized statistical methods

Basic Usage
-----------

Setting Up R Interpreter
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from pantheon.toolsets.r import RInterpreterToolSet
   from pantheon.toolsets.utils.toolset import run_toolsets
   from pantheon.agent import Agent
   
   async def create_r_agent():
       # Create R interpreter toolset
       r_tools = RInterpreterToolSet("r_interpreter")
       
       # Run toolset service
       async with run_toolsets([r_tools], log_level="WARNING"):
           # Create agent with R capabilities
           agent = Agent(
               name="statistician",
               instructions="You are a statistician who performs analysis using R.",
               model="gpt-4o"
           )
           
           # Connect to R toolset
           await agent.remote_toolset(r_tools.service_id)
           
           # Agent can now execute R code
           await agent.chat()

Statistical Analysis
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Basic statistics
   response = await agent.run([{
       "role": "user",
       "content": "Perform a t-test on two groups: A=[5,7,9,11,13] and B=[8,10,12,14,16]"
   }])
   
   # Agent executes:
   # group_a <- c(5, 7, 9, 11, 13)
   # group_b <- c(8, 10, 12, 14, 16)
   # 
   # # Perform t-test
   # result <- t.test(group_a, group_b)
   # print(result)
   # 
   # # Effect size
   # library(effsize)
   # cohen.d(group_a, group_b)

Advanced Features
-----------------

Data Visualization with ggplot2
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   viz_agent = Agent(
       name="viz_expert",
       instructions="Create beautiful visualizations using ggplot2.",
       model="gpt-4o"
   )
   
   response = await viz_agent.run([{
       "role": "user",
       "content": "Create a scatter plot with regression line for height vs weight data"
   }])
   
   # Agent creates:
   # library(ggplot2)
   # 
   # # Sample data
   # data <- data.frame(
   #     height = rnorm(100, mean=170, sd=10),
   #     weight = rnorm(100, mean=70, sd=15)
   # )
   # data$weight <- data$weight + 0.5 * (data$height - 170)
   # 
   # # Create plot
   # p <- ggplot(data, aes(x=height, y=weight)) +
   #     geom_point(alpha=0.6, color="blue") +
   #     geom_smooth(method="lm", color="red") +
   #     labs(title="Height vs Weight Relationship",
   #          x="Height (cm)", y="Weight (kg)") +
   #     theme_minimal()
   # 
   # print(p)

Time Series Analysis
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   ts_agent = Agent(
       name="time_series_expert",
       instructions="Analyze time series data using R's specialized packages."
   )
   
   # Agent can use:
   # library(forecast)
   # library(tseries)
   # 
   # # Load and analyze time series
   # ts_data <- ts(data, frequency=12, start=c(2020,1))
   # 
   # # Decomposition
   # decomp <- stl(ts_data, s.window="periodic")
   # plot(decomp)
   # 
   # # Forecasting
   # model <- auto.arima(ts_data)
   # forecast_result <- forecast(model, h=12)
   # plot(forecast_result)

Machine Learning in R
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   ml_r_agent = Agent(
       name="r_ml_expert",
       instructions="Build machine learning models using R's ML packages."
   )
   
   # Agent implements:
   # library(caret)
   # library(randomForest)
   # 
   # # Prepare data
   # set.seed(123)
   # trainIndex <- createDataPartition(data$target, p=0.8, list=FALSE)
   # trainData <- data[trainIndex,]
   # testData <- data[-trainIndex,]
   # 
   # # Train model
   # rf_model <- randomForest(target ~ ., data=trainData, ntree=100)
   # 
   # # Evaluate
   # predictions <- predict(rf_model, testData)
   # confusionMatrix(predictions, testData$target)

Available Packages
------------------

Common R packages available:

- **Base R**: stats, graphics, utils
- **Tidyverse**: dplyr, ggplot2, tidyr, readr
- **Statistics**: lme4, survival, MASS
- **Machine Learning**: caret, randomForest, xgboost
- **Time Series**: forecast, zoo, xts
- **Visualization**: ggplot2, plotly, lattice

Working with Data
-----------------

Data Import/Export
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   data_agent = Agent(
       name="r_data_handler",
       instructions="Handle data import/export in R."
   )
   
   # Agent can:
   # # Read CSV
   # data <- read.csv("data.csv")
   # 
   # # Read Excel
   # library(readxl)
   # excel_data <- read_excel("data.xlsx")
   # 
   # # Save RDS
   # saveRDS(processed_data, "processed.rds")
   # 
   # # Export to CSV
   # write.csv(results, "results.csv", row.names=FALSE)

Data Manipulation
~~~~~~~~~~~~~~~~~

.. code-block:: python

   # Using tidyverse
   response = await agent.run([{
       "role": "user",
       "content": "Clean and transform this messy dataset using dplyr"
   }])
   
   # Agent uses:
   # library(dplyr)
   # library(tidyr)
   # 
   # cleaned_data <- data %>%
   #     filter(!is.na(important_column)) %>%
   #     mutate(new_column = column1 + column2) %>%
   #     group_by(category) %>%
   #     summarise(
   #         mean_value = mean(value, na.rm=TRUE),
   #         count = n()
   #     ) %>%
   #     arrange(desc(mean_value))

Statistical Modeling
--------------------

Linear Models
~~~~~~~~~~~~~

.. code-block:: python

   model_agent = Agent(
       name="r_modeler",
       instructions="Build and interpret statistical models in R."
   )
   
   # Agent builds:
   # # Multiple regression
   # model <- lm(y ~ x1 + x2 + x3, data=df)
   # summary(model)
   # 
   # # Diagnostics
   # par(mfrow=c(2,2))
   # plot(model)
   # 
   # # ANOVA
   # anova(model)

Mixed Effects Models
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   # For hierarchical data
   # library(lme4)
   # 
   # mixed_model <- lmer(y ~ x1 + x2 + (1|group), data=df)
   # summary(mixed_model)
   # 
   # # Model comparison
   # null_model <- lmer(y ~ 1 + (1|group), data=df)
   # anova(null_model, mixed_model)

Specialized Analysis
--------------------

Survival Analysis
~~~~~~~~~~~~~~~~~

.. code-block:: python

   survival_agent = Agent(
       name="survival_analyst",
       instructions="Perform survival analysis using R."
   )
   
   # Agent implements:
   # library(survival)
   # library(survminer)
   # 
   # # Kaplan-Meier
   # km_fit <- survfit(Surv(time, event) ~ treatment, data=survival_data)
   # ggsurvplot(km_fit, data=survival_data, pval=TRUE)
   # 
   # # Cox regression
   # cox_model <- coxph(Surv(time, event) ~ age + treatment, data=survival_data)
   # summary(cox_model)

Bioinformatics
~~~~~~~~~~~~~~~

.. code-block:: python

   bio_agent = Agent(
       name="bioinformatics_expert",
       instructions="Analyze biological data using Bioconductor packages."
   )
   
   # Agent can use:
   # if (!requireNamespace("BiocManager", quietly = TRUE))
   #     install.packages("BiocManager")
   # 
   # library(DESeq2)
   # library(edgeR)
   # 
   # # Differential expression analysis
   # dds <- DESeqDataSetFromMatrix(countData, colData, design = ~ condition)
   # dds <- DESeq(dds)
   # results <- results(dds)

Best Practices
--------------

1. **Reproducibility**: Always set random seeds
2. **Documentation**: Use comments and markdown
3. **Visualization**: Create informative plots
4. **Validation**: Check assumptions of statistical tests
5. **Error Handling**: Use tryCatch for robust code

Common Patterns
---------------

Report Generation
~~~~~~~~~~~~~~~~~

.. code-block:: python

   report_r_agent = Agent(
       name="r_reporter",
       instructions="""Generate statistical reports with:
       - Descriptive statistics
       - Hypothesis tests
       - Visualizations
       - Interpretations"""
   )

Interactive Analysis
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   interactive_r_agent = Agent(
       name="r_interactive",
       instructions="Perform exploratory data analysis interactively."
   )
   
   # Agent guides through:
   # 1. Data exploration
   # 2. Assumption checking
   # 3. Model selection
   # 4. Result interpretation

Performance Optimization
------------------------

- Use vectorized operations
- Leverage data.table for large datasets
- Pre-allocate memory for loops
- Use parallel processing with foreach
- Profile code with Rprof()

Integration with Python
-----------------------

.. code-block:: python

   # Agent can bridge Python and R
   bridge_agent = Agent(
       name="py_r_bridge",
       instructions="Use both Python and R for analysis."
   )
   
   # Can transfer data between environments
   # Python: save data as CSV
   # R: read CSV and analyze
   # Python: read R results