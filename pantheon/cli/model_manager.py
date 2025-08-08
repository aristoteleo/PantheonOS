"""Model Management Module for Pantheon CLI"""

import json
from pathlib import Path
from typing import Optional

from .api_key_manager import APIKeyManager

# Available models configuration
AVAILABLE_MODELS = {
    # OpenAI Models - GPT-5 Series (Latest)
    "gpt-5": "OpenAI GPT-5 (Latest)",
    "gpt-5-mini": "OpenAI GPT-5 Mini",
    "gpt-5-nano": "OpenAI GPT-5 Nano",
    "gpt-5-chat-latest": "OpenAI GPT-5 Chat Latest",
    # OpenAI Models - GPT-4 Series
    "gpt-4.1": "OpenAI GPT-4.1", 
    "gpt-4.1-mini": "OpenAI GPT-4.1 Mini",
    "gpt-4.1-nano": "OpenAI GPT-4.1 Nano",
    "gpt-4o": "OpenAI GPT-4o",
    "gpt-4o-2024-05-13": "OpenAI GPT-4o (2024-05-13)",
    "gpt-4o-audio-preview": "OpenAI GPT-4o Audio Preview",
    "gpt-4o-realtime-preview": "OpenAI GPT-4o Realtime Preview",
    "gpt-4o-mini": "OpenAI GPT-4o Mini",
    "gpt-4o-mini-audio-preview": "OpenAI GPT-4o Mini Audio Preview",
    "gpt-4o-mini-realtime-preview": "OpenAI GPT-4o Mini Realtime Preview",
    # OpenAI Models - o-Series (Reasoning)
    "o1": "OpenAI o1 (Reasoning)",
    "o1-pro": "OpenAI o1 Pro (Reasoning)",
    "o3-pro": "OpenAI o3 Pro (Reasoning)",
    "o3": "OpenAI o3 (Reasoning)",
    "o3-deep-research": "OpenAI o3 Deep Research",
    "o4-mini": "OpenAI o4 Mini (Reasoning)",
    "o4-mini-deep-research": "OpenAI o4 Mini Deep Research",
    "o3-mini": "OpenAI o3 Mini (Reasoning)",
    "o1-mini": "OpenAI o1 Mini (Reasoning)",
    # OpenAI Models - Codex Series
    "codex-mini-latest": "OpenAI Codex Mini Latest",
    # Anthropic Models - Claude 4 Series (Latest)
    "anthropic/claude-opus-4-1-20250805": "Claude Opus 4.1 (Latest)",
    "anthropic/claude-opus-4-20250514": "Claude Opus 4",
    "anthropic/claude-sonnet-4-20250514": "Claude Sonnet 4",
    "anthropic/claude-3-7-sonnet-20250219": "Claude Sonnet 3.7",
    "anthropic/claude-3-5-haiku-20241022": "Claude Haiku 3.5",
    # Anthropic Models - Claude 3 Series (Legacy)
    "anthropic/claude-3-opus-20240229": "Claude 3 Opus (Legacy)",
    "anthropic/claude-3-sonnet-20240229": "Claude 3 Sonnet (Legacy)", 
    "anthropic/claude-3-haiku-20240307": "Claude 3 Haiku (Legacy)",
    # Google Models
    "gemini/gemini-2.5-pro": "Gemini 2.5 Pro",
    "gemini/gemini-2.5-flash": "Gemini 2.5 Flash",
    "gemini/gemini-2.0-pro": "Gemini 2.0 Pro",
    "gemini/gemini-2.0-flash": "Gemini 2.0 Flash",
    "gemini/gemini-pro": "Gemini Pro",
    # DeepSeek Models
    "deepseek/deepseek-chat": "DeepSeek Chat",
    "deepseek/deepseek-reasoner": "DeepSeek Reasoner",
    # Qwen/Alibaba Models - Latest 2025 Series
    "qwq-plus": "QwQ Plus (Reasoning)",
    "qwen-max": "Qwen Max (Latest)",
    "qwen-max-latest": "Qwen Max Latest",
    "qwen-max-2025-01-25": "Qwen Max 2025-01-25",
    "qwen-plus": "Qwen Plus (Latest)",
    "qwen-plus-latest": "Qwen Plus Latest", 
    "qwen-plus-2025-04-28": "Qwen Plus 2025-04-28",
    "qwen-plus-2025-01-25": "Qwen Plus 2025-01-25",
    "qwen-turbo": "Qwen Turbo (Latest)",
    "qwen-turbo-latest": "Qwen Turbo Latest",
    "qwen-turbo-2025-04-28": "Qwen Turbo 2025-04-28", 
    "qwen-turbo-2024-11-01": "Qwen Turbo 2024-11-01",
    "qvq-max": "QVQ Max (Visual Reasoning)",
    "qvq-max-latest": "QVQ Max Latest",
    "qvq-max-2025-03-25": "QVQ Max 2025-03-25",
    # Qwen/Alibaba Models - Legacy
    "qwen/qwen-2.5-72b-instruct": "Qwen 2.5 72B (Legacy)",
    # Kimi/Moonshot Models - Latest K2 Series
    "kimi-k2-0711-preview": "Kimi K2 (Preview)",
    "kimi-k2-turbo-preview": "Kimi K2 Turbo (Preview)",
    # Kimi/Moonshot Models - Latest Series
    "kimi-latest": "Kimi Latest (Auto Context)",
    "kimi-latest-8k": "Kimi Latest 8K",
    "kimi-latest-32k": "Kimi Latest 32K",
    "kimi-latest-128k": "Kimi Latest 128K",
    # Kimi/Moonshot Models - Moonshot V1 Series
    "moonshot-v1-8k": "Moonshot V1 8K",
    "moonshot-v1-32k": "Moonshot V1 32K",
    "moonshot-v1-128k": "Moonshot V1 128K",
    "moonshot-v1-8k-vision-preview": "Moonshot V1 8K Vision",
    "moonshot-v1-32k-vision-preview": "Moonshot V1 32K Vision",
    "moonshot-v1-128k-vision-preview": "Moonshot V1 128K Vision",
    # Kimi/Moonshot Models - Thinking Series
    "kimi-thinking-preview": "Kimi Thinking (Preview)",
    # Kimi/Moonshot Models - Legacy
    "moonshot/moonshot-v1-8k": "Kimi 8K (Legacy)",
    "moonshot/moonshot-v1-32k": "Kimi 32K (Legacy)", 
    "moonshot/moonshot-v1-128k": "Kimi 128K (Legacy)",
    # Grok/xAI Models
    "grok/grok-beta": "Grok Beta",
    "grok/grok-2": "Grok 2",
    # Local/Other Models
    "ollama/llama3.2": "Llama 3.2 (Local)",
}


