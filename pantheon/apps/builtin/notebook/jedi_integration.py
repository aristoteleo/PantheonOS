#!/usr/bin/env python3
"""
Jedi Integration Module - Simplified Code Intelligence
Provides clean completion and inspection using pure Jedi approach
"""

from jedi.api import Script
from typing import Dict, List, Any, Tuple
import time
from pantheon.utils.log import logger


class SessionContextManager:
    """Manages session-based code context for better completions"""

    def __init__(self):
        self.contexts: Dict[str, str] = {}
        self.standard_imports = """
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import json
import os
import sys
from typing import *
"""

    def get_context(self, session_id: str, current_code: str = "") -> str:
        """Get full context for session"""
        base_context = self.contexts.get(session_id, "")
        return f"{self.standard_imports}\n{base_context}\n{current_code}"

    def update_context(self, session_id: str, executed_code: str):
        """Add executed code to session context"""
        if session_id not in self.contexts:
            self.contexts[session_id] = ""

        self.contexts[session_id] += f"\n{executed_code}"

        # Keep context size reasonable (last 5000 chars)
        if len(self.contexts[session_id]) > 5000:
            self.contexts[session_id] = self.contexts[session_id][-5000:]

    def clear_context(self, session_id: str):
        """Clear session context"""
        self.contexts.pop(session_id, None)


class JediCodeIntelligence:
    """Simplified Jedi-based code intelligence"""

    def __init__(self):
        self.context_manager = SessionContextManager()

    def _convert_cursor_position(self, code: str, cursor_pos: int) -> Tuple[int, int]:
        """Convert string cursor position to line/column for Jedi"""
        if cursor_pos > len(code):
            cursor_pos = len(code)

        lines = code[:cursor_pos].split('\n')
        line = len(lines)  # 1-indexed for Jedi
        column = len(lines[-1]) if lines else 0  # 0-indexed for Jedi
        return line, column

    def _find_function_call_position(self, code: str, cursor_pos: int) -> Tuple[int, int]:
        """Find the position of the function name before the opening parenthesis

        For example:
        - plt.plot(arg1, arg2) -> finds position of 't' in 'plt'
        - range(10) -> finds position of 'e' in 'range'
        """
        if cursor_pos > len(code):
            cursor_pos = len(code)

        # Work backwards from cursor position to find the opening parenthesis
        search_pos = cursor_pos - 1
        while search_pos >= 0 and code[search_pos] != '(':
            search_pos -= 1

        if search_pos < 0:
            # No opening parenthesis found, return original position
            return self._convert_cursor_position(code, cursor_pos)

        # Found opening parenthesis at search_pos
        # Now find the function name before it (skip whitespace)
        func_name_end = search_pos - 1
        while func_name_end >= 0 and code[func_name_end].isspace():
            func_name_end -= 1

        if func_name_end < 0:
            # No function name found, return original position
            return self._convert_cursor_position(code, cursor_pos)

        # Position should be right after the function name for help()
        target_pos = func_name_end + 1
        return self._convert_cursor_position(code, target_pos)

    def _get_jedi_script(self, code: str, cursor_pos: int, session_id: str, context_code: str = "") -> Tuple[Script, int, int]:
        """Create Jedi script with proper context and cursor position"""
        # Ensure cursor_pos is within code bounds
        cursor_pos = min(cursor_pos, len(code))

        full_context = self.context_manager.get_context(session_id, context_code)
        full_code = f"{full_context}\n{code}"

        # Calculate cursor position in full code
        context_lines = full_context.count("\n") + 1  # +1 for the extra newline we added
        line, column = self._convert_cursor_position(code, cursor_pos)

        return Script(code=full_code, path="notebook.py"), context_lines + line, column

    def _format_documentation(self, obj, truncate: int = 0) -> str:
        """Unified documentation formatting"""
        try:
            docstring = obj.docstring() if hasattr(obj, 'docstring') else str(obj)
            if not docstring:
                return "No documentation available"

            if truncate > 0 and len(docstring) > truncate:
                return docstring[:truncate] + "..."
            return docstring
        except:
            return "No documentation available"

    def get_completions(self, code: str, cursor_pos: int, session_id: str, context_code: str = "") -> List[Dict[str, Any]]:
        """Get Jedi completions with rich metadata"""
        try:
            script, line, column = self._get_jedi_script(code, cursor_pos, session_id, context_code)
            jedi_completions = script.complete(line=line, column=column)

            completions = []
            for completion in jedi_completions:
                # Get clean signature for functions/methods
                signature = ""
                try:
                    signatures = completion.get_signatures()
                    if signatures:
                        sig = signatures[0]
                        # Construct proper signature from Jedi signature object
                        if hasattr(sig, 'params') and hasattr(sig, 'name'):
                            param_strs = []
                            for param in sig.params:
                                param_str = param.name
                                if hasattr(param, 'description') and param.description:
                                    desc = param.description
                                    if ': ' in desc:
                                        type_part = desc.split(': ', 1)[1]
                                        if type_part and type_part != param.name:
                                            param_str += f': {type_part}'
                                param_strs.append(param_str)

                            param_list = ', '.join(param_strs)
                            signature = f'({param_list})'  # Only parameters, no function name
                except:
                    pass

                comp_data = {
                    "name": completion.name,
                    "type": completion.type,
                    # Only provide signature for function types
                    **({"signature": signature} if signature and completion.type in ['function', 'method'] else {}),
                }
                completions.append(comp_data)

            return completions[:50]  # Limit for performance

        except Exception as e:
            logger.error(f"Jedi completion failed: {e}")
            return []

    def get_inspection(self, code: str, cursor_pos: int, session_id: str, context_code: str = "") -> Dict[str, Any]:
        """Get Jedi inspection information using simple strategy:
        1. Try get_signatures() first
        2. If signatures found, backtrack column by 1 for help() docstring
        3. If no signatures, use help() directly
        """
        try:
            script, line, column = self._get_jedi_script(code, cursor_pos, session_id, context_code)

            # Step 1: Try to get signatures first
            jedi_signatures = script.get_signatures(line=line, column=column)
            signatures = []

            # Process Jedi signatures
            for sig in jedi_signatures:
                # Clean up signature string - remove "<Signature: index=N " prefix and ">" suffix
                sig_str = str(sig)
                if sig_str.startswith("<Signature: "):
                    # Extract the actual signature part
                    # Find the space after "index=N" and extract everything until the closing ">"
                    space_after_index = sig_str.find(' ', sig_str.find('index='))
                    if space_after_index != -1:
                        clean_sig = sig_str[space_after_index + 1:-1]  # Remove trailing ">"
                    else:
                        clean_sig = sig_str
                else:
                    clean_sig = sig_str

                sig_info = {
                    "signature": clean_sig,
                    "parameters": []
                }

                # Extract parameter information
                for param in sig.params:
                    param_info = {
                        "name": param.name,
                        "type": param.description.replace(f"param {param.name}: ", "") if param.description else "Any",
                        "required": "=..." not in param.description,
                        "description": param.description or ""
                    }
                    sig_info["parameters"].append(param_info)

                signatures.append(sig_info)

            # Step 2: Get documentation using improved backtrack strategy
            if jedi_signatures:
                # We have signatures, find the function name position for help()
                func_line, func_column = self._find_function_call_position(code, cursor_pos)
                try:
                    help_info = script.help(line=func_line, column=func_column)
                    if help_info and help_info[0].name != '(':
                        item = help_info[0]
                        # Use raw=True since we already have signature info
                        docstring = item.docstring(raw=True)
                    else:
                        # Fallback to signature docstring (raw since we have signatures)
                        sig = jedi_signatures[0]
                        item = type('Item', (), {
                            'name': sig.name,
                            'full_name': getattr(sig, 'full_name', sig.name),
                            'type': 'function',
                            'module_name': getattr(sig, 'module_name', 'unknown')
                        })()
                        docstring = sig.docstring(raw=True)
                except Exception:
                    # Fallback to signature docstring (raw since we have signatures)
                    sig = jedi_signatures[0]
                    item = type('Item', (), {
                        'name': sig.name,
                        'full_name': getattr(sig, 'full_name', sig.name),
                        'type': 'function',
                        'module_name': getattr(sig, 'module_name', 'unknown')
                    })()
                    docstring = sig.docstring(raw=True)
            else:
                # Step 3: No signatures, use help() directly
                help_info = script.help(line=line, column=column)
                if not help_info:
                    return {"found": False}

                item = help_info[0]
                docstring = item.docstring()  # Default behavior (includes signatures if available)

            return {
                "found": True,
                "name": item.name,
                "full_name": item.full_name,
                "type": item.type,
                "module": item.module_name,
                "signatures": signatures,
                "docstring": docstring,
            }

        except Exception as e:
            logger.error(f"Jedi inspection failed: {e}")
            return {"found": False, "error": str(e)}



    def update_context(self, session_id: str, executed_code: str):
        """Update session context"""
        self.context_manager.update_context(session_id, executed_code)

    def clear_context(self, session_id: str):
        """Clear session context"""
        self.context_manager.clear_context(session_id)


