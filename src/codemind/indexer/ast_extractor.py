"""
Multi-language AST extraction using Tree-Sitter.

Extracts code structure: classes, functions, imports, calls.
Supports 20+ programming languages via lazy-loaded parsers.
"""

import importlib
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter import Language, Parser


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Symbol:
    """Represents a code symbol (class, function, etc.)."""

    name: str
    type: str  # "class", "function", "method", "interface", "struct", "trait", "enum"
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    docstring: str | None = None
    parent: str | None = None  # For methods, the class name
    bases: list[str] = field(default_factory=list)  # Base classes / interfaces
    parameters: list[str] = field(default_factory=list)  # Function parameters


@dataclass
class Import:
    """Represents an import statement."""

    module: str
    names: list[str]  # Imported names
    line: int
    is_relative: bool = False  # Relative import (e.g., from . import x)


@dataclass
class ASTExtractionResult:
    """Result of AST extraction for a file."""

    symbols: list[Symbol]
    imports: list[Import]
    language: str
    success: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Language config tables
# ---------------------------------------------------------------------------

# File extension → tree-sitter language name
LANGUAGE_MAP: dict[str, str] = {
    # Python
    ".py": "python", ".pyi": "python",
    # JavaScript / TypeScript
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "typescript",
    # Go
    ".go": "go",
    # Rust
    ".rs": "rust",
    # Java / Kotlin / Scala
    ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".scala": "scala",
    # C / C++
    ".c": "c", ".h": "c",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hxx": "cpp",
    # C# / .NET
    ".cs": "c_sharp",
    # Ruby
    ".rb": "ruby",
    # PHP
    ".php": "php",
    # Swift
    ".swift": "swift",
    # Shell
    ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    # Web / Markup
    ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "css",
    # Data / Config
    ".json": "json",
    ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml",
    ".sql": "sql",
    ".md": "markdown",
    # DevOps
    ".tf": "hcl", ".hcl": "hcl",
}

# tree-sitter module name mapping (some need special names)
_MODULE_MAP: dict[str, str] = {
    "python": "tree_sitter_python",
    "javascript": "tree_sitter_javascript",
    "typescript": "tree_sitter_typescript",
    "go": "tree_sitter_go",
    "java": "tree_sitter_java",
    "rust": "tree_sitter_rust",
    "c": "tree_sitter_c",
    "cpp": "tree_sitter_cpp",
    "c_sharp": "tree_sitter_c_sharp",
    "ruby": "tree_sitter_ruby",
    "php": "tree_sitter_php",
    "swift": "tree_sitter_swift",
    "kotlin": "tree_sitter_kotlin",
    "scala": "tree_sitter_scala",
    "bash": "tree_sitter_bash",
    "html": "tree_sitter_html",
    "css": "tree_sitter_css",
    "json": "tree_sitter_json",
    "yaml": "tree_sitter_yaml",
    "toml": "tree_sitter_toml",
    "sql": "tree_sitter_sql",
    "markdown": "tree_sitter_markdown",
    "hcl": "tree_sitter_hcl",
}

# Per-language AST node types for functions
FUNCTION_NODE_TYPES: dict[str, list[str]] = {
    "python": ["function_definition"],
    "javascript": ["function_declaration", "arrow_function", "method_definition",
                    "generator_function_declaration"],
    "typescript": ["function_declaration", "arrow_function", "method_definition",
                    "generator_function_declaration"],
    "go": ["function_declaration", "method_declaration"],
    "java": ["method_declaration", "constructor_declaration"],
    "rust": ["function_item"],
    "c": ["function_definition"],
    "cpp": ["function_definition"],
    "c_sharp": ["method_declaration", "constructor_declaration"],
    "ruby": ["method", "singleton_method"],
    "php": ["function_definition", "method_declaration"],
    "swift": ["function_declaration"],
    "kotlin": ["function_declaration"],
    "scala": ["function_definition", "def_definition"],
    "bash": ["function_definition"],
}

