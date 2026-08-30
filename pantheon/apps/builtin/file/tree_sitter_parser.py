"""Tree-sitter based code parser for extracting file outlines and code items.

Supports multiple languages through tree-sitter grammars.
Languages are lazy-loaded on first use to avoid import overhead.
"""

from pathlib import Path
from dataclasses import dataclass, field

# Language extension to tree-sitter language mapping
LANGUAGE_MAP = {
    ".py": "python",
    ".pyw": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}

# Cached language parsers
_parsers: dict = {}


@dataclass
class SymbolInfo:
    """Information about a code symbol (class, function, etc.)."""
    
    name: str
    kind: str  # "class", "function", "method"
    start_line: int
    end_line: int
    signature: str = ""
    docstring: str = ""
    children: list["SymbolInfo"] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        result = {
            "name": self.name,
            "kind": self.kind,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }
        if self.signature:
            result["signature"] = self.signature
        if self.docstring:
            result["docstring"] = self.docstring[:100] + "..." if len(self.docstring) > 100 else self.docstring
        if self.children:
            result["children"] = [c.to_dict() for c in self.children]
        return result


def _get_parser(lang: str):
    """Get or create a parser for the given language.
    
    Lazy-loads tree-sitter and language grammars on first use.
    """
    if lang in _parsers:
        return _parsers[lang]
    
    try:
        import tree_sitter
    except ImportError:
        raise ImportError(
            "tree-sitter is required for code outline tools. "
            "Install with: pip install tree-sitter tree-sitter-python tree-sitter-javascript"
        )
    
    # Load language grammar
    if lang == "python":
        try:
            import tree_sitter_python as ts_python
            language = tree_sitter.Language(ts_python.language())
        except ImportError:
            raise ImportError("tree-sitter-python not installed")
    elif lang in ("javascript", "typescript"):
        try:
            import tree_sitter_javascript as ts_js
            language = tree_sitter.Language(ts_js.language())
        except ImportError:
            raise ImportError("tree-sitter-javascript not installed")
    else:
        raise ValueError(f"Unsupported language: {lang}")
    
    parser = tree_sitter.Parser(language)
    _parsers[lang] = parser
    return parser


def detect_language(file_path: str | Path) -> str | None:
    """Detect language from file extension."""
    ext = Path(file_path).suffix.lower()
    return LANGUAGE_MAP.get(ext)


def get_file_outline(file_path: str | Path) -> dict:
    """Parse a file and extract its symbol outline.
    
    Args:
        file_path: Path to the source file.
        
    Returns:
        dict: {
            "success": bool,
            "file": str,
            "language": str,
            "total_lines": int,
            "symbols": [SymbolInfo.to_dict(), ...]
        }
    """
    file_path = Path(file_path)
    
    if not file_path.exists():
        return {"success": False, "error": "File does not exist"}
    
    if not file_path.is_file():
        return {"success": False, "error": "Path is not a file"}
    
    lang = detect_language(file_path)
    if not lang:
        return {"success": False, "error": f"Unsupported file type: {file_path.suffix}"}
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        source_bytes = source.encode("utf-8")
        source_lines = source.splitlines()
        total_lines = len(source_lines)
    except UnicodeDecodeError:
        return {"success": False, "error": "File is not a valid text file"}
    except Exception as e:
        return {"success": False, "error": str(e)}
    
    try:
        parser = _get_parser(lang)
    except ImportError as e:
        return {"success": False, "error": str(e)}
    
    tree = parser.parse(source_bytes)
    
    # Extract symbols based on language
    if lang == "python":
        symbols = _extract_python_symbols(tree.root_node, source_bytes, source_lines)
    else:
        symbols = _extract_js_symbols(tree.root_node, source_bytes, source_lines)
    
    return {
        "success": True,
        "file": str(file_path),
        "language": lang,
        "total_lines": total_lines,
        "symbols": [s.to_dict() for s in symbols],
    }


def _extract_python_symbols(node, source_bytes: bytes, source_lines: list[str]) -> list[SymbolInfo]:
    """Extract symbols from Python AST."""
    symbols = []
    
    for child in node.children:
        if child.type == "class_definition":
            symbols.append(_parse_python_class(child, source_bytes, source_lines))
        elif child.type == "function_definition":
            symbols.append(_parse_python_function(child, source_bytes, source_lines))
        elif child.type == "decorated_definition":
            # Handle decorated classes/functions
            for subchild in child.children:
                if subchild.type == "class_definition":
                    symbols.append(_parse_python_class(subchild, source_bytes, source_lines))
                elif subchild.type == "function_definition":
                    symbols.append(_parse_python_function(subchild, source_bytes, source_lines))
    
    return symbols


def _parse_python_class(node, source_bytes: bytes, source_lines: list[str]) -> SymbolInfo:
    """Parse a Python class node."""
    name = ""
    signature = ""
    docstring = ""
    children = []
    
    for child in node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8")
        elif child.type == "argument_list":
            signature = child.text.decode("utf-8")
        elif child.type == "block":
            # Look for docstring and methods
            for block_child in child.children:
                if block_child.type == "expression_statement":
                    # Check for docstring
                    for expr_child in block_child.children:
                        if expr_child.type == "string":
                            docstring = expr_child.text.decode("utf-8").strip("\"'")
                            break
                elif block_child.type == "function_definition":
                    children.append(_parse_python_function(block_child, source_bytes, source_lines, is_method=True))
                elif block_child.type == "decorated_definition":
                    for subchild in block_child.children:
                        if subchild.type == "function_definition":
                            children.append(_parse_python_function(subchild, source_bytes, source_lines, is_method=True))
    
    return SymbolInfo(
        name=name,
        kind="class",
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        signature=f"class {name}{signature}" if signature else f"class {name}",
        docstring=docstring,
        children=children,
    )