class EnhancedCompletionService:
    """Simplified Jedi-based completion service"""

    def __init__(self):
        self.jedi_intelligence = JediCodeIntelligence()

    async def get_completions(self, code: str, cursor_pos: int, session_id: str, context_code: str = "") -> Dict[str, Any]:
        """Get completions using pure Jedi approach"""
        start_time = time.time()

        try:
            jedi_completions = self.jedi_intelligence.get_completions(code, cursor_pos, session_id, context_code)

            return {
                "success": True,
                "data": {
                    "items": jedi_completions,
                },
                "metadata": {
                    "source": "jedi",
                    "timing": time.time() - start_time,
                    "count": len(jedi_completions),
                },
            }

        except Exception as e:
            logger.error(f"Jedi completion error: {e}")
            return {
                "success": False,
                "error": str(e),
                "metadata": {"timing": time.time() - start_time},
            }

    async def get_inspection(self, code: str, cursor_pos: int, session_id: str, context_code: str = "") -> Dict[str, Any]:
        """Get inspection info using pure Jedi approach"""
        start_time = time.time()

        try:
            jedi_result = self.jedi_intelligence.get_inspection(code, cursor_pos, session_id, context_code)

            return {
                "success": True,
                "data": jedi_result,
                "metadata": {"source": "jedi", "timing": time.time() - start_time},
            }

        except Exception as e:
            logger.error(f"Jedi inspection error: {e}")
            return {
                "success": False,
                "error": str(e),
                "metadata": {"timing": time.time() - start_time},
            }

    def update_session_context(self, session_id: str, executed_code: str):
        """Update context for a session after code execution"""
        self.jedi_intelligence.update_context(session_id, executed_code)

    def clear_session_context(self, session_id: str):
        """Clear context for a session (e.g., on kernel restart)"""
        self.jedi_intelligence.clear_context(session_id)

