"""CLI entry point for CodeMind."""

import click


@click.group()
@click.version_option(version="0.1.0")
def main():
    """CodeMind - Incremental code intelligence platform."""
    pass


@main.command()
def version():
    """Display version information."""
    click.echo("CodeMind v0.1.0")


if __name__ == "__main__":
    main()
