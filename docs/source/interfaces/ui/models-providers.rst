Models & Providers
==================

The web app provides a full model browser and provider management panel. You can search,
filter, and switch models without restarting the backend, and manage API keys directly from
the settings panel.

Overview
--------

Pantheon supports multiple model sources:

- **OpenRouter** — a unified API aggregating 300+ models from dozens of providers.
- **Ollama** — locally hosted models running on your machine or a local server.
- **Direct providers** — OpenAI, Anthropic, Google, Mistral, and others via their native
  APIs, configured with provider API keys.
- **OAuth providers** — providers such as Codex that require browser-based authentication.

All of these are accessible from the same model browser in the UI.

Browsing & Selecting Models
----------------------------

Open the model browser by clicking the model tag in the input bar or in the **Agents**
panel next to any agent.

**Search and filter**

Type in the search box to filter by model name, provider, or capability tag
(e.g., ``vision``, ``coding``, ``128k``). The list updates in real time.

**OpenRouter catalog**

Models sourced from OpenRouter are marked with the OpenRouter badge. Each entry shows:

- Provider and model name (e.g., ``anthropic/claude-opus-4-5``)
- Context window size
- Input and output cost per million tokens
- Capability tags (vision, tool-use, JSON mode, etc.)

**Local Ollama models**

Ollama models are listed under the **Local** section. They show the model name and size on
disk. If Ollama is not running, the section displays a warning with a link to the Ollama
status panel.

**Saved / favorite models**

Click the star icon next to any model to add it to your **Favorites** list. Favorites
appear at the top of the model browser for quick access.

**Selecting a model**

Click a model row to select it. The model is applied to the active agent immediately. The
model tag in the input bar updates to reflect the new selection.

OpenRouter Integration
-----------------------

OpenRouter provides a single API key that gives access to models from Anthropic, OpenAI,
Google, Mistral, Meta, and many other providers.

**Setting the OpenRouter API key**

Go to **Settings → API Keys** and enter your OpenRouter key in the **OpenRouter** field.
The key is stored in ``.pantheon/settings.json`` (or the system keychain, depending on
your OS) and used for all OpenRouter-sourced models.

**Automatic catalog**

When an OpenRouter key is configured, Pantheon fetches the live model catalog from the
OpenRouter API and displays it in the browser. The catalog includes pricing, context
window, and capability information that is kept current.

**Usage and billing**

OpenRouter usage appears on your OpenRouter dashboard. Pantheon does not aggregate or
modify OpenRouter billing — costs are passed through directly at the rates published in
the catalog.

Ollama
------

Ollama runs open-weight models locally, with no API key required.

**Checking Ollama status**

The model browser's **Local** section shows a green or red indicator for the Ollama
service. Click **Ollama Status** to open a detail panel showing the Ollama version,
running models, and the URL Pantheon is configured to use (default:
``http://localhost:11434``).

**Pulling and selecting Ollama models**

If a model is not yet downloaded, click **Pull** next to its name. The download progress
is shown in real time. Once the download is complete, the model appears as available and
can be selected.

**Configuring the Ollama URL**

If Ollama is running on a different host (e.g., a GPU server), update the URL in
**Settings → Providers → Ollama URL**. Pantheon will connect to that host for all Ollama
model operations.

OAuth Providers
---------------

Some providers (such as Codex) use browser-based OAuth rather than a static API key.

**Authenticating**

Go to **Settings → Providers** and click **Connect** next to the provider. A new browser
tab opens with the provider's OAuth login page. After you authorize, the tab closes and
Pantheon stores the token.

**Token refresh**

Pantheon automatically refreshes OAuth tokens before they expire. If a token expires
unexpectedly (e.g., due to a revocation), the **Connect** button reappears in the
settings panel. Re-authenticate without restarting the backend.

API Key Management
------------------

API keys for all providers can be entered and updated from **Settings → API Keys**. Keys
are stored encrypted in ``.pantheon/settings.json`` and never logged.

Supported providers:

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Provider
     - Key setting
   * - OpenRouter
     - ``OPENROUTER_API_KEY``
   * - OpenAI
     - ``OPENAI_API_KEY``
   * - Anthropic
     - ``ANTHROPIC_API_KEY``
   * - Google (Gemini)
     - ``GOOGLE_API_KEY``
   * - Mistral
     - ``MISTRAL_API_KEY``
   * - Together AI
     - ``TOGETHER_API_KEY``
   * - Groq
     - ``GROQ_API_KEY``

Key changes take effect immediately for new requests — a backend restart is not required.

Model Fallback Chains
---------------------

You can configure a primary model and one or more fallbacks per agent. If the primary model
returns an error (rate limit, context overflow, etc.), Pantheon automatically retries with
the next model in the chain.

Fallback chains are configured in ``.pantheon/settings.json``:

.. code-block:: json

   {
     "model_fallback_chains": {
       "Analyst": [
         "anthropic/claude-opus-4-5",
         "openai/gpt-4o",
         "openrouter/mistralai/mistral-large"
       ]
     }
   }

The UI displays the active model for each agent and indicates when a fallback is being
used.

See :doc:`/configuration/models` for the full model configuration reference.
