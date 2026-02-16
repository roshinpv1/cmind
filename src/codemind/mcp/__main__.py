"""Entry point: python -m codemind.mcp"""

from .server import mcp


def main():
    """Run the CodeMind MCP server (stdio transport)."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
