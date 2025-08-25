"""API Key Management Module for Pantheon CLI"""

import os
import json
import base64
from pathlib import Path
from typing import Dict, Tuple

try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False

# Provider-specific API key mappings
PROVIDER_API_KEYS = {
    # OpenAI Models - GPT-5 Series (Latest)
    "gpt-5": "OPENAI_API_KEY",
    "gpt-5-mini": "OPENAI_API_KEY",
    "gpt-5-nano": "OPENAI_API_KEY",
    "gpt-5-chat-latest": "OPENAI_API_KEY",
    # OpenAI Models - GPT-4 Series
    "gpt-4.1": "OPENAI_API_KEY", 
    "gpt-4.1-mini": "OPENAI_API_KEY",
    "gpt-4.1-nano": "OPENAI_API_KEY",
    "gpt-4o": "OPENAI_API_KEY",
    "gpt-4o-2024-05-13": "OPENAI_API_KEY",
    "gpt-4o-audio-preview": "OPENAI_API_KEY",
    "gpt-4o-realtime-preview": "OPENAI_API_KEY",
    "gpt-4o-mini": "OPENAI_API_KEY",
    "gpt-4o-mini-audio-preview": "OPENAI_API_KEY",
    "gpt-4o-mini-realtime-preview": "OPENAI_API_KEY",
    # OpenAI Models - o-Series (Reasoning)
    "o1": "OPENAI_API_KEY",
    "o1-pro": "OPENAI_API_KEY",
    "o3-pro": "OPENAI_API_KEY",
    "o3": "OPENAI_API_KEY",
    "o3-deep-research": "OPENAI_API_KEY",
    "o4-mini": "OPENAI_API_KEY",
    "o4-mini-deep-research": "OPENAI_API_KEY",
    "o3-mini": "OPENAI_API_KEY",
    "o1-mini": "OPENAI_API_KEY",
    # OpenAI Models - Codex Series
    "codex-mini-latest": "OPENAI_API_KEY",
    # Anthropic Models - Claude 4 Series (Latest)
    "anthropic/claude-opus-4-1-20250805": "ANTHROPIC_API_KEY",
    "anthropic/claude-opus-4-20250514": "ANTHROPIC_API_KEY",
    "anthropic/claude-sonnet-4-20250514": "ANTHROPIC_API_KEY",
    "anthropic/claude-3-7-sonnet-20250219": "ANTHROPIC_API_KEY",
    "anthropic/claude-3-5-haiku-20241022": "ANTHROPIC_API_KEY",
    # Anthropic Models - Claude 3 Series (Legacy)
    "anthropic/claude-3-opus-20240229": "ANTHROPIC_API_KEY",
    "anthropic/claude-3-sonnet-20240229": "ANTHROPIC_API_KEY", 
    "anthropic/claude-3-haiku-20240307": "ANTHROPIC_API_KEY",
    # Google Models
    "gemini/gemini-2.5-pro": "GOOGLE_API_KEY",
    "gemini/gemini-2.5-flash": "GOOGLE_API_KEY",
    "gemini/gemini-2.0-pro": "GOOGLE_API_KEY",
    "gemini/gemini-2.0-flash": "GOOGLE_API_KEY",
    "gemini/gemini-pro": "GOOGLE_API_KEY",
    # DeepSeek Models
    "deepseek/deepseek-chat": "DEEPSEEK_API_KEY",
    "deepseek/deepseek-reasoner": "DEEPSEEK_API_KEY",
    # Qwen/Alibaba Models - Latest 2025 Series
    "qwq-plus": "QWEN_API_KEY",
    "qwen-max": "QWEN_API_KEY",
    "qwen-max-latest": "QWEN_API_KEY",
    "qwen-max-2025-01-25": "QWEN_API_KEY",
    "qwen-plus": "QWEN_API_KEY",
    "qwen-plus-latest": "QWEN_API_KEY", 
    "qwen-plus-2025-04-28": "QWEN_API_KEY",
    "qwen-plus-2025-01-25": "QWEN_API_KEY",
    "qwen-turbo": "QWEN_API_KEY",
    "qwen-turbo-latest": "QWEN_API_KEY",
    "qwen-turbo-2025-04-28": "QWEN_API_KEY", 
    "qwen-turbo-2024-11-01": "QWEN_API_KEY",
    "qvq-max": "QWEN_API_KEY",
    "qvq-max-latest": "QWEN_API_KEY",
    "qvq-max-2025-03-25": "QWEN_API_KEY",
    # Qwen/Alibaba Models - Legacy
    "qwen/qwen-2.5-72b-instruct": "QWEN_API_KEY",
    # Kimi/Moonshot Models - Latest K2 Series
    "kimi-k2-0711-preview": "MOONSHOT_API_KEY",
    "kimi-k2-turbo-preview": "MOONSHOT_API_KEY",
    # Kimi/Moonshot Models - Latest Series
    "kimi-latest": "MOONSHOT_API_KEY",
    "kimi-latest-8k": "MOONSHOT_API_KEY",
    "kimi-latest-32k": "MOONSHOT_API_KEY",
    "kimi-latest-128k": "MOONSHOT_API_KEY",
    # Kimi/Moonshot Models - Moonshot V1 Series
    "moonshot-v1-8k": "MOONSHOT_API_KEY",
    "moonshot-v1-32k": "MOONSHOT_API_KEY",
    "moonshot-v1-128k": "MOONSHOT_API_KEY",
    "moonshot-v1-8k-vision-preview": "MOONSHOT_API_KEY",
    "moonshot-v1-32k-vision-preview": "MOONSHOT_API_KEY",
    "moonshot-v1-128k-vision-preview": "MOONSHOT_API_KEY",
    # Kimi/Moonshot Models - Thinking Series
    "kimi-thinking-preview": "MOONSHOT_API_KEY",
    # Kimi/Moonshot Models - Legacy
    "moonshot/moonshot-v1-8k": "MOONSHOT_API_KEY",
    "moonshot/moonshot-v1-32k": "MOONSHOT_API_KEY",
    "moonshot/moonshot-v1-128k": "MOONSHOT_API_KEY",
    # Grok/xAI Models
    "grok/grok-beta": "GROK_API_KEY", 
    "grok/grok-2": "GROK_API_KEY",
    # Local/Other Models (no key needed)
    "ollama/llama3.2": None,
}