class ModelManager:
    """Manages model selection and switching for Pantheon CLI"""
    
    def __init__(self, config_file_path: Path, api_key_manager: APIKeyManager):
        self.config_file_path = config_file_path
        self.api_key_manager = api_key_manager
        self.current_model = "gpt-5"
        self.current_agent = None
        self._load_model_config()
    
    def _load_model_config(self) -> str:
        """Load saved model configuration"""
        if self.config_file_path and self.config_file_path.exists():
            try:
                with open(self.config_file_path, 'r') as f:
                    config = json.load(f)
                    self.current_model = config.get('model', 'gpt-4.1')
            except Exception:
                pass
        return self.current_model
    
    def save_model_config(self, model: str):
        """Save current model configuration"""
        if not self.config_file_path:
            return
        
        # Load existing config to preserve API keys
        config = {'model': model}
        if self.config_file_path.exists():
            try:
                with open(self.config_file_path, 'r') as f:
                    config = json.load(f)
                    config['model'] = model
            except Exception:
                pass
        
        try:
            with open(self.config_file_path, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save model config: {e}")
    
    def set_agent(self, agent):
        """Set the current agent reference for model updates"""
        self.current_agent = agent
    
    def switch_model(self, new_model: str) -> str:
        """Switch to a new model"""
        if new_model not in AVAILABLE_MODELS:
            available = "\n".join([f"  {k}: {v}" for k, v in AVAILABLE_MODELS.items()])
            return f"❌ Model '{new_model}' not available. Available models:\n{available}"
        
        # Check API key availability
        key_available, key_message = self.api_key_manager.check_api_key_for_model(new_model)
        if not key_available:
            return f"❌ Cannot switch to {new_model}: {key_message}"
        
        old_model = self.current_model
        self.current_model = new_model
        
        # Update agent's model
        if self.current_agent:
            if isinstance(new_model, str):
                self.current_agent.models = [new_model]
                if new_model != "gpt-5-mini":
                    self.current_agent.models.append("gpt-5-mini")
            else:
                self.current_agent.models = new_model
        
        # Save configuration
        self.save_model_config(new_model)
        
        return f"✅ Switched from {AVAILABLE_MODELS.get(old_model, old_model)} to {AVAILABLE_MODELS[new_model]} ({new_model})\nℹ️ {key_message}"
    
    def list_models(self) -> str:
        """List all available models with API key status"""
        result = "🤖 Available Models (Top 3 per provider):\n\n"
        
        # Group models by provider with correct categorization
        providers = {}
        for model_id, description in AVAILABLE_MODELS.items():
            # Determine provider based on model naming patterns
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
        
        for provider, models in providers.items():
            result += f"{provider}:\n"
            # Show only first 3 models per provider
            top_models = models[:3]
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
            
            # Show count if there are more models
            if len(models) > 3:
                result += f"  ... and {len(models) - 3} more models\n"
            result += "\n"
        
        result += "Legend: 🟢 No API key needed | ✅ API key available | ❌ API key missing\n\n"
        result += f"💡 Usage: /model <model_id> | /api-key <provider> <key>\n"
        result += f"📝 Current: {AVAILABLE_MODELS.get(self.current_model, self.current_model)} ({self.current_model})"
        
        return result
    
    def get_current_model_status(self) -> str:
        """Get current model with API key status"""
        key_available, key_message = self.api_key_manager.check_api_key_for_model(self.current_model)
        key_status = "✅" if key_available else "❌"
        return f"📱 Current Model: {AVAILABLE_MODELS.get(self.current_model, self.current_model)} ({self.current_model})\n{key_status} {key_message}"
    
    def handle_model_command(self, command: str) -> str:
        """Handle /model commands"""
        parts = command.strip().split()
        
        if len(parts) == 1:  # Just "/model"
            return self.list_models()
        
        subcommand = parts[1].lower()
        
        if subcommand == "list":
            return self.list_models()
        elif subcommand == "current":
            return self.get_current_model_status()
        elif subcommand in AVAILABLE_MODELS:
            return self.switch_model(subcommand)
        else:
            # Try to match partial model names
            matches = [m for m in AVAILABLE_MODELS.keys() if subcommand in m.lower()]
            if len(matches) == 1:
                return self.switch_model(matches[0])
            elif len(matches) > 1:
                match_list = "\n".join([f"  • {m}: {AVAILABLE_MODELS[m]}" for m in matches])
                return f"🔍 **Multiple matches found:**\n{match_list}\n\n💡 Use the full model ID: `/model <model_id>`"
            else:
                return f"❌ Model '{subcommand}' not found. Use `/model list` to see available models."