# Per-language AST node types for classes / types
CLASS_NODE_TYPES: dict[str, list[str]] = {
    "python": ["class_definition"],
    "javascript": ["class_declaration"],
    "typescript": ["class_declaration", "interface_declaration", "type_alias_declaration"],
    "go": ["type_declaration"],
    "java": ["class_declaration", "interface_declaration", "enum_declaration",
             "annotation_type_declaration"],
    "rust": ["struct_item", "enum_item", "impl_item", "trait_item"],
    "c": ["struct_specifier", "enum_specifier"],
    "cpp": ["class_specifier", "struct_specifier", "enum_specifier"],
    "c_sharp": ["class_declaration", "interface_declaration", "struct_declaration",
                "enum_declaration"],
    "ruby": ["class", "module"],
    "php": ["class_declaration", "interface_declaration", "trait_declaration"],
    "swift": ["class_declaration", "struct_declaration", "protocol_declaration",
              "enum_declaration"],
    "kotlin": ["class_declaration", "object_declaration", "interface_declaration"],
    "scala": ["class_definition", "trait_definition", "object_definition"],
}

# Per-language AST node types for imports
IMPORT_NODE_TYPES: dict[str, list[str]] = {
    "python": ["import_statement", "import_from_statement"],
    "javascript": ["import_statement"],
    "typescript": ["import_statement"],
    "go": ["import_declaration"],
    "java": ["import_declaration"],
    "rust": ["use_declaration"],
    "c_sharp": ["using_directive"],
    "ruby": ["call"],  # require / require_relative
    "php": ["namespace_use_declaration"],
    "swift": ["import_declaration"],
    "kotlin": ["import_header"],
    "scala": ["import_declaration"],
}


# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------

