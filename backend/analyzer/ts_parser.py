"""Tree-sitter based universal code parser.

Replaces the existing language-specific analyzers with a unified
tree-sitter approach that supports 100+ languages via grammar libraries.
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore", category=FutureWarning, module="tree_sitter")

from log import get_logger; logger = get_logger(__name__)

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ParsedSymbol:
    """A symbol (function, class, etc.) extracted by tree-sitter."""
    name: str
    kind: str  # "function", "class", "method", "variable", "import", etc.
    file_path: str
    line_start: int
    line_end: int
    docstring: str = ""
    signature: str = ""  # full function/class signature
    parent_class: str = ""  # for methods: the enclosing class name
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedRelation:
    """A relationship between two symbols."""
    source: str  # symbol id
    target: str  # symbol id
    kind: str  # "calls", "imports", "inherits", "contains", etc.
    line_number: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class TreeSitterParser:
    """Unified tree-sitter parser for multiple languages.

    Uses tree-sitter's language grammars to extract functions, classes,
    imports, calls, and other structural elements from source code.

    Language support is determined by available tree-sitter grammar
    shared libraries (.so/.dylib files).
    """

    # File extension → tree-sitter language name mapping
    EXT_TO_LANG: dict[str, str] = {
        ".py": "python",
        ".pyi": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".go": "go",
        ".rs": "rust",
        ".cpp": "cpp",
        ".cc": "cpp",
        ".cxx": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".java": "java",
        ".rb": "ruby",
        ".php": "php",
        ".swift": "swift",
        ".kt": "kotlin",
        ".scala": "scala",
        ".sh": "bash",
        ".bash": "bash",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".json": "json",
        ".toml": "toml",
        ".sql": "sql",
        ".css": "css",
        ".scss": "css",
        ".html": "html",
        ".md": "markdown",
        ".mdx": "markdown",
    }

    # Query patterns per language for extracting symbols
    QUERIES: dict[str, dict[str, str]] = {
        "python": {
            "functions": """
                (function_definition
                    name: (identifier) @name
                ) @func
            """,
            "classes": """
                (class_definition
                    name: (identifier) @name
                ) @class
            """,
            "imports": """
                (import_statement
                    name: (dotted_name) @module
                )
                (import_from_statement
                    module_name: (dotted_name) @module
                    name: (dotted_name) @name
                )
            """,
            "calls": """
                (call
                    function: (identifier) @call_name
                )
                (call
                    function: (attribute
                        object: (_) @obj
                        attribute: (identifier) @call_name
                    )
                )
            """,
        },
        "javascript": {
            "functions": """
                (function_declaration
                    name: (identifier) @name
                    parameters: (formal_parameters) @params
                    body: (statement_block)
                ) @func
                (arrow_function
                    parameters: (formal_parameters) @params
                    body: (statement_block)
                ) @func
            """,
            "classes": """
                (class_declaration
                    name: (identifier) @name
                    body: (class_body)
                ) @class
            """,
            "imports": """
                (import_statement
                    source: (string) @module
                )
                (import_statement
                    import_clause: (named_imports) @name
                    source: (string) @module
                )
            """,
            "calls": """
                (call_expression
                    function: (identifier) @call_name
                )
                (call_expression
                    function: (member_expression
                        property: (property_identifier) @call_name
                    )
                )
            """,
        },
        "typescript": {
            "functions": """
                (function_declaration
                    name: (identifier) @name
                    parameters: (formal_parameters) @params
                    body: (statement_block)
                ) @func
                (method_definition
                    name: (property_identifier) @name
                    parameters: (formal_parameters) @params
                    body: (statement_block)
                ) @func
                (arrow_function
                    parameters: (formal_parameters) @params
                    body: (statement_block)
                ) @func
            """,
            "classes": """
                (class_declaration
                    name: (type_identifier) @name
                    body: (class_body)
                ) @class
            """,
            "imports": """
                (import_statement
                    source: (string) @module
                )
            """,
            "calls": """
                (call_expression
                    function: (identifier) @call_name
                )
                (call_expression
                    function: (member_expression
                        property: (property_identifier) @call_name
                    )
                )
            """,
        },
        "go": {
            "functions": """
                (function_declaration
                    name: (identifier) @name
                    parameters: (parameter_list) @params
                    body: (block)
                ) @func
                (method_declaration
                    name: (field_identifier) @name
                    parameters: (parameter_list) @params
                    body: (block)
                ) @func
            """,
            "imports": """
                (import_declaration
                    (import_spec
                        path: (interpreted_string_literal) @module
                    )
                )
            """,
            "calls": """
                (call_expression
                    function: (identifier) @call_name
                )
                (call_expression
                    function: (selector_expression
                        field: (field_identifier) @call_name
                    )
                )
            """,
        },
        "rust": {
            "functions": """
                (function_item
                    name: (identifier) @name
                    parameters: (parameters) @params
                    body: (block)
                ) @func
            """,
            "imports": """
                (use_declaration
                    argument: (_) @module
                )
            """,
            "calls": """
                (call_expression
                    function: (identifier) @call_name
                )
            """,
        },
    }

    def __init__(self) -> None:
        self._language_cache: dict[str, Any] = {}
        self._parser = None
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """Lazy-init tree-sitter. Grammar libraries are loaded on demand."""
        if self._initialized:
            return
        try:
            import tree_sitter
            self._tree_sitter = tree_sitter
            self._initialized = True
        except ImportError:
            raise ImportError(
                "tree-sitter is not installed. Run: pip install tree-sitter"
            )

    def _get_language(self, lang_name: str) -> Any:
        """Load a tree-sitter language grammar via tree-sitter-languages."""
        if lang_name in self._language_cache:
            return self._language_cache[lang_name]

        self._ensure_initialized()

        try:
            import tree_sitter_languages
            lang = tree_sitter_languages.get_language(lang_name)
            self._language_cache[lang_name] = lang
            return lang
        except (ImportError, Exception) as e:
            logger.warning(f"tree-sitter grammar for '{lang_name}' not available: {e}")
            return None

    def _get_parser(self, lang_name: str) -> Any | None:
        """Get or create a tree-sitter parser for a specific language."""
        try:
            import tree_sitter_languages
            return tree_sitter_languages.get_parser(lang_name)
        except (ImportError, Exception) as e:
            logger.warning(f"tree-sitter parser for '{lang_name}' not available: {e}")
            return None

    def parse_file(self, file_path: str, content: str = "") -> tuple[
        list[ParsedSymbol], list[ParsedRelation]
    ]:
        """Parse a single file and extract symbols and relations.

        Args:
            file_path: Path to the source file.
            content: File content (if empty, reads from disk).

        Returns:
            Tuple of (symbols, relations).
        """
        ext = Path(file_path).suffix.lower()
        lang_name = self.EXT_TO_LANG.get(ext)
        if lang_name is None:
            return [], []

        parser = self._get_parser(lang_name)
        if parser is None:
            return [], []

        # Read content if not provided
        if not content:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                return [], []

        if not content.strip():
            return [], []

        try:
            tree = parser.parse(bytes(content, "utf-8"))
        except Exception:
            return [], []

        symbols: list[ParsedSymbol] = []
        relations: list[ParsedRelation] = []

        # Extract classes FIRST (needed for method classification)
        class_query = self.QUERIES.get(lang_name, {}).get("classes", "")
        if class_query:
            symbols.extend(self._query_symbols(
                lang_name, file_path, content, tree, class_query, "class"
            ))

        # Extract functions
        funcs_query = self.QUERIES.get(lang_name, {}).get("functions", "")
        if funcs_query:
            symbols.extend(self._query_symbols(
                lang_name, file_path, content, tree, funcs_query, "function"
            ))

        # ── Classify methods: functions inside classes → kind="method" ──
        class_ranges = [(s, s.line_start, s.line_end) for s in symbols if s.kind == "class"]
        for sym in symbols:
            if sym.kind != "function":
                continue
            for cls_sym, cls_start, cls_end in class_ranges:
                if cls_start <= sym.line_start <= cls_end:
                    sym.kind = "method"
                    sym.parent_class = cls_sym.name
                    break

        # Extract imports
        import_query = self.QUERIES.get(lang_name, {}).get("imports", "")
        if import_query:
            relations.extend(self._query_imports(
                lang_name, file_path, content, tree, import_query
            ))

        # Extract calls
        calls_query = self.QUERIES.get(lang_name, {}).get("calls", "")
        if calls_query:
            relations.extend(self._query_calls(
                lang_name, file_path, content, tree, calls_query
            ))

        return symbols, relations

    def _query_symbols(
        self,
        lang_name: str,
        file_path: str,
        content: str,
        tree: Any,
        query_str: str,
        default_kind: str,
    ) -> list[ParsedSymbol]:
        """Run a tree-sitter query and extract ParsedSymbols."""
        symbols: list[ParsedSymbol] = []
        try:
            import tree_sitter_languages
            lang = tree_sitter_languages.get_language(lang_name)
            query = lang.query(query_str)
            captures = query.captures(tree.root_node)

            # Group captures by node
            seen_lines: set[int] = set()
            current_func: dict[str, Any] = {}

            for node, tag in captures:
                line = node.start_point[0] + 1

                if tag == "func" or tag == "class" or tag == "func_def":
                    # Flush previous
                    if current_func and "name" in current_func:
                        symbols.append(self._make_symbol(
                            file_path, content, current_func, default_kind
                        ))
                    current_func = {
                        "line_start": line,
                        "line_end": node.end_point[0] + 1,
                    }
                elif tag == "name" and "name" not in current_func:
                    current_func["name"] = self._node_text(node, content)
                elif tag == "params":
                    current_func["params"] = self._node_text(node, content)

            # Flush last
            if current_func and "name" in current_func:
                symbols.append(self._make_symbol(
                    file_path, content, current_func, default_kind
                ))

        except Exception:
            pass  # Query failed for this language/file — skip

        return symbols

    def _make_symbol(
        self,
        file_path: str,
        content: str,
        info: dict,
        kind: str,
    ) -> ParsedSymbol:
        """Create a ParsedSymbol from extraction info."""
        name = info.get("name", "unknown")
        line_start = info.get("line_start", 0)
        line_end = info.get("line_end", line_start)
        params = info.get("params", "")

        # Build signature
        if params:
            signature = f"{name}({params})"
        else:
            signature = name

        # Try to extract docstring
        docstring = self._extract_docstring(content, line_start, line_end)

        return ParsedSymbol(
            name=name,
            kind=kind,
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
            docstring=docstring,
            signature=signature,
        )

    def _extract_docstring(
        self, content: str, line_start: int, line_end: int
    ) -> str:
        """Extract docstring/comment from the beginning of a function body."""
        lines = content.split("\n")
        # Look at lines after the function definition line
        for i in range(line_start, min(line_start + 10, len(lines))):
            line = lines[i].strip()
            if not line:
                continue
            if line.startswith(('"""', "'''", '"""', '"""')):
                # Python docstring
                doc_lines = []
                delimiter = line[:3]
                i += 1
                while i < len(lines):
                    doc_lines.append(lines[i].strip())
                    if delimiter in lines[i]:
                        break
                    i += 1
                return "\n".join(doc_lines).strip(delimiter).strip()
            elif line.startswith("//") or line.startswith("/**"):
                # JS/TS/Go/Rust doc comment
                return line.lstrip("/ *")
            elif line.startswith("#"):
                # Python/Shell comment
                return line.lstrip("# ")
            else:
                break
        return ""

    def _query_imports(
        self,
        lang_name: str,
        file_path: str,
        content: str,
        tree: Any,
        query_str: str,
    ) -> list[ParsedRelation]:
        """Extract import relations."""
        relations: list[ParsedRelation] = []
        try:
            import tree_sitter_languages
            lang = tree_sitter_languages.get_language(lang_name)
            query = lang.query(query_str)
            captures = query.captures(tree.root_node)

            for node, tag in captures:
                if tag == "module":
                    module_name = self._node_text(node, content).strip('"\'').strip()
                    if module_name:
                        relations.append(ParsedRelation(
                            source=file_path,
                            target=f"module:{module_name}",
                            kind="imports",
                            line_number=node.start_point[0] + 1,
                        ))

        except Exception:
            pass

        return relations

    def _query_calls(
        self,
        lang_name: str,
        file_path: str,
        content: str,
        tree: Any,
        query_str: str,
    ) -> list[ParsedRelation]:
        """Extract function call relations."""
        relations: list[ParsedRelation] = []
        seen: set[str] = set()
        try:
            import tree_sitter_languages
            lang = tree_sitter_languages.get_language(lang_name)
            query = lang.query(query_str)
            captures = query.captures(tree.root_node)

            for node, tag in captures:
                if tag == "call_name":
                    call_name = self._node_text(node, content).strip()
                    # Filter: only accept valid identifier-like names
                    if (call_name and call_name not in ("require", "super")
                            and not any(c in call_name for c in '("{[<\'\n ')
                            and len(call_name) >= 2 and len(call_name) <= 80
                            and not call_name[0].isdigit()
                            and not call_name.startswith('__')):
                        key = f"{file_path}:{call_name}"
                        if key not in seen:
                            seen.add(key)
                            relations.append(ParsedRelation(
                                source=file_path,
                                target=call_name,
                                kind="calls",
                                line_number=node.start_point[0] + 1,
                            ))

        except Exception:
            pass

        return relations

    @staticmethod
    def _node_text(node: Any, content: str) -> str:
        """Get the text content of a tree-sitter node (correct UTF-8 handling)."""
        try:
            content_bytes = content.encode('utf-8')
            return content_bytes[node.start_byte:node.end_byte].decode('utf-8')
        except (IndexError, AttributeError, UnicodeDecodeError):
            return ""


# Global singleton
_parser_instance: TreeSitterParser | None = None


def get_parser() -> TreeSitterParser:
    """Get or create the global tree-sitter parser instance."""
    global _parser_instance
    if _parser_instance is None:
        _parser_instance = TreeSitterParser()
    return _parser_instance
