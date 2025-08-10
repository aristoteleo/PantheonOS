def list_models(self) -> str:
    """List top 3 models from each provider with API key status"""
    result = "🤖 Available Models (Top 3 per provider):\n\n"
    
    # Define provider model priorities (latest first)
    provider_priorities = {
        "OpenAI": [
            # GPT-5 Series (Latest)
            "gpt-5", "gpt-5-mini", "gpt-5-nano",
            # o-Series (Reasoning) 
            "o3", "o1-pro", "o4-mini",
            # GPT-4 Series
            "gpt-4o", "gpt-4.1", "gpt-4o-mini"
        ],
        "Anthropic": [
            # Claude 4 Series (Latest)
            "anthropic/claude-opus-4-1-20250805", "anthropic/claude-sonnet-4-20250514", "anthropic/claude-3-7-sonnet-20250219",
            # Claude 3 Series (Legacy)
            "anthropic/claude-3-opus-20240229", "anthropic/claude-3-sonnet-20240229", "anthropic/claude-3-haiku-20240307"
        ],
        "Qwen": [
            # Latest 2025 Series
            "qwq-plus", "qwen-max", "qwen-plus",
            # Visual & Enhanced
            "qvq-max", "qwen-turbo", "qwen-max-latest"
        ],
        "Kimi": [
            # K2 & Latest Series
            "kimi-k2-turbo-preview", "kimi-latest", "kimi-thinking-preview",
            # V1 Series
            "moonshot-v1-128k", "moonshot-v1-32k", "moonshot-v1-8k"
        ]
    }
    
    # Group models by provider with custom provider names
    providers = {}
    for model_id, description in AVAILABLE_MODELS.items():
        # Determine provider
        if model_id.startswith("anthropic/"):
            provider = "Anthropic"
        elif model_id.startswith(("qwq-", "qwen-", "qvq-")) or model_id.startswith("qwen/"):
            provider = "Qwen"
        elif model_id.startswith(("kimi-", "moonshot-")) or model_id.startswith("moonshot/"):
            provider = "Kimi"
        elif model_id.startswith("grok/"):
            provider = "Grok"
        elif model_id.startswith("gemini/"):
            provider = "Google"
        elif model_id.startswith("deepseek/"):
            provider = "DeepSeek"
        elif model_id.startswith("ollama/"):
            provider = "Local"
        else:
            provider = "OpenAI"
        
        if provider not in providers:
            providers[provider] = []
        providers[provider].append((model_id, description))
    
    # Display top 3 models per provider based on priority
    for provider_name in ["OpenAI", "Anthropic", "Qwen", "Kimi", "Grok", "Google", "DeepSeek", "Local"]:
        if provider_name not in providers:
            continue
            
        models = providers[provider_name]
        
        # Sort models by priority if defined, otherwise keep original order
        if provider_name in provider_priorities:
            priority_list = provider_priorities[provider_name]
            # Sort by priority (earlier in list = higher priority)
            models.sort(key=lambda x: priority_list.index(x[0]) if x[0] in priority_list else 999)
        
        # Show only top 3 models
        top_models = models[:3]
        
        result += f"{provider_name}:\n"
        for model_id, description in top_models:
            current_indicator = " ← Current" if model_id == self.current_model else ""
            
            # Check API key status
            key_available, _ = self.api_key_manager.check_api_key_for_model(model_id)
            from .api_key_manager import PROVIDER_API_KEYS
            if PROVIDER_API_KEYS.get(model_id) is None:
                key_status = " 🟢"  # Green circle for no key needed
            elif key_available:
                key_status = " ✅"  # Checkmark for available key
            else:
                key_status = " ❌"  # X for missing key
            
            result += f"  • {model_id}: {description}{key_status}{current_indicator}\n"
        
        # Show total count if there are more models
        total_count = len(models)
        if total_count > 3:
            result += f"  ... and {total_count - 3} more models\n"
        result += "\n"
    
    result += "Legend: 🟢 No API key needed | ✅ API key available | ❌ API key missing\n\n"
    result += f"💡 Usage: /model <model_id> | /api-key <provider> <key>\n"
    result += f"📝 Current: {AVAILABLE_MODELS.get(self.current_model, self.current_model)} ({self.current_model})"
    
    return result