# Provider display names
PROVIDER_NAMES = {
    "OPENAI_API_KEY": "OpenAI",
    "ANTHROPIC_API_KEY": "Anthropic", 
    "GOOGLE_API_KEY": "Google",
    "DEEPSEEK_API_KEY": "DeepSeek",
    "QWEN_API_KEY": "Qwen (Alibaba)",
    "MOONSHOT_API_KEY": "Kimi (Moonshot)",
    "GROK_API_KEY": "Grok (xAI)",
}


class APIKeyManager:
    """Manages API keys and endpoints for different LLM providers with secure storage"""
    
    def __init__(self, config_file_path: Path):
        self.config_file_path = config_file_path
        self.api_keys_cache: Dict[str, str] = {}
        self.endpoints_cache: Dict[str, str] = {}
        self._load_config()
    
    def _get_encryption_key(self) -> bytes:
        """Get or create encryption key for API keys"""
        key_file = Path.home() / ".pantheon_key"
        
        if key_file.exists():
            with open(key_file, 'rb') as f:
                return f.read()
        else:
            key = Fernet.generate_key()
            # Store with restrictive permissions
            key_file.touch(mode=0o600)
            with open(key_file, 'wb') as f:
                f.write(key)
            return key
    
    def _encrypt_api_key(self, api_key: str) -> str:
        """Encrypt API key for secure storage"""
        if not CRYPTO_AVAILABLE:
            # Fallback to base64 encoding (not secure, but better than plaintext)
            return base64.b64encode(api_key.encode()).decode()
        
        key = self._get_encryption_key()
        fernet = Fernet(key)
        return fernet.encrypt(api_key.encode()).decode()
    
    def _decrypt_api_key(self, encrypted_key: str) -> str:
        """Decrypt API key from storage"""
        if not CRYPTO_AVAILABLE:
            # Fallback to base64 decoding
            try:
                return base64.b64decode(encrypted_key.encode()).decode()
            except:
                return encrypted_key  # Assume it's already decrypted
        
        key = self._get_encryption_key()
        fernet = Fernet(key)
        return fernet.decrypt(encrypted_key.encode()).decode()
    
    def _load_config(self) -> None:
        """Load encrypted API keys and endpoints from config"""
        if self.config_file_path and self.config_file_path.exists():
            try:
                with open(self.config_file_path, 'r') as f:
                    config = json.load(f)
                    
                    # Load API keys
                    encrypted_keys = config.get('api_keys', {})
                    for provider, encrypted_key in encrypted_keys.items():
                        try:
                            decrypted_key = self._decrypt_api_key(encrypted_key)
                            self.api_keys_cache[provider] = decrypted_key
                            # IMPORTANT: Set environment variable for LiteLLM to use
                            os.environ[provider] = decrypted_key
                        except Exception:
                            pass  # Skip corrupted keys
                    
                    # Load endpoints
                    endpoints = config.get('endpoints', {})
                    for provider, endpoint in endpoints.items():
                        self.endpoints_cache[provider] = endpoint
                        # Set environment variable for LiteLLM base_url
                        env_var = f"{provider.upper()}_API_BASE"
                        os.environ[env_var] = endpoint
                        
            except Exception:
                pass
        
        # Also load from environment variables
        for provider in PROVIDER_NAMES.keys():
            if provider not in self.api_keys_cache:
                env_key = os.environ.get(provider)
                if env_key:
                    self.api_keys_cache[provider] = env_key
    
    def sync_environment_variables(self):
        """Sync cached API keys and endpoints to environment variables"""
        for provider, api_key in self.api_keys_cache.items():
            if api_key:  # Only set non-empty keys
                os.environ[provider] = api_key
        
        for provider, endpoint in self.endpoints_cache.items():
            if endpoint:  # Only set non-empty endpoints
                env_var = f"{provider.upper()}_API_BASE"
                os.environ[env_var] = endpoint
    
    def save_api_key(self, provider: str, api_key: str) -> bool:
        """Save encrypted API key to config"""
        if not self.config_file_path:
            return False
        
        # Load existing config
        config = {}
        if self.config_file_path.exists():
            try:
                with open(self.config_file_path, 'r') as f:
                    config = json.load(f)
            except Exception:
                pass
        
        # Add encrypted API key
        if 'api_keys' not in config:
            config['api_keys'] = {}
        
        config['api_keys'][provider] = self._encrypt_api_key(api_key)
        
        # Cache the decrypted key
        self.api_keys_cache[provider] = api_key
        
        # Set environment variable for immediate use
        # provider parameter is already the full env var name (e.g. "OPENAI_API_KEY")
        os.environ[provider] = api_key
        
        try:
            # Ensure restrictive permissions
            self.config_file_path.touch(mode=0o600)
            with open(self.config_file_path, 'w') as f:
                json.dump(config, f, indent=2)
            return True
        except Exception as e:
            print(f"Warning: Could not save API key: {e}")
            return False
    
    def save_endpoint(self, provider: str, endpoint: str) -> bool:
        """Save endpoint URL to config"""
        if not self.config_file_path:
            return False
        
        # Load existing config
        config = {}
        if self.config_file_path.exists():
            try:
                with open(self.config_file_path, 'r') as f:
                    config = json.load(f)
            except Exception:
                pass
        
        # Add endpoint
        if 'endpoints' not in config:
            config['endpoints'] = {}
        
        config['endpoints'][provider] = endpoint
        
        # Cache the endpoint
        self.endpoints_cache[provider] = endpoint
        
        # Set environment variable for immediate use
        env_var = f"{provider.upper()}_API_BASE"
        os.environ[env_var] = endpoint
        
        try:
            # Ensure restrictive permissions
            self.config_file_path.touch(mode=0o600)
            with open(self.config_file_path, 'w') as f:
                json.dump(config, f, indent=2)
            return True
        except Exception as e:
            print(f"Warning: Could not save endpoint: {e}")
            return False
    
    def check_api_key_for_model(self, model: str) -> Tuple[bool, str]:
        """Check if API key is available for the given model"""
        required_key = PROVIDER_API_KEYS.get(model)
        
        if required_key is None:
            return True, "No API key required"  # Local models like Ollama
        
        # Check cache first
        if required_key in self.api_keys_cache and self.api_keys_cache[required_key]:
            return True, f"{PROVIDER_NAMES[required_key]} API key available"
        
        # Check environment variable
        if os.environ.get(required_key):
            self.api_keys_cache[required_key] = os.environ[required_key]
            return True, f"{PROVIDER_NAMES[required_key]} API key available (from environment)"
        
        provider_name = PROVIDER_NAMES[required_key]
        return False, f"{provider_name} API key required. Use '/api-key {required_key.lower().replace('_api_key', '')} <your-key>' to set it."
    
    def list_api_keys(self) -> str:
        """List API key status for all providers"""
        result = "🔑 API Key Management:\n\n"
        
        for provider_key, provider_name in PROVIDER_NAMES.items():
            key_available = provider_key in self.api_keys_cache and self.api_keys_cache[provider_key]
            env_available = bool(os.environ.get(provider_key))
            
            status_icon = "✅" if (key_available or env_available) else "❌"
            source = ""
            if key_available and not env_available:
                source = " (saved)"
            elif env_available and not key_available:
                source = " (environment)"
            elif key_available and env_available:
                source = " (saved + environment)"
            
            provider_cmd = provider_key.lower().replace('_api_key', '')
            result += f"{status_icon} {provider_name}: {provider_cmd}{source}\n"
        
        result += "\n💡 Usage:\n"
        result += "  /api-key list - Show this status\n"
        result += "  /api-key <provider> <key> - Set API key\n"
        result += "  Examples:\n"
        result += "    /api-key openai sk-... - Set OpenAI key\n"
        result += "    /api-key anthropic sk-... - Set Anthropic key\n"
        result += "    /api-key google ai... - Set Google key\n"
        result += "    /api-key deepseek sk-... - Set DeepSeek key\n"
        result += "    /api-key qwen sk-... - Set Qwen key\n"
        result += "    /api-key kimi sk-... - Set Kimi key\n"
        result += "    /api-key grok sk-... - Set Grok key\n"
        result += "\n🌐 Endpoint Management:\n"
        result += "  /endpoint list - Show endpoint status\n"
        result += "  /endpoint <provider> <url> - Set custom endpoint\n"
        result += "  Examples:\n"
        result += "    /endpoint openai https://api.openai.com/v1\n"
        result += "    /endpoint anthropic https://api.anthropic.com\n"
        result += "    /endpoint custom https://your-proxy.com/v1\n"
        
        return result
    
    def list_endpoints(self) -> str:
        """List endpoint configuration for all providers"""
        result = "🌐 Endpoint Management:\n\n"
        
        # Default endpoints
        default_endpoints = {
            "openai": "https://api.openai.com/v1",
            "anthropic": "https://api.anthropic.com",
            "google": "https://generativelanguage.googleapis.com/v1beta",
            "deepseek": "https://api.deepseek.com/v1", 
            "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "moonshot": "https://api.moonshot.cn/v1",
            "grok": "https://api.x.ai/v1",
        }
        
        result += "Current Endpoints:\n"
        for provider, default_url in default_endpoints.items():
            custom_url = self.endpoints_cache.get(provider)
            if custom_url:
                status = f"✅ Custom: {custom_url}"
            else:
                status = f"🟢 Default: {default_url}"
            
            result += f"  • {provider.capitalize()}: {status}\n"
        
        # Show any additional custom endpoints
        for provider, endpoint in self.endpoints_cache.items():
            if provider not in default_endpoints:
                result += f"  • {provider.capitalize()}: ✅ Custom: {endpoint}\n"
        
        result += "\n💡 Usage:\n"
        result += "  /endpoint list - Show this status\n" 
        result += "  /endpoint <provider> <url> - Set custom endpoint\n"
        result += "  /endpoint reset <provider> - Reset to default endpoint\n"
        
        return result
    
    def show_api_key_status(self) -> str:
        """Show detailed API key status"""
        from .model_manager import AVAILABLE_MODELS
        
        result = "📊 API Key Status Report:\n\n"
        
        # Check which models are ready to use
        ready_models = []
        blocked_models = []
        
        for model_id in AVAILABLE_MODELS.keys():
            key_available, _ = self.check_api_key_for_model(model_id)
            if key_available:
                ready_models.append(model_id)
            else:
                blocked_models.append(model_id)
        
        result += f"✅ Ready Models ({len(ready_models)}): {', '.join(ready_models)}\n\n"
        
        if blocked_models:
            result += f"❌ Blocked Models ({len(blocked_models)}): {', '.join(blocked_models)}\n\n"
        
        # Show provider status
        result += "Provider Status:\n"
        for provider_key, provider_name in PROVIDER_NAMES.items():
            status = "✅ Ready" if (provider_key in self.api_keys_cache or os.environ.get(provider_key)) else "❌ Missing"
            result += f"  • {provider_name}: {status}\n"
        
        return result
    
    def handle_api_key_command(self, command: str) -> str:
        """Handle /api-key commands"""
        parts = command.strip().split(maxsplit=2)
        
        if len(parts) == 1:  # Just "/api-key"
            return self.list_api_keys()
        
        subcommand = parts[1].lower()
        
        if subcommand == "list":
            return self.list_api_keys()
        elif subcommand == "status":
            return self.show_api_key_status()
        elif subcommand in ['openai', 'anthropic', 'google', 'deepseek', 'qwen', 'kimi', 'moonshot', 'grok']:
            if len(parts) < 3:
                return f"❌ Please provide the API key: `/api-key {subcommand} <your-key>`"
            
            # Handle provider key mapping
            if subcommand in ['kimi', 'moonshot']:
                provider_key = "MOONSHOT_API_KEY"
            else:
                provider_key = f"{subcommand.upper()}_API_KEY"
            api_key = parts[2]
            
            if len(api_key) < 10:  # Basic validation
                return f"❌ API key seems too short. Please check your key."
            
            if self.save_api_key(provider_key, api_key):
                return f"✅ {PROVIDER_NAMES[provider_key]} API key saved and set successfully!"
            else:
                return f"❌ Failed to save {PROVIDER_NAMES[provider_key]} API key. Check file permissions."
        else:
            return f"❌ Unknown provider '{subcommand}'. Available: openai, anthropic, google, deepseek, qwen, kimi, grok"
    
    def handle_endpoint_command(self, command: str) -> str:
        """Handle /endpoint commands"""
        parts = command.strip().split(maxsplit=2)
        
        if len(parts) == 1:  # Just "/endpoint"
            return self.list_endpoints()
        
        subcommand = parts[1].lower()
        
        if subcommand == "list":
            return self.list_endpoints()
        elif subcommand == "reset":
            if len(parts) < 3:
                return "❌ Please specify provider to reset: `/endpoint reset <provider>`"
            
            provider = parts[2].lower()
            if provider in self.endpoints_cache:
                del self.endpoints_cache[provider]
                env_var = f"{provider.upper()}_API_BASE"
                if env_var in os.environ:
                    del os.environ[env_var]
                
                # Update config file
                try:
                    if self.config_file_path and self.config_file_path.exists():
                        with open(self.config_file_path, 'r') as f:
                            config = json.load(f)
                        if 'endpoints' in config and provider in config['endpoints']:
                            del config['endpoints'][provider]
                            with open(self.config_file_path, 'w') as f:
                                json.dump(config, f, indent=2)
                    return f"✅ {provider.capitalize()} endpoint reset to default"
                except Exception as e:
                    return f"❌ Failed to reset endpoint: {e}"
            else:
                return f"❌ No custom endpoint set for {provider}"
        else:
            # Set custom endpoint: /endpoint <provider> <url>
            if len(parts) < 3:
                return f"❌ Please provide the endpoint URL: `/endpoint {subcommand} <url>`"
            
            provider = subcommand
            endpoint_url = parts[2]
            
            # Basic URL validation
            if not endpoint_url.startswith(('http://', 'https://')):
                return f"❌ Endpoint URL must start with http:// or https://"
            
            if self.save_endpoint(provider, endpoint_url):
                return f"✅ {provider.capitalize()} endpoint set to: {endpoint_url}"
            else:
                return f"❌ Failed to save {provider} endpoint. Check file permissions."