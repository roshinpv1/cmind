"""
AST extraction using Tree-Sitter.

Extracts code structure: classes, functions, imports, calls.
"""

from dataclasses import dataclass
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Parser


@dataclass
class Symbol:
    """Represents a code symbol (class, function, etc.)."""

    name: str
    type: str  # "class", "function", "method"
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    docstring: str | None = None
    parent: str | None = None  # For methods, the class name


@dataclass
class Import:
    """Represents an import statement."""

    module: str
    names: list[str]  # Imported names
    line: int


@dataclass
class ASTExtractionResult:
    """Result of AST extraction for a file."""

    symbols: list[Symbol]
    imports: list[Import]
    language: str
    success: bool
    error: str | None = None


class ASTExtractor:
    """Extracts Abstract Syntax Tree information from code files."""

    def __init__(self):
        """Initialize AST extractor with language support."""
        # Initialize Python parser
        PY_LANGUAGE = Language(tspython.language())
        self.python_parser = Parser(PY_LANGUAGE)

    def extract(self, file_path: Path, language: str = "python") -> ASTExtractionResult:
        """
        Extract AST information from file.

        Args:
            file_path: Path to source file
            language: Programming language

        Returns:
            ASTExtractionResult with extracted symbols and imports
        """
        try:
            # Read file content
            with open(file_path, "rb") as f:
                content = f.read()

            if language == "python":
                return self._extract_python(content, file_path)
            else:
                # Unsupported language - return empty result
                return ASTExtractionResult(
                    symbols=[],
                    imports=[],
                    language=language,
                    success=True,
                    error=f"Language '{language}' not yet supported",
                )

        except Exception as e:
            # Fail gracefully - return error result
            return ASTExtractionResult(
                symbols=[], imports=[], language=language, success=False, error=str(e)
            )

    def _extract_python(self, content: bytes, file_path: Path) -> ASTExtractionResult:
        """Extract Python AST."""
        tree = self.python_parser.parse(content)
        root_node = tree.root_node

        symbols = []
        imports = []

        # Extract top-level definitions
        for node in root_node.children:
            if node.type == "function_definition":
                sym = self._extract_function(node, content)
                if sym:
                    symbols.append(sym)

            elif node.type == "class_definition":
                sym = self._extract_class(node, content)
                if sym:
                    symbols.append(sym)
                # Also extract methods
                symbols.extend(self._extract_methods(node, content))

            elif node.type in ("import_statement", "import_from_statement"):
                imp = self._extract_import(node, content)
                if imp:
                    imports.append(imp)

        return ASTExtractionResult(
            symbols=symbols, imports=imports, language="python", success=True
        )

    def _extract_function(self, node, content: bytes, parent: str | None = None) -> Symbol | None:
        """Extract function definition."""
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None

        name = content[name_node.start_byte : name_node.end_byte].decode("utf-8")

        # Extract docstring
        docstring = None
        body = node.child_by_field_name("body")
        if body and len(body.children) > 0:
            first_stmt = body.children[0]
            if first_stmt.type == "expression_statement":
                expr = first_stmt.children[0]
                if expr.type == "string":
                    docstring = (
                        content[expr.start_byte : expr.end_byte].decode("utf-8").strip("\"'")
                    )

        return Symbol(
            name=name,
            type="method" if parent else "function",
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_byte=node.start_byte,
            end_byte=node.end_byte,
            docstring=docstring,
            parent=parent,
        )

    def _extract_class(self, node, content: bytes) -> Symbol | None:
        """Extract class definition."""
        name_node = node.child_by_field_name("name")
        if not name_node:
            return None

        name = content[name_node.start_byte : name_node.end_byte].decode("utf-8")

        # Extract docstring
        docstring = None
        body = node.child_by_field_name("body")
        if body and len(body.children) > 0:
            for child in body.children:
                if child.type == "expression_statement":
                    expr = child.children[0]
                    if expr.type == "string":
                        docstring = (
                            content[expr.start_byte : expr.end_byte].decode("utf-8").strip("\"'")
                        )
                        break

        return Symbol(
            name=name,
            type="class",
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            start_byte=node.start_byte,
            end_byte=node.end_byte,
            docstring=docstring,
        )

    def _extract_methods(self, class_node, content: bytes) -> list[Symbol]:
        """Extract methods from class."""
        methods = []
        class_name_node = class_node.child_by_field_name("name")
        if not class_name_node:
            return methods

        class_name = content[class_name_node.start_byte : class_name_node.end_byte].decode("utf-8")

        body = class_node.child_by_field_name("body")
        if not body:
            return methods

        for node in body.children:
            if node.type == "function_definition":
                method = self._extract_function(node, content, parent=class_name)
                if method:
                    methods.append(method)

        return methods

    def _extract_import(self, node, content: bytes) -> Import | None:
        """Extract import statement."""
        if node.type == "import_statement":
            # import module
            names = []
            module = ""
            for child in node.children:
                if child.type == "dotted_name":
                    module = content[child.start_byte : child.end_byte].decode("utf-8")
                    names.append(module)

            return Import(module=module, names=names, line=node.start_point[0] + 1)

        elif node.type == "import_from_statement":
            # from module import names
            module = ""
            names = []

            module_node = node.child_by_field_name("module_name")
            if module_node:
                module = content[module_node.start_byte : module_node.end_byte].decode("utf-8")

            # Extract imported names
            for child in node.children:
                if child.type == "dotted_name" or child.type == "identifier":
                    name = content[child.start_byte : child.end_byte].decode("utf-8")
                    if name != module:
                        names.append(name)

            return Import(module=module, names=names, line=node.start_point[0] + 1)

        return None
