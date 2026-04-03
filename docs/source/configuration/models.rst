Model Configuration
===================

Configure which LLM models your agents use.

Overview
--------

Pantheon provides a unified LLM interface with native SDK adapters, giving access to **many LLM providers** through a consistent API.

Key features:

- **Smart Model Selection**: Use quality tags (``high``, ``normal``, ``low``) instead of hardcoding model names
- **Automatic Provider Detection**: Pantheon detects available providers from your API keys
- **Capability Filtering**: Select models by capabilities (``vision``, ``reasoning``, ``tools``, etc.)
- **Fallback Chains**: Automatic failover to backup models

Smart Model Selection
---------------------

Instead of hardcoding specific model names, you can use Pantheon's intelligent tag-based selection.

Quality Tags
~~~~~~~~~~~~

Select models by quality level:

.. list-table::
   :header-rows: 1
   :widths: 15 35 50

   * - Tag
     - Description
     - Typical Models (System Defaults)
   * - ``high``
     - Most capable models for complex tasks
     - ``openai/gpt-5.4-pro``, ``anthropic/claude-opus-4-5-20251101``, ``gemini/gemini-3-pro-preview``
   * - ``normal``
     - Balanced performance and cost
     - ``openai/gpt-5.4``, ``anthropic/claude-sonnet-4-5-20250929``, ``gemini/gemini-3-flash-preview``
   * - ``low``
     - Fast and cost-effective
     - ``openai/gpt-5-mini``, ``anthropic/claude-haiku-4-5-20251001``, ``gemini/gemini-flash-lite-latest``

.. code-block:: python

   from pantheon.agent import Agent

   # Let Pantheon choose the best available model
   agent = Agent(model="high")      # Best quality
   agent = Agent(model="normal")    # Balanced (default)
   agent = Agent(model="low")       # Fast and cheap

Capability Tags
~~~~~~~~~~~~~~~

Filter models by required capabilities:

.. list-table::
   :header-rows: 1
   :widths: 15 85

   * - Tag
     - Description
   * - ``vision``
     - Image understanding (GPT-4o, Claude 3, Gemini Pro Vision)
   * - ``reasoning``
     - Enhanced reasoning (o1, DeepSeek R1)
   * - ``tools``
     - Function/tool calling support
   * - ``audio_in``
     - Audio input support
   * - ``audio_out``
     - Audio output/TTS support
   * - ``web``
     - Built-in web search
   * - ``pdf``
     - PDF document input
   * - ``computer``
     - Computer use capabilities
   * - ``schema``
     - Structured output/response schema
   * - ``prefill``
     - Assistant message prefilling

Combine quality and capability tags:

.. code-block:: python

   # High quality with vision support
   agent = Agent(model="high,vision")

   # Cost-effective with tool calling
   agent = Agent(model="low,tools")

   # Normal quality with reasoning
   agent = Agent(model="normal,reasoning")

Automatic Provider Detection
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Pantheon automatically detects available providers from environment variables:

.. code-block:: bash

   # Set any of these - Pantheon will use the first available
   export OPENAI_API_KEY="sk-..."
   export ANTHROPIC_API_KEY="sk-ant-..."
   export GEMINI_API_KEY="..."

Provider priority (configurable in settings.json):

1. OpenAI
2. Anthropic
3. Gemini
4. Z.ai (Zhipu)
5. DeepSeek

Supported Providers
-------------------

Pantheon supports many LLM providers. Here are the most common ones:

Major Cloud Providers
~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Provider
     - Prefix
     - Example Models
   * - OpenAI
     - ``openai/``
     - ``gpt-5.4``, ``gpt-5.4-pro``, ``gpt-5.2``, ``gpt-5-mini``, ``gpt-4o``
   * - Anthropic
     - ``anthropic/``
     - ``claude-opus-4-5-20251101``, ``claude-sonnet-4-5-20250929``, ``claude-haiku-4-5-20251001``
   * - Google AI
     - ``gemini/``
     - ``gemini-3-pro-preview``, ``gemini-3-flash-preview``, ``gemini-flash-lite-latest``
   * - Azure OpenAI
     - ``azure/``
     - Your deployed model names
   * - AWS Bedrock
     - ``bedrock/``
     - ``anthropic.claude-3``, ``amazon.titan``
   * - Google Vertex AI
     - ``vertex_ai/``
     - ``gemini-pro``, ``claude-3-sonnet``

