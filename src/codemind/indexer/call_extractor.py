"""
Call-site extraction from AST.

Extracts function call sites within function bodies to build
CALLS relationships in the code graph.
"""

from dataclasses import dataclass
from pathlib import Path
import logging

from .ast_extractor import ASTExtractor, FUNCTION_NODE_TYPES

@dataclass
class CallSite:
    """Represents a function call in source code."""
    caller_name: str           # Function making the call
    callee_name: str           # Function being called
    callee_module: str | None  # Module prefix if qualified call (e.g., os.path -> "os.path")
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
        # Pre-compile queries for speed
        self._queries: dict[str, any] = {}
        self.logger = logging.getLogger("codemind.call_extractor")

    def _get_query(self, language: str):
        """Get or compile tree-sitter query for calls in a specific language."""
        if language in self._queries:
            return self._queries[language]

        query_str = ""
        if language == "python":
            query_str = "(call function: (identifier) @call.name)"
        elif language in ("javascript", "typescript"):
            query_str = "(call_expression function: (identifier) @call.name)"
        elif language == "java":
            query_str = "(method_invocation name: (identifier) @call.name)"
        elif language in ("go", "rust", "cpp", "c", "c_sharp", "ruby"):
            query_str = "(call_expression function: (identifier) @call.name)"

        if not query_str:
            return None

        try:
            lang_obj = self.ast_extractor._get_language_obj(language)
            if lang_obj:
                query = lang_obj.query(query_str)
                self._queries[language] = query
                return query
        except Exception as e:
            self.logger.warning(f"Failed to compile query for {language}: {e}")
        return None

    def extract_calls(
        self, file_path: Path, language: str | None = None, content: bytes | None = None, tree: any = None
    ) -> list[CallSite]:
        """
        Extract all function calls from a source file, optionally reusing an existing tree.
        """
        if language is None:
            language = self.ast_extractor.detect_language(file_path)
        if not language:
            return []

        if tree is None or content is None:
            try:
                with open(file_path, "rb") as f:
                    content = f.read()
                parser = self.ast_extractor._get_parser(language)
                if not parser: return []
                tree = parser.parse(content)
            except Exception as e:
                self.logger.error(f"Failed to parse {file_path}: {e}")
                return []

        if not tree or not content:
            return []

        return self.extract_calls_from_tree(tree, content, language, str(file_path))

    def extract_calls_from_tree(
        self, tree, content: bytes, language: str, file_path: str
    ) -> list[CallSite]:
        """Fast query-based extraction from an existing tree."""
        calls: list[CallSite] = []
        query = self._get_query(language)

        if query:
            # High-performance native query walk (Releases GIL)
            captures = query.captures(tree.root_node)
            for node, tag in captures:
                if tag == "call.name":
                    name = content[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
                    
                    # Find the caller (climb up the tree)
                    caller = self._find_parent_function(node, language, content)
                    
                    calls.append(CallSite(
                        caller_name=caller or "top_level",
                        callee_name=name,
                        callee_module=None,
                        line=node.start_point[0] + 1,
                        file_path=file_path,
                    ))
            return calls

        # Fallback to legacy recursive walk if no optimized query exists for this language
        func_types = FUNCTION_NODE_TYPES.get(language, [])
        call_types = CALL_NODE_TYPES.get(language, [])
        if not call_types: return []
        
        self._walk_functions(tree.root_node, content, language, func_types, call_types, calls, file_path)
        return calls

    def _find_parent_function(self, node, language: str, content: bytes) -> str | None:
        """Climb up the tree to find the name of the containing function node."""
        func_types = FUNCTION_NODE_TYPES.get(language, [])
        curr = node.parent
        while curr:
            if curr.type in func_types:
                return self._get_name(curr, content)
            curr = curr.parent
        return None

    def _get_name(self, node, content: bytes) -> str | None:
        """Helper to get the name field of a node."""
        name_node = node.child_by_field_name("name")
        if name_node:
            return content[name_node.start_byte:name_node.end_byte].decode("utf-8", errors="replace")
        return None

    def _walk_functions(
        self, node, content: bytes, language: str,
        func_types: list[str], call_types: list[str],
        calls: list[CallSite], file_path: str,
        current_func: str | None = None, depth: int = 0
    ):
        """Standard recursive walk (slower, keeps GIL)."""
        if depth > 50: return
        for child in node.children:
            if child.type in func_types:
                func_name = self._get_name(child, content)
                if func_name:
                    self._find_calls_in_node(child, content, language, call_types, calls, file_path, func_name, depth + 1)
            else:
                self._walk_functions(child, content, language, func_types, call_types, calls, file_path, current_func, depth + 1)

    def _find_calls_in_node(
        self, node, content: bytes, language: str,
        call_types: list[str], calls: list[CallSite],
        file_path: str, caller_name: str, depth: int = 0
    ):
        """Recursive call search within a node."""
        if depth > 50: return
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
            if child.type not in FUNCTION_NODE_TYPES.get(language, []):
                self._find_calls_in_node(child, content, language, call_types, calls, file_path, caller_name, depth + 1)

    def _extract_callee(self, call_node, content: bytes, language: str) -> tuple[str | None, str | None]:
        """Legacy helper for callee extraction."""
        func_node = call_node.child_by_field_name("function") or call_node.child_by_field_name("name")
        if not func_node:
            for child in call_node.children:
                if child.type in ("identifier", "attribute", "member_expression", "field_expression"):
                    func_node = child; break
        if not func_node: return None, None

        if func_node.type in ("attribute", "member_expression", "field_expression"):
            full_name = content[func_node.start_byte:func_node.end_byte].decode("utf-8", errors="replace")
            parts = full_name.rsplit(".", 1)
            return (parts[1], parts[0]) if len(parts) == 2 else (full_name, None)
        
        name = content[func_node.start_byte:func_node.end_byte].decode("utf-8", errors="replace")
        return name, None