class ASTExtractor:
    """Multi-language AST extractor using Tree-Sitter.
    
    Parsers are lazy-loaded: only initialized when a language is first used.
    Gracefully degrades for unsupported languages.
    """

    def __init__(self):
        """Initialize AST extractor. Parsers are loaded lazily."""
        self._parsers: dict[str, Parser] = {}
        self._failed_languages: set[str] = set()

    # -- Public API ---------------------------------------------------------

    def detect_language(self, file_path: Path) -> str | None:
        """Detect programming language from file extension."""
        return LANGUAGE_MAP.get(file_path.suffix.lower())

    def extract(self, file_path: Path, language: str | None = None) -> ASTExtractionResult:
        """
        Extract AST information from any supported language.

        Args:
            file_path: Path to source file
            language: Programming language (auto-detected if None)

        Returns:
            ASTExtractionResult with extracted symbols and imports
        """
        if language is None:
            language = self.detect_language(file_path)
        if not language:
            return ASTExtractionResult(
                symbols=[], imports=[], language="unknown", success=True,
                error="Could not detect language"
            )

        try:
            parser = self._get_parser(language)
            if not parser:
                return ASTExtractionResult(
                    symbols=[], imports=[], language=language, success=True,
                    error=f"No parser available for '{language}'"
                )

            with open(file_path, "rb") as f:
                content = f.read()

            tree = parser.parse(content)

            symbols = self._extract_symbols(tree.root_node, content, language)
            imports = self._extract_imports(tree.root_node, content, language)

            return ASTExtractionResult(
                symbols=symbols, imports=imports, language=language, success=True
            )

        except Exception as e:
            return ASTExtractionResult(
                symbols=[], imports=[], language=language, success=False, error=str(e)
            )

    # -- Parser management --------------------------------------------------

    def _get_parser(self, language: str) -> Parser | None:
        """Get or create parser for language (lazy)."""
        if language in self._failed_languages:
            return None

        if language not in self._parsers:
            try:
                module_name = _MODULE_MAP.get(language, f"tree_sitter_{language}")
                lang_mod = importlib.import_module(module_name)

                # Some modules expose language() directly, others via submodule
                if hasattr(lang_mod, "language"):
                    lang_obj = Language(lang_mod.language())
                else:
                    # Try typescript submodule pattern
                    if language == "typescript":
                        ts_mod = importlib.import_module(f"{module_name}.typescript")
                        lang_obj = Language(ts_mod.language())
                    else:
                        self._failed_languages.add(language)
                        return None

                parser = Parser(lang_obj)
                self._parsers[language] = parser
            except (ImportError, AttributeError, Exception):
                self._failed_languages.add(language)
                return None

        return self._parsers.get(language)

    # -- Generic symbol extraction ------------------------------------------

    def _extract_symbols(self, root_node, content: bytes, language: str) -> list[Symbol]:
        """Extract all symbols (classes, functions, methods) from AST."""
        symbols: list[Symbol] = []
        func_types = FUNCTION_NODE_TYPES.get(language, [])
        class_types = CLASS_NODE_TYPES.get(language, [])

        self._walk_for_symbols(root_node, content, language, func_types, class_types, symbols, parent=None)
        return symbols

    def _walk_for_symbols(
        self, node, content: bytes, language: str,
        func_types: list[str], class_types: list[str],
        symbols: list[Symbol], parent: str | None
    ):
        """Recursively walk AST and extract symbols."""
        for child in node.children:
            if child.type in func_types:
                sym = self._node_to_symbol(child, content, language,
                                            sym_type="method" if parent else "function",
                                            parent=parent)
                if sym:
                    symbols.append(sym)

            elif child.type in class_types:
                sym = self._node_to_symbol(child, content, language,
                                            sym_type=self._classify_class_type(child.type),
                                            parent=parent)
                if sym:
                    symbols.append(sym)
                    # Recurse into class body for methods
                    body = self._find_body(child, language)
                    if body:
                        self._walk_for_symbols(
                            body, content, language, func_types, class_types,
                            symbols, parent=sym.name
                        )
            else:
                # Continue walking for nested definitions (e.g., Go type blocks)
                if child.child_count > 0 and child.type not in func_types:
                    self._walk_for_symbols(
                        child, content, language, func_types, class_types,
                        symbols, parent=parent
                    )

    def _node_to_symbol(
        self, node, content: bytes, language: str,
        sym_type: str, parent: str | None
    ) -> Symbol | None:
        """Convert a tree-sitter node to a Symbol."""
        name = self._extract_name(node, content, language)
        if not name:
            return None

        docstring = self._extract_docstring(node, content, language)
        bases = self._extract_bases(node, content, language) if "class" in sym_type or sym_type in ("struct", "interface", "trait") else []
        params = self._extract_parameters(node, content, language) if sym_type in ("function", "method") else []

        return Symbol(
            name=name,
            type=sym_type,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_byte=node.start_byte,
            end_byte=node.end_byte,
            docstring=docstring,
            parent=parent,
            bases=bases,
            parameters=params,
        )

    def _classify_class_type(self, node_type: str) -> str:
        """Map tree-sitter class node type to our type label."""
        mapping = {
            "class_definition": "class", "class_declaration": "class", "class_specifier": "class",
            "interface_declaration": "interface",
            "struct_specifier": "struct", "struct_item": "struct", "struct_declaration": "struct",
            "enum_specifier": "enum", "enum_item": "enum", "enum_declaration": "enum",
            "trait_item": "trait", "trait_definition": "trait", "protocol_declaration": "trait",
            "impl_item": "impl",
            "type_declaration": "type", "type_alias_declaration": "type",
            "module": "module", "object_declaration": "object", "object_definition": "object",
        }
        return mapping.get(node_type, "class")

    # -- Name extraction (per-language) ------------------------------------

    def _extract_name(self, node, content: bytes, language: str) -> str | None:
        """Extract the name of a symbol from its AST node."""
        # Most languages: child_by_field_name("name")
        name_node = node.child_by_field_name("name")
        if name_node:
            return content[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")

        # Fallback: look for identifier child
        for child in node.children:
            if child.type in ("identifier", "type_identifier", "constant"):
                return content[child.start_byte:child.end_byte].decode("utf-8", errors="replace")

        return None

    # -- Docstring extraction -----------------------------------------------

    def _extract_docstring(self, node, content: bytes, language: str) -> str | None:
        """Extract docstring from a symbol node."""
        body = self._find_body(node, language)
        if not body or not body.children:
            return None

        first_stmt = body.children[0]

        # Python: triple-quoted strings
        if language == "python":
            if first_stmt.type == "expression_statement" and first_stmt.children:
                expr = first_stmt.children[0]
                if expr.type == "string":
                    return content[expr.start_byte:expr.end_byte].decode("utf-8", errors="replace").strip("\"'")

        # JS/TS/Java/C#: look for comment before the node
        if language in ("javascript", "typescript", "java", "c_sharp", "kotlin", "scala", "go", "rust", "cpp"):
            # Check previous sibling for doc comment
            prev = node.prev_sibling
            if prev and prev.type in ("comment", "block_comment", "line_comment"):
                comment_text = content[prev.start_byte:prev.end_byte].decode("utf-8", errors="replace")
                if comment_text.startswith("/**") or comment_text.startswith("///") or comment_text.startswith("//!"):
                    return comment_text.strip("/* \n\t/!")

        return None

    # -- Base class extraction ----------------------------------------------

    def _extract_bases(self, node, content: bytes, language: str) -> list[str]:
        """Extract base classes / interfaces."""
        bases = []

        if language == "python":
            # class Foo(Bar, Baz):
            arg_list = node.child_by_field_name("superclasses")
            if arg_list:
                for child in arg_list.children:
                    if child.type in ("identifier", "attribute"):
                        bases.append(content[child.start_byte:child.end_byte].decode("utf-8", errors="replace"))

        elif language in ("java", "c_sharp", "kotlin", "scala", "typescript"):
            # Look for superclass / interfaces field
            for child in node.children:
                if child.type in ("superclass", "super_class_clause", "extends_clause",
                                  "implements_clause", "class_heritage"):
                    for name_node in child.children:
                        if name_node.type in ("identifier", "type_identifier", "scoped_type_identifier"):
                            bases.append(content[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace"))

        elif language == "rust":
            # trait bounds on impl
            for child in node.children:
                if child.type == "type_identifier":
                    bases.append(content[child.start_byte:child.end_byte].decode("utf-8", errors="replace"))

        return bases

    # -- Parameter extraction -----------------------------------------------

    def _extract_parameters(self, node, content: bytes, language: str) -> list[str]:
        """Extract function parameter names."""
        params = []
        param_node = node.child_by_field_name("parameters")
        if not param_node:
            return params

        for child in param_node.children:
            if child.type in ("identifier", "typed_parameter", "typed_default_parameter",
                              "parameter", "formal_parameter", "simple_parameter",
                              "required_parameter", "optional_parameter",
                              "parameter_declaration"):
                # Get just the name
                name_node = child.child_by_field_name("name") or child
                if name_node.type == "identifier":
                    name = content[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
                    if name != "self" and name != "cls":
                        params.append(name)
                elif name_node.children:
                    for n in name_node.children:
                        if n.type == "identifier":
                            name = content[n.start_byte:n.end_byte].decode("utf-8", errors="replace")
                            if name != "self" and name != "cls":
                                params.append(name)
                                break

        return params

    # -- Body finder --------------------------------------------------------

    def _find_body(self, node, language: str):
        """Find the body/block of a class or function node."""
        body = node.child_by_field_name("body")
        if body:
            return body

        # Fallback: look for block-type children
        for child in node.children:
            if child.type in ("block", "class_body", "function_body", "declaration_list",
                              "field_declaration_list", "statement_block", "compound_statement"):
                return child

        return None

    # -- Import extraction --------------------------------------------------

    def _extract_imports(self, root_node, content: bytes, language: str) -> list[Import]:
        """Extract import statements from AST."""
        imports: list[Import] = []
        import_types = IMPORT_NODE_TYPES.get(language, [])

        if not import_types:
            return imports

        for node in self._iter_children_recursive(root_node, max_depth=2):
            if node.type in import_types:
                imp = self._parse_import(node, content, language)
                if imp:
                    imports.append(imp)

        return imports

    def _parse_import(self, node, content: bytes, language: str) -> Import | None:
        """Parse an import node into an Import object (per-language)."""
        try:
            if language == "python":
                return self._parse_python_import(node, content)
            elif language in ("javascript", "typescript"):
                return self._parse_js_import(node, content)
            elif language == "go":
                return self._parse_go_import(node, content)
            elif language == "java":
                return self._parse_java_import(node, content)
            elif language == "rust":
                return self._parse_rust_import(node, content)
            elif language == "c_sharp":
                return self._parse_csharp_import(node, content)
            else:
                # Generic: extract full text as module name
                text = content[node.start_byte:node.end_byte].decode("utf-8", errors="replace").strip()
                return Import(module=text, names=[], line=node.start_point[0] + 1)
        except Exception:
            return None

    def _parse_python_import(self, node, content: bytes) -> Import | None:
        if node.type == "import_statement":
            names = []
            module = ""
            for child in node.children:
                if child.type == "dotted_name":
                    module = content[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                    names.append(module)
            return Import(module=module, names=names, line=node.start_point[0] + 1)

        elif node.type == "import_from_statement":
            module = ""
            names = []
            is_relative = False

            module_node = node.child_by_field_name("module_name")
            if module_node:
                module = content[module_node.start_byte:module_node.end_byte].decode("utf-8", errors="replace")

            # Check for relative import dots
            for child in node.children:
                if child.type == "relative_import":
                    is_relative = True
                elif child.type in ("dotted_name", "identifier"):
                    name = content[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                    if name != module:
                        names.append(name)

            return Import(module=module, names=names, line=node.start_point[0] + 1, is_relative=is_relative)
        return None

    def _parse_js_import(self, node, content: bytes) -> Import | None:
        module = ""
        names = []
        for child in node.children:
            if child.type == "string":
                module = content[child.start_byte:child.end_byte].decode("utf-8", errors="replace").strip("\"'")
            elif child.type == "import_clause":
                for spec in self._iter_children_recursive(child, max_depth=3):
                    if spec.type == "identifier":
                        names.append(content[spec.start_byte:spec.end_byte].decode("utf-8", errors="replace"))
        is_relative = module.startswith(".")
        return Import(module=module, names=names, line=node.start_point[0] + 1, is_relative=is_relative)

    def _parse_go_import(self, node, content: bytes) -> Import | None:
        # Go imports can be single or grouped
        imports = []
        for child in self._iter_children_recursive(node, max_depth=3):
            if child.type == "interpreted_string_literal":
                module = content[child.start_byte:child.end_byte].decode("utf-8", errors="replace").strip('"')
                imports.append(module)

        if imports:
            return Import(module=imports[0], names=imports, line=node.start_point[0] + 1)
        return None

    def _parse_java_import(self, node, content: bytes) -> Import | None:
        for child in self._iter_children_recursive(node, max_depth=3):
            if child.type == "scoped_identifier":
                module = content[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                parts = module.rsplit(".", 1)
                return Import(module=module, names=[parts[-1]] if len(parts) > 1 else [module],
                              line=node.start_point[0] + 1)
        return None

    def _parse_rust_import(self, node, content: bytes) -> Import | None:
        text = content[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
        # use std::collections::HashMap;
        parts = text.replace("use ", "").replace(";", "").strip()
        module = parts.rsplit("::", 1)[0] if "::" in parts else parts
        name = parts.rsplit("::", 1)[-1] if "::" in parts else parts
        return Import(module=module, names=[name], line=node.start_point[0] + 1)

    def _parse_csharp_import(self, node, content: bytes) -> Import | None:
        for child in node.children:
            if child.type in ("identifier", "qualified_name"):
                module = content[child.start_byte:child.end_byte].decode("utf-8", errors="replace")
                return Import(module=module, names=[module.split(".")[-1]],
                              line=node.start_point[0] + 1)
        return None

    # -- Utilities ----------------------------------------------------------

    def _iter_children_recursive(self, node, max_depth: int = 10, _depth: int = 0):
        """Iterate children recursively up to max_depth."""
        if _depth >= max_depth:
            return
        for child in node.children:
            yield child
            yield from self._iter_children_recursive(child, max_depth, _depth + 1)
