# PyInstaller runtime hook for tiktoken
# tiktoken uses pkgutil.iter_modules on the tiktoken_ext namespace package
# to discover encoding constructors. This doesn't work reliably in frozen
# environments because namespace package __path__ may not resolve correctly.
# We bypass the discovery by directly importing and registering the constructors.

import tiktoken.registry
import tiktoken_ext.openai_public

tiktoken.registry.ENCODING_CONSTRUCTORS = dict(
    tiktoken_ext.openai_public.ENCODING_CONSTRUCTORS
)
