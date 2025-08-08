Single-Cell Analysis CLI Bot
=============================

This example demonstrates how to build a powerful single-cell/spatial genomics analysis CLI assistant using Pantheon Agents. The bot can perform upstream analysis tasks using command-line tools with the help of a custom RAG (Retrieval-Augmented Generation) database containing tool documentation.

Overview
--------

The Single-Cell Analysis CLI Bot combines multiple toolsets to create an intelligent assistant that can:

- Search a vector database for single-cell analysis tool documentation
- Execute shell commands to perform analysis
- Search the web for additional information when needed
- Provide step-by-step analysis plans with checkboxes
- Summarize analysis results

Prerequisites
-------------

Before running this example, you need to install the required packages and set up environment variables.

Installation
~~~~~~~~~~~~

First, install the Pantheon Agents and Pantheon Toolsets packages by following the **Install from Source** instructions in the :doc:`../installation` guide.
Then, change the working directory to the root of the pantheon-agents repository:

.. code-block:: bash

   cd pantheon-agents


The example code is in the ``examples/sc_analysis_cli`` directory.

Environment Variables
~~~~~~~~~~~~~~~~~~~~~

Set up the required API keys:

.. code-block:: bash

   export OPENAI_API_KEY="your-openai-api-key"
   export SCRAPER_API_KEY="your-scraper-api-key"

Building the RAG Database
-------------------------

The bot uses a custom RAG database containing documentation for single-cell analysis tools. Build the database using the provided YAML configuration:

.. code-block:: bash

   python -m pantheon.toolsets.utils.rag build \
       examples/sc_analysis_cli/sc_cli_tools.yaml \
       tmp/sc_cli_tools_rag

This command will:

1. Read the ``sc_cli_tools.yaml`` configuration file
2. Download and process documentation from specified sources (kallisto-bustools, STAR, etc.)
3. Create a vector database in the ``tmp/sc_cli_tools_rag`` directory

The YAML configuration includes:

- **kallisto-bustools**: A workflow for pre-processing single-cell RNA-seq data
- **STAR**: RNA-seq aligner documentation (PDF format)

Running the Bot
---------------

Once the RAG database is built, run the bot:

.. code-block:: bash

   python examples/sc_analysis_cli/main.py tmp/sc_cli_tools_rag/single-cell-cli-tools

The bot will start an interactive chat session where you can ask questions about single-cell analysis and request it to perform analysis tasks.

.. image:: ../_static/sc_cli_bot.png
   :alt: Single-Cell Analysis CLI Bot Interface
   :align: center
   :width: 100%

The interface shows the Pantheon REPL with the configured agent ready to assist with single-cell analysis tasks.

How It Works
------------

The bot integrates three powerful toolsets:

1. **VectorRAGToolSet**: Searches the custom-built documentation database for relevant information about single-cell analysis tools
2. **ShellToolSet**: Executes shell commands to run actual analysis tools
3. **ScraperToolSet**: Searches the web for additional information when the local database doesn't contain what's needed

Example Usage
-------------

Here's an example interaction with the bot:

.. code-block:: text

   User: How do I process single-cell RNA-seq data using kallisto and bustools?
   
   Bot: I'll help you process single-cell RNA-seq data using kallisto and bustools. Let me first search for information about this workflow.
   
   [✓] Search vector database for kallisto-bustools documentation
   [✓] Create analysis plan
   [ ] Download reference transcriptome
   [ ] Build kallisto index
   [ ] Run kallisto bus
   [ ] Process with bustools
   ...

The bot will provide detailed commands and explanations for each step of the analysis.

Customization
-------------

You can extend this example by:

1. **Adding more tools** to the ``sc_cli_tools.yaml`` configuration
2. **Modifying the bot instructions** in ``main.py`` to specialize for specific analysis types
3. **Adding additional toolsets** for enhanced functionality

Code Examples
-------------

Main Script (main.py)
~~~~~~~~~~~~~~~~~~~~~

Here's the complete ``main.py`` file that creates the Single-Cell Analysis CLI Bot:

.. code-block:: python

   import fire
   from pantheon.toolsets.scraper import ScraperToolSet
   from pantheon.toolsets.shell import ShellToolSet
   from pantheon.toolsets.vector_rag import VectorRAGToolSet
   from pantheon.agent import Agent


   async def main(path_to_rag_db: str):
       scraper_toolset = ScraperToolSet("scraper")
       shell_toolset = ShellToolSet("shell")
       vector_rag_toolset = VectorRAGToolSet(
           "vector_rag",
           db_path=path_to_rag_db,
       )

       instructions = """
       You are a CLI assistant that can run perfrom the Single-Cell/Spatial genomics upstream analysis.
       You can run shell commands to perform the analysis.
       Given the user's input, you should first analyze the input and determine your analysis plan.
       Then, you should output the analysis plan with check boxes for each step.
       You can search the vector database to get the knowledge about the tools.
       If you didn't find the information you need, you can search the web,
       you can use google search or web crawl from the scraper toolset.
       Then, you should run the analysis step by step with the shell tools.
       After all the analysis is done, you should output the analysis results and summarize the results.
       """

       agent = Agent(
           "general_bot",
           instructions,
           model="gpt-4.1-mini",
       )
       agent.toolset(scraper_toolset)
       agent.toolset(shell_toolset)
       agent.toolset(vector_rag_toolset)

       await agent.chat()


   if __name__ == "__main__":
       fire.Fire(main)

RAG Configuration (sc_cli_tools.yaml)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``sc_cli_tools.yaml`` file defines the documentation sources for building the RAG database:

.. code-block:: yaml

   single-cell-cli-tools:
     description: Vector database of single-cell/spatial genomics command line tools documentation and tutorials.
     type: vector_db
     parameters:
       embedding_model: text-embedding-3-large
       chunk_size: 4000
       chunk_overlap: 200
     items:
       kallisto-bustools:
         type: package documentation
         url: https://www.kallistobus.tools/
         description: "kallisto | bustools is a workflow for pre-processing single-cell RNA-seq data. Pre-processing single-cell RNA-seq involves: (1) association of reads with their cells of origin, (2) collapsing of reads according to unique molecular identifiers (UMIs), and (3) generation of gene or feature counts from the reads to generate a cell x gene matrix."
       star:
         type: pdf
         url: https://raw.githubusercontent.com/alexdobin/STAR/master/doc/STARmanual.pdf
         description: RNA-seq aligner

Extending the RAG Database
--------------------------

You can easily extend the RAG database by adding more items to the ``sc_cli_tools.yaml`` file. Each item can be:

- **Web documentation**: Add tools with online documentation
- **PDF files**: Include tool manuals in PDF format
- **Local files**: Reference local documentation files

To add a new tool, simply add a new item under the ``items`` section:

.. code-block:: yaml

   items:
     # ... existing items ...
     cellranger:
       type: package documentation
       url: https://support.10xgenomics.com/single-cell-gene-expression/software/pipelines/latest/what-is-cell-ranger
       description: "10x Genomics Cell Ranger for single-cell RNA-seq analysis"
     
     seurat:
       type: package documentation
       url: https://satijalab.org/seurat/
       description: "R toolkit for single-cell genomics analysis"
     
     scanpy:
       type: package documentation
       url: https://scanpy.readthedocs.io/
       description: "Python-based single-cell analysis toolkit"

After modifying the YAML file, rebuild the RAG database:

.. code-block:: bash

   python -m pantheon.toolsets.utils.rag build \
       examples/sc_analysis_cli/sc_cli_tools.yaml \
       tmp/sc_cli_tools_rag

The bot will then have access to the newly added documentation.

Tips for Best Results
---------------------

1. **Be specific** in your queries - mention the exact tools or analysis types you need
2. **Provide context** about your data type (e.g., 10x Genomics, Smart-seq2)
3. **Ask for step-by-step plans** before executing complex analyses
4. **Review commands** before the bot executes them in your environment

See Also
--------

- :doc:`vector_rag` - Learn more about the Vector RAG toolset
- :doc:`shell_bot` - Example of shell command execution
- :doc:`../toolsets/builtin_toolsets` - Overview of available toolsets