def _parse_python_function(node, source_bytes: bytes, source_lines: list[str], is_method: bool = False) -> SymbolInfo:
    """Parse a Python function node."""
    name = ""
    params = ""
    docstring = ""
    
    for child in node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8")
        elif child.type == "parameters":
            params = child.text.decode("utf-8")
        elif child.type == "block":
            # Look for docstring
            for block_child in child.children:
                if block_child.type == "expression_statement":
                    for expr_child in block_child.children:
                        if expr_child.type == "string":
                            docstring = expr_child.text.decode("utf-8").strip("\"'")
                            break
                    break
    
    return SymbolInfo(
        name=name,
        kind="method" if is_method else "function",
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        signature=f"def {name}{params}",
        docstring=docstring,
    )


def _extract_js_symbols(node, source_bytes: bytes, source_lines: list[str]) -> list[SymbolInfo]:
    """Extract symbols from JavaScript/TypeScript AST."""
    symbols = []
    
    for child in node.children:
        if child.type == "class_declaration":
            symbols.append(_parse_js_class(child, source_bytes, source_lines))
        elif child.type == "function_declaration":
            symbols.append(_parse_js_function(child, source_bytes, source_lines))
        elif child.type == "lexical_declaration":
            # Handle const/let function expressions
            for subchild in child.children:
                if subchild.type == "variable_declarator":
                    func = _try_parse_js_arrow_function(subchild, source_bytes, source_lines)
                    if func:
                        symbols.append(func)
        elif child.type == "export_statement":
            # Handle exported declarations
            for subchild in child.children:
                if subchild.type == "class_declaration":
                    symbols.append(_parse_js_class(subchild, source_bytes, source_lines))
                elif subchild.type == "function_declaration":
                    symbols.append(_parse_js_function(subchild, source_bytes, source_lines))
    
    return symbols


def _parse_js_class(node, source_bytes: bytes, source_lines: list[str]) -> SymbolInfo:
    """Parse a JavaScript class node."""
    name = ""
    children = []
    
    for child in node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8")
        elif child.type == "class_body":
            for body_child in child.children:
                if body_child.type == "method_definition":
                    children.append(_parse_js_method(body_child, source_bytes, source_lines))
    
    return SymbolInfo(
        name=name,
        kind="class",
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        signature=f"class {name}",
        children=children,
    )


def _parse_js_function(node, source_bytes: bytes, source_lines: list[str]) -> SymbolInfo:
    """Parse a JavaScript function declaration."""
    name = ""
    params = ""
    
    for child in node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8")
        elif child.type == "formal_parameters":
            params = child.text.decode("utf-8")
    
    return SymbolInfo(
        name=name,
        kind="function",
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        signature=f"function {name}{params}",
    )


def _parse_js_method(node, source_bytes: bytes, source_lines: list[str]) -> SymbolInfo:
    """Parse a JavaScript class method."""
    name = ""
    params = ""
    
    for child in node.children:
        if child.type == "property_identifier":
            name = child.text.decode("utf-8")
        elif child.type == "formal_parameters":
            params = child.text.decode("utf-8")
    
    return SymbolInfo(
        name=name,
        kind="method",
        start_line=node.start_point[0] + 1,
        end_line=node.end_point[0] + 1,
        signature=f"{name}{params}",
    )


def _try_parse_js_arrow_function(node, source_bytes: bytes, source_lines: list[str]) -> SymbolInfo | None:
    """Try to parse a variable declarator as an arrow function."""
    name = ""
    params = ""
    
    for child in node.children:
        if child.type == "identifier":
            name = child.text.decode("utf-8")
        elif child.type == "arrow_function":
            for arrow_child in child.children:
                if arrow_child.type == "formal_parameters":
                    params = arrow_child.text.decode("utf-8")
            return SymbolInfo(
                name=name,
                kind="function",
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                signature=f"const {name} = {params} =>",
            )
    
    return None


def get_code_item(file_path: str | Path, node_path: str) -> dict:
    """Extract source code for a specific symbol.
    
    Args:
        file_path: Path to the source file.
        node_path: Qualified name of the symbol (e.g., "MyClass.my_method").
        
    Returns:
        dict: {
            "success": bool,
            "name": str,
            "kind": str,
            "start_line": int,
            "end_line": int,
            "source": str
        }
    """
    file_path = Path(file_path)
    
    if not file_path.exists():
        return {"success": False, "error": "File does not exist"}
    
    # Get outline first
    outline = get_file_outline(file_path)
    if not outline["success"]:
        return outline
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source_lines = f.readlines()
    except Exception as e:
        return {"success": False, "error": str(e)}
    
    # Parse node path
    path_parts = node_path.split(".")
    
    # Find symbol in outline
    symbols = outline["symbols"]
    current_symbol = None
    
    for part in path_parts:
        found = False
        for sym in symbols:
            if sym["name"] == part:
                current_symbol = sym
                symbols = sym.get("children", [])
                found = True
                break
        if not found:
            return {"success": False, "error": f"Symbol not found: {node_path}"}
    
    if current_symbol is None:
        return {"success": False, "error": f"Symbol not found: {node_path}"}
    
    # Extract source code
    start = current_symbol["start_line"] - 1
    end = current_symbol["end_line"]
    source = "".join(source_lines[start:end])
    
    return {
        "success": True,
        "name": current_symbol["name"],
        "kind": current_symbol["kind"],
        "start_line": current_symbol["start_line"],
        "end_line": current_symbol["end_line"],
        "source": source,
    }
