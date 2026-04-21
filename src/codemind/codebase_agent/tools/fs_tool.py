"""File system tool for secure codebase exploration.

This module provides a secure interface for executing file system operations with safety
constraints, working directory restrictions, and comprehensive logging.
"""

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

class FileSystemExecutionError(Exception):
    """Raised when file system operation fails."""
    pass

class FileSystemTool:
    """Secure file system tool for codebase exploration.

    This tool provides controlled execution of file operations with security
    constraints and comprehensive logging.
    """

    def __init__(
        self,
        working_directory: str,
        max_output_size: int = 20000,
        enable_logging: bool = True,
    ):
        """Initialize FileSystemTool with security constraints.

        Args:
            working_directory: Directory to restrict execution to
            max_output_size: Maximum size of command output in characters
            enable_logging: Whether to log execution details
        """
        self.working_directory = Path(working_directory).resolve()
        self.max_output_size = max_output_size
        self.enable_logging = enable_logging

        # Validate working directory
        if not self.working_directory.exists():
            raise ValueError(
                f"Working directory does not exist: {self.working_directory}"
            )
        if not self.working_directory.is_dir():
            raise ValueError(
                f"Working directory is not a directory: {self.working_directory}"
            )

        if self.enable_logging:
            logger.info(
                f"FileSystemTool initialized: working_dir={self.working_directory}, "
                f"max_output={self.max_output_size}"
            )

    def _resolve_and_validate_path(self, path: str) -> Path:
        """Resolve a path relative to working directory and validate it's within bounds."""
        target_path = (self.working_directory / path).resolve()
        if not str(target_path).startswith(str(self.working_directory)):
            raise ValueError(f"Path outside working directory: {path}")
        return target_path

    def _truncate_output(self, output: str) -> str:
        """Truncate output if it exceeds max size."""
        if len(output) > self.max_output_size:
            return output[: self.max_output_size] + f"\n... (output truncated at {self.max_output_size} characters)"
        return output

    def list_directory(self, path: str = ".") -> str:
        """List contents of a directory.
        
        Args:
            path: Path to directory
        Returns:
            Directory contents as a string
        """
        try:
            target_path = self._resolve_and_validate_path(path)
            if not target_path.exists() or not target_path.is_dir():
                return f"Error: Directory {path} does not exist."
            
            output = []
            for item in sorted(target_path.iterdir()):
                item_type = "DIR" if item.is_dir() else "FILE"
                size = item.stat().st_size if item.is_file() else "-"
                output.append(f"{item_type}\t{size}\t{item.name}")
                
            return self._truncate_output("\n".join(output))
        except Exception as e:
            return f"Error listing directory: {e}"

    def read_file(self, path: str, num_lines: int = -1) -> str:
        """Read contents of a file.
        
        Args:
            path: Path to file
            num_lines: Number of lines to read (-1 for all)
        Returns:
            File contents
        """
        try:
            target_path = self._resolve_and_validate_path(path)
            if not target_path.exists() or not target_path.is_file():
                return f"Error: File {path} does not exist."
                
            try:
                # Try reading as text
                with open(target_path, 'r', encoding='utf-8') as f:
                    if num_lines > 0:
                        lines = [next(f) for _ in range(num_lines)]
                        content = "".join(lines)
                    else:
                        content = f.read()
                return self._truncate_output(content)
            except UnicodeDecodeError:
                return f"Error: {path} appears to be a binary file."
        except Exception as e:
            return f"Error reading file: {e}"

    def search_content(self, pattern: str, path: str = ".") -> str:
        """Search for a pattern in files.
        
        Args:
            pattern: Regular expression pattern
            path: Path to search in (file or directory)
        Returns:
            Search results
        """
        try:
            target_path = self._resolve_and_validate_path(path)
            if not target_path.exists():
                return f"Error: Path {path} does not exist."

            regex = re.compile(pattern)
            results = []
            
            def search_file(file_path: Path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                rel_path = file_path.relative_to(self.working_directory)
                                results.append(f"{rel_path}:{i}:{line.rstrip()}")
                except (UnicodeDecodeError, PermissionError):
                    pass # Skip binary files and files without permission

            if target_path.is_file():
                search_file(target_path)
            else:
                for root, _, files in os.walk(target_path):
                    for file in files:
                        search_file(Path(root) / file)
                        if sum(len(r) for r in results) > self.max_output_size:
                            break # Stop early if output is too large
                    if sum(len(r) for r in results) > self.max_output_size:
                        break

            if not results:
                return "No matches found."
            return self._truncate_output("\n".join(results))
        except Exception as e:
            return f"Error searching content: {e}"

    def get_file_info(self, path: str) -> str:
        """Get file statistics.
        
        Args:
            path: Path to file
        Returns:
            File statistics
        """
        try:
            target_path = self._resolve_and_validate_path(path)
            if not target_path.exists():
                return f"Error: Path {path} does not exist."
            
            stat = target_path.stat()
            type_str = "Directory" if target_path.is_dir() else "File"
            return f"Type: {type_str}\nSize: {stat.st_size} bytes\nLast Modified: {stat.st_mtime}"
        except Exception as e:
            return f"Error getting file info: {e}"

    def execute_tool(self, tool_name: str, args: dict) -> tuple[bool, str, str]:
        """Execute a tool dynamically.
        
        Args:
            tool_name: Name of the tool function
            args: Arguments for the tool
        Returns:
            Tuple of (success, stdout, stderr)
        """
        if self.enable_logging:
            logger.info(f"Executing FileSystem tool: {tool_name} with {args}")
            
        tools = {
            "list_directory": self.list_directory,
            "read_file": self.read_file,
            "search_content": self.search_content,
            "get_file_info": self.get_file_info,
        }
        
        if tool_name not in tools:
            return False, "", f"Unknown tool: {tool_name}"
            
        try:
            result = tools[tool_name](**args)
            if result.startswith("Error:"):
                return False, "", result
            return True, result, ""
        except TypeError as e:
            return False, "", f"Invalid arguments for {tool_name}: {e}"
        except Exception as e:
            return False, "", f"Error executing {tool_name}: {e}"
