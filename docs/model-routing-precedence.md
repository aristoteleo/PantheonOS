# Model Routing Precedence

Pantheon now resolves chat model choices in one explicit order so that
`codex`, `openai`, and configured Kimi coding endpoints do not silently
override one another.

## Final precedence

```mermaid
flowchart TD
    A["User model choice"] --> B{"Explicit provider/model?"}
    B -->|Yes| C["Use exact route<br/>Examples: codex/gpt-5.4-mini<br/>openai/gpt-5.4<br/>custom_anthropic/K2.5"]
    B -->|No| D{"Known alias?"}
    D -->|Yes| E["Normalize alias to explicit route<br/>codex oauth -> codex/gpt-5.4-mini<br/>openai chatgpt -> openai/gpt-5.4<br/>kimi-for-coding -> custom_anthropic/<configured model>"]
    D -->|No| F{"Quality/capability tag?"}
    F -->|Yes| G["Resolve tag via ModelSelector<br/>high / normal / low / vision / tools"]
    F -->|No| H["Treat as explicit model name and validate provider"]
    G --> I["Automatic provider selection"]
    I --> J["For non-custom tag requests, skip custom endpoints and fall back to standard providers"]
```

## Why this matters

- Explicit provider routes always win.
- Friendly aliases are converted once into explicit routes and then persisted.
- Tags still support automatic provider selection.
- Custom endpoints are only chosen automatically for explicit custom requests,
  not for unrelated `high` or `normal` tag resolutions.
