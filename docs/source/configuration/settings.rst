Settings Reference
==================

The ``settings.json`` file contains all Pantheon configuration.

Location
--------

- **Project**: ``./.pantheon/settings.json``
- **User global**: ``~/.pantheon/settings.json``

Project settings override user settings.

Format
------

JSONC format (JSON with comments):

.. code-block:: json

   {
     // This is a comment
     "repl": {
       "quiet": false
     }
   }

Full Reference
--------------

API Keys
~~~~~~~~

.. code-block:: json

   {
     "api_keys": {
       "openai": "sk-...",
       "anthropic": "sk-ant-...",
       "google": "...",
       "huggingface": "..."
     }
   }

**Recommended**: Use environment variables instead:

.. code-block:: bash

   export OPENAI_API_KEY="sk-..."
   export ANTHROPIC_API_KEY="sk-ant-..."

For third-party proxy services, use the universal endpoint variables:

.. code-block:: bash

   export LLM_API_BASE="https://your-proxy.com/v1"
   export LLM_API_KEY="your-proxy-key"

See :doc:`models` for full details on custom API endpoints and priority rules.

Models
~~~~~~

Pantheon supports smart model selection with quality tags. See :doc:`models` for full details.

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

- ``provider_priority``: Order of preference when multiple API keys are available
- ``provider_models``: Model lists per quality level (``high``, ``normal``, ``low``) for each provider

Image Generation
~~~~~~~~~~~~~~~~

.. code-block:: json

   {
     "image_gen_model": "normal",
     "image_gen_models": {
       "gemini": {
         "high": ["gemini/gemini-3-pro-image-preview"],
         "normal": ["gemini/gemini-2.5-flash-image-preview"]
       },
       "openai": {
         "high": ["dall-e-3"],
         "normal": ["dall-e-3"]
       }
     }
   }

- ``image_gen_model``: Quality level for image generation (``high`` or ``normal``)

REPL Settings
~~~~~~~~~~~~~

.. code-block:: json

   {
     "repl": {
       "quiet": false,
       "default_template": "default",
       "log_level": "ERROR"
     }
   }

- ``quiet``: Suppress startup messages
- ``default_template``: Team template to load
- ``log_level``: DEBUG, INFO, WARNING, ERROR

Template Auto-Update
~~~~~~~~~~~~~~~~~~~~

.. code-block:: json

   {
     "default_template_auto_update": true
   }

When ``true`` (default), factory templates are synced to your ``.pantheon/`` directory on startup.
The sync uses **hash-based change detection** to avoid overwriting user modifications:

- If only the factory template changed, it is updated automatically.
- If you modified a template (e.g. via ``/model``), your changes are preserved even when the factory version updates.
- New templates from factory are always copied.

ChatRoom Settings
~~~~~~~~~~~~~~~~~

.. code-block:: json

   {
     "chatroom": {
       "memory_dir": ".pantheon/memory",
       "enable_nats_streaming": false,
       "speech_to_text_model": null
     }
   }

Endpoint Settings
~~~~~~~~~~~~~~~~~

.. code-block:: json

   {
     "endpoint": {
       "workspace": ".pantheon/workspace",
       "timeout": 120,
       "execution_mode": "local"
     }
   }

- ``workspace``: Working directory for file operations
- ``timeout``: Tool execution timeout (seconds)
- ``execution_mode``: "local" or "remote"

Services
~~~~~~~~

Built-in toolset configuration:

.. code-block:: json

   {
     "services": {
       "tier1": ["file_manager"],
       "tier2": ["python_interpreter", "shell"],
       "tier3": ["web_browse"]
     }
   }

Learning Settings
~~~~~~~~~~~~~~~~~

.. code-block:: json

   {
     "learning": {
       "enabled": true,
       "skillbook_path": ".pantheon/skills",
       "trajectory_tracking": true
     }
   }

Context Compression
~~~~~~~~~~~~~~~~~~~

.. code-block:: json

   {
     "context_compression": {
       "enabled": true,
       "threshold_tokens": 100000,
       "target_tokens": 50000
     }
   }

Knowledge Base
~~~~~~~~~~~~~~

.. code-block:: json

   {
     "knowledge": {
       "vector_db": "qdrant",
       "qdrant_url": "http://localhost:6333",
       "embedding_model": "text-embedding-3-small"
     }
   }

Remote Backend
~~~~~~~~~~~~~~

For distributed deployments:

.. code-block:: json

   {
     "remote": {
       "nats_url": "nats://localhost:4222",
       "backend": "nats"
     }
   }

Environment Variables
---------------------

API Keys (auto-detected by Pantheon):

.. list-table::
   :header-rows: 1

   * - Variable
     - Provider
   * - ``OPENAI_API_KEY``
     - OpenAI
   * - ``ANTHROPIC_API_KEY``
     - Anthropic
   * - ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY``
     - Google AI / Gemini
   * - ``AZURE_API_KEY``
     - Azure OpenAI
   * - ``MISTRAL_API_KEY``
     - Mistral AI
   * - ``COHERE_API_KEY``
     - Cohere
   * - ``GROQ_API_KEY``
     - Groq
   * - ``DEEPSEEK_API_KEY``
     - DeepSeek
   * - ``TOGETHER_API_KEY``
     - Together AI
   * - ``FIREWORKS_API_KEY``
     - Fireworks AI
   * - ``OPENROUTER_API_KEY``
     - OpenRouter
   * - ``ZAI_API_KEY``
     - Z.ai (Zhipu)

System Variables:

.. list-table::
   :header-rows: 1

   * - Variable
     - Description
   * - ``PANTHEON_LOG_LEVEL``
     - Log level (DEBUG, INFO, WARNING, ERROR)
   * - ``PANTHEON_CONFIG_DIR``
     - Override config directory

Example Configuration
---------------------

Minimal (uses automatic provider detection):

.. code-block:: json

   {}

With custom model configuration:

.. code-block:: json

   {
     "models": {
       "provider_priority": ["anthropic", "openai"],
       "provider_models": {
         "anthropic": {
           "high": ["anthropic/claude-opus-4-5-20251101"],
           "normal": ["anthropic/claude-sonnet-4-5-20250929"],
           "low": ["anthropic/claude-haiku-4-5-20251001"]
         }
       }
     },
     "repl": {
       "quiet": false,
       "default_template": "developer_team"
     },
     "chatroom": {
       "memory_dir": ".pantheon/memory"
     },
     "endpoint": {
       "workspace": ".pantheon/workspace",
       "timeout": 120
     },
     "learning": {
       "enabled": true
     }
   }
