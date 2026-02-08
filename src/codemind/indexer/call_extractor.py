"""
Call-site extraction from AST.

Extracts function call sites within function bodies to build
CALLS relationships in the code graph.
"""

from dataclasses import dataclass
from pathlib import Path

from .ast_extractor import ASTExtractor, FUNCTION_NODE_TYPES


@dataclass
class CallSite:
    """Represents a function call in source code."""
    caller_name: str           # Function making the call
    callee_name: str           # Function being called
    callee_module: str | None  # Module prefix if qualified call (e.g., os.path → "os.path")
    line: int
    file_path: str


# Per-language call expression node types
CALL_NODE_TYPES: dict[str, list[str]] = {
    "python": ["call"],
    "javascript": ["call_expression"],
    "typescript": ["call_expression"],
    "go": ["call_expression"],
    "java": ["method_invocation"],
    "rust": ["call_expression", "macro_invocation"],
    "c": ["call_expression"],
    "cpp": ["call_expression"],
    "c_sharp": ["invocation_expression"],
    "ruby": ["call", "method_call"],
    "php": ["function_call_expression", "method_call_expression"],
    "swift": ["call_expression"],
    "kotlin": ["call_expression"],
    "scala": ["call_expression"],
}


class CallExtractor:
    """Extract function calls from AST nodes (multi-language)."""

    def __init__(self):
        self.ast_extractor = ASTExtractor()

    def extract_calls(
        self, file_path: Path, language: str | None = None
    ) -> list[CallSite]:
        """
        Extract all function calls from a source file.

        Args:
            file_path: Path to source file
            language: Programming language (auto-detected if None)

        Returns:
            List of CallSite objects
        """
        if language is None:
            language = self.ast_extractor.detect_language(file_path)
        if not language:
            return []

        parser = self.ast_extractor._get_parser(language)
        if not parser:
            return []

        try:
            with open(file_path, "rb") as f:
                content = f.read()

            tree = parser.parse(content)
            return self._extract_from_tree(tree.root_node, content, language, str(file_path))
        except Exception:
            return []

    def _extract_from_tree(
        self, root_node, content: bytes, language: str, file_path: str
    ) -> list[CallSite]:
        """Walk AST and find all call expressions within functions."""
        calls: list[CallSite] = []
        func_types = FUNCTION_NODE_TYPES.get(language, [])
        call_types = CALL_NODE_TYPES.get(language, [])

        if not call_types:
            return calls

        # Find all function bodies, then look for calls within them
        self._walk_functions(root_node, content, language, func_types, call_types, calls, file_path)
        return calls

    def _walk_functions(
        self, node, content: bytes, language: str,
        func_types: list[str], call_types: list[str],
        calls: list[CallSite], file_path: str,
        current_func: str | None = None
    ):
        """Recursively find functions and extract calls within them."""
        for child in node.children:
            if child.type in func_types:
                func_name = self._get_name(child, content)
                if func_name:
                    # Extract calls within this function
                    self._find_calls_in_node(child, content, language, call_types, calls, file_path, func_name)
                    # Also recurse for nested functions
                    self._walk_functions(child, content, language, func_types, call_types, calls, file_path, func_name)
            else:
                self._walk_functions(child, content, language, func_types, call_types, calls, file_path, current_func)

    def _find_calls_in_node(
        self, node, content: bytes, language: str,
        call_types: list[str], calls: list[CallSite],
        file_path: str, caller_name: str
    ):
        """Find all call expressions within a node."""
        for child in node.children:
            if child.type in call_types:
                callee_name, callee_module = self._extract_callee(child, content, language)
                if callee_name:
                    calls.append(CallSite(
                        caller_name=caller_name,
                        callee_name=callee_name,
                        callee_module=callee_module,
                        line=child.start_point[0] + 1,
                        file_path=file_path,
                    ))
            # Recurse (but not into nested function definitions)
            if child.type not in FUNCTION_NODE_TYPES.get(language, []):
                self._find_calls_in_node(child, content, language, call_types, calls, file_path, caller_name)

    def _extract_callee(self, call_node, content: bytes, language: str) -> tuple[str | None, str | None]:
        """Extract the callee name and optional module from a call expression."""
        # Look for the function being called
        func_node = call_node.child_by_field_name("function")
        if not func_node:
            # Some languages use different field names
            func_node = call_node.child_by_field_name("name")
        if not func_node:
            # Fallback: first child that's an identifier or attribute
            for child in call_node.children:
                if child.type in ("identifier", "attribute", "member_expression",
                                  "field_expression", "scoped_identifier",
                                  "qualified_identifier"):
                    func_node = child
                    break

        if not func_node:
            return None, None

        # Handle attribute access (e.g., obj.method())
        if func_node.type in ("attribute", "member_expression", "field_expression"):
            full_name = content[func_node.start_byte:func_node.end_byte].decode("utf-8", errors="replace")
            parts = full_name.rsplit(".", 1)
            if len(parts) == 2:
                return parts[1], parts[0]
            return full_name, None

        # Simple identifier
        name = content[func_node.start_byte:func_node.end_byte].decode("utf-8", errors="replace")
        return name, None

    def _get_name(self, node, content: bytes) -> str | None:
        """Get the name of a function/method node."""
        name_node = node.child_by_field_name("name")
        if name_node:
            return content[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
        return None
