"""Model Management Module for Pantheon CLI"""

import json
from pathlib import Path
from typing import Optional

from .api_key_manager import APIKeyManager

# Available models configuration
AVAILABLE_MODELS = {
    # OpenAI Models
    "gpt-4o": "OpenAI GPT-4o (Latest)",
    "gpt-4.1": "OpenAI GPT-4.1 (Default)", 
    "gpt-4.1-mini": "OpenAI GPT-4.1-mini (Fast)",
    "gpt-4o-mini": "OpenAI GPT-4o-mini (Cost-effective)",
    "o3": "OpenAI o3 (Reasoning)",
    "o3-mini": "OpenAI o3-mini (Reasoning, Fast)",
    # Anthropic Models
    "anthropic/claude-3-opus-20240229": "Claude 3 Opus",
    "anthropic/claude-3-sonnet-20240229": "Claude 3 Sonnet", 
    "anthropic/claude-3-haiku-20240307": "Claude 3 Haiku",
    # Google Models
    "gemini/gemini-2.0-flash": "Gemini 2.0 Flash",
    "gemini/gemini-pro": "Gemini Pro",
    # DeepSeek Models
    "deepseek/deepseek-chat": "DeepSeek Chat",
    "deepseek/deepseek-reasoner": "DeepSeek Reasoner",
    # Local/Other Models
    "ollama/llama3.2": "Llama 3.2 (Local)",
}


class ModelManager:
    """Manages model selection and switching for Pantheon CLI"""
    
    def __init__(self, config_file_path: Path, api_key_manager: APIKeyManager):
        self.config_file_path = config_file_path
        self.api_key_manager = api_key_manager
        self.current_model = "gpt-4.1"
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
                if new_model != "gpt-4.1-mini":
                    self.current_agent.models.append("gpt-4.1-mini")
            else:
                self.current_agent.models = new_model
        
        # Save configuration
        self.save_model_config(new_model)
        
        return f"✅ Switched from {AVAILABLE_MODELS.get(old_model, old_model)} to {AVAILABLE_MODELS[new_model]} ({new_model})\nℹ️ {key_message}"
    
    def list_models(self) -> str:
        """List all available models with API key status"""
        result = "🤖 Available Models:\n\n"
        
        # Group models by provider
        providers = {}
        for model_id, description in AVAILABLE_MODELS.items():
            if "/" in model_id:
                provider = model_id.split("/")[0].title()
            else:
                provider = "OpenAI"
            
            if provider not in providers:
                providers[provider] = []
            providers[provider].append((model_id, description))
        
        for provider, models in providers.items():
            result += f"{provider}:\n"
            for model_id, description in models:
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