Specialized AI Platforms
~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Provider
     - Prefix
     - Example Models
   * - Mistral AI
     - ``mistral/``
     - ``mistral-large``, ``mistral-medium``, ``codestral``
   * - Cohere
     - ``cohere/``
     - ``command-r-plus``, ``command-r``
   * - Groq
     - ``groq/``
     - ``llama-3.3-70b-versatile``, ``mixtral-8x7b-32768``
   * - Together AI
     - ``together_ai/``
     - ``llama-3``, ``qwen``, ``deepseek``
   * - Fireworks AI
     - ``fireworks_ai/``
     - ``llama-v3``, ``mixtral``
   * - DeepSeek
     - ``deepseek/``
     - ``deepseek-chat``, ``deepseek-reasoner``
   * - Perplexity
     - ``perplexity/``
     - ``pplx-70b-online``, ``sonar-medium``
   * - OpenRouter
     - ``openrouter/``
     - Access to 100+ models via single API

Local & Open Source
~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Provider
     - Prefix
     - Example Models
   * - Ollama
     - ``ollama/``
     - ``llama3``, ``mistral``, ``codellama``, ``qwen``
   * - vLLM
     - ``vllm/``
     - Any HuggingFace model
   * - LM Studio
     - ``lm_studio/``
     - Local models
   * - HuggingFace
     - ``huggingface/``
     - ``meta-llama/Llama-3``

Chinese Providers
~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Provider
     - Prefix
     - Example Models
   * - Z.ai (Zhipu)
     - ``zai/``
     - ``glm-4.6``, ``glm-4.5``, ``glm-4.5v``
   * - Qwen (Alibaba)
     - ``qwen/``
     - ``qwen-turbo``, ``qwen-plus``
   * - Baidu ERNIE
     - ``ernie/``
     - ``ernie-bot-4``
   * - Kimi (Moonshot)
     - *(none)*
     - ``kimi-for-coding``

.. note::

   **Kimi Coding**: Use model name ``kimi-for-coding`` with ``LLM_API_BASE=https://api.kimi.com/coding/v1``
   and your Kimi API key as ``LLM_API_KEY``. See `Kimi Code Docs <https://www.kimi.com/code/docs/en/more/third-party-agents.html>`_ for details.

.. note::

   For additional providers, see the Pantheon documentation or configure custom endpoints.

Model Format
------------

You can specify models in two ways:

**1. Strict Model Name** (``provider/model-name``):

.. code-block:: text

   openai/gpt-5.4
   anthropic/claude-opus-4-5-20251101
   gemini/gemini-3-pro-preview
   deepseek/deepseek-chat

**2. Tags** (quality and/or capability):

.. code-block:: text

   high
   normal
   low
   high,vision
   normal,reasoning
   low,tools

For OpenAI models, the prefix is optional:

.. code-block:: python

   # These are equivalent
   agent = Agent(model="gpt-4o")
   agent = Agent(model="openai/gpt-4o")

Configuration
-------------

Settings File
~~~~~~~~~~~~~

Configure model defaults in ``.pantheon/settings.json``:

.. code-block:: json

   {
     "models": {
       "provider_priority": ["openai", "anthropic", "gemini", "deepseek"],
       "provider_models": {
         "openai": {
           "high": ["openai/gpt-5.4-pro", "openai/gpt-5.4", "openai/gpt-5.2"],
           "normal": ["openai/gpt-5.4", "openai/gpt-5.2", "openai/gpt-4o"],
           "low": ["openai/gpt-5-mini", "openai/gpt-4o-mini"]
         },
         "anthropic": {
           "high": ["anthropic/claude-opus-4-5-20251101"],
           "normal": ["anthropic/claude-sonnet-4-5-20250929"],
           "low": ["anthropic/claude-haiku-4-5-20251001"]
         }
       }
     }
   }

Provider Priority
~~~~~~~~~~~~~~~~~

Control which provider is used when multiple API keys are available:

.. code-block:: json

   {
     "models": {
       "provider_priority": ["anthropic", "openai", "gemini"]
     }
   }

Model in Templates
~~~~~~~~~~~~~~~~~~

In agent templates (``.pantheon/agents/*.md``):

.. code-block:: markdown

   ---
   name: Smart Agent
   model: high,vision
   ---

   You are a helpful assistant with vision capabilities.

In team templates:

.. code-block:: markdown

   ---
   name: Research Team
   model: normal
   agents:
     - name: researcher
       model: high
       instructions: Research complex topics.
     - name: writer
       model: low
       instructions: Write summaries.
   ---

Python API
~~~~~~~~~~

.. code-block:: python

   from pantheon.agent import Agent

   # Using tags (recommended)
   agent = Agent(model="normal")
   agent = Agent(model="high,vision")

   # Using strict model name
   agent = Agent(model="anthropic/claude-opus-4-5-20251101")

   # Using fallback chain
   agent = Agent(model=["openai/gpt-5.4", "openai/gpt-5.2", "openai/gpt-4o"])

API Keys
--------

Set API keys via environment variables (recommended):

.. code-block:: bash

   # Major providers
   export OPENAI_API_KEY="sk-..."
   export ANTHROPIC_API_KEY="sk-ant-..."
   export GEMINI_API_KEY="..."
   export GOOGLE_API_KEY="..."  # Alternative for Gemini

   # Other providers
   export MISTRAL_API_KEY="..."
   export COHERE_API_KEY="..."
   export GROQ_API_KEY="..."
   export DEEPSEEK_API_KEY="..."
   export TOGETHER_API_KEY="..."
   export FIREWORKS_API_KEY="..."
   export OPENROUTER_API_KEY="..."

   # Chinese providers
   export ZAI_API_KEY="..."
   export QWEN_API_KEY="..."

Or in ``.pantheon/settings.json`` (not recommended for security):

.. code-block:: json

   {
     "api_keys": {
       "OPENAI_API_KEY": "sk-..."
     }
   }

Custom API Endpoint (Third-party Proxy)
---------------------------------------

To route all LLM calls through a third-party proxy or custom OpenAI-compatible endpoint,
set the universal ``LLM_API_BASE`` and ``LLM_API_KEY``:

.. code-block:: bash

   export LLM_API_BASE="https://your-proxy.com/v1"
   export LLM_API_KEY="your-proxy-key"

Or add to your ``.env`` / ``~/.pantheon/.env`` file:

.. code-block:: bash

   LLM_API_BASE=https://your-proxy.com/v1
   LLM_API_KEY=your-proxy-key

**Interactive setup:**

.. code-block:: bash

   # Launch the setup wizard
   pantheon setup
   # Select option [0] Custom API Endpoint

   # Or use /keys in the REPL
   /keys 0 https://your-proxy.com/v1 your-proxy-key

**Priority rules:**

- **Base URL**: ``OPENAI_API_BASE`` (provider-specific) > ``LLM_API_BASE`` (universal)
- **API Key (unified proxy mode)**: When ``LLM_API_BASE`` is set, ``LLM_API_KEY`` takes priority over provider-specific keys (e.g. ``OPENAI_API_KEY``). This ensures all requests to the proxy use the correct credentials.
- **API Key (normal mode)**: When no ``LLM_API_BASE`` is set, provider-specific keys (e.g. ``OPENAI_API_KEY``) take priority over ``LLM_API_KEY``.

Azure OpenAI
------------

Configure Azure endpoints:

.. code-block:: bash

   export AZURE_API_KEY="..."
   export AZURE_API_BASE="https://your-resource.openai.azure.com"
   export AZURE_API_VERSION="2024-02-01"

Use with:

.. code-block:: python

   agent = Agent(model="azure/your-deployment-name")

Local Models (Ollama)
---------------------

1. Install `Ollama <https://ollama.ai>`_
2. Pull a model: ``ollama pull llama3``
3. Use in Pantheon:

.. code-block:: python

   agent = Agent(model="ollama/llama3")

Model Parameters
----------------

Set model parameters per-agent:

.. code-block:: python

   agent = Agent(
       model="high",
       model_params={
           "temperature": 0.7,
           "max_tokens": 4000,
           "top_p": 0.9
       }
   )

In templates:

.. code-block:: markdown

   ---
   name: Creative Writer
   model: high
   temperature: 0.9
   max_tokens: 4000
   ---

Temperature Guidelines
~~~~~~~~~~~~~~~~~~~~~~

- **0.0-0.3**: Deterministic, factual (code, analysis)
- **0.4-0.7**: Balanced (general tasks)
- **0.8-1.0**: Creative (writing, brainstorming)

Troubleshooting
---------------

**"No provider available"**

.. code-block:: bash

   # Check if API keys are set
   echo $OPENAI_API_KEY
   echo $ANTHROPIC_API_KEY

   # Set one
   export OPENAI_API_KEY="sk-..."

**"Model not found"**

- Check model name spelling
- Verify the model exists for that provider
- Check provider prefix is correct

**"Rate limit exceeded"**

- Use quality tags - Pantheon will fallback automatically
- Configure fallback models in settings.json
- Add delays between requests

**Checking Available Models**

.. code-block:: python

   from pantheon.utils.model_selector import get_model_selector

   selector = get_model_selector()
   info = selector.list_available_models()

   print("Available providers:", info["available_providers"])
   print("Current provider:", info["current_provider"])
   print("Models:", info["models_by_provider"])
