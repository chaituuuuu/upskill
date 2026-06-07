"""
CodeWiki CLI — entry point for all commands.

Commands (Phase 0):
  codewiki ping     — verify LLM endpoint & config

Coming in later phases:
  codewiki generate — full wiki build
  codewiki update   — diff-aware refresh
  codewiki chat     — grounded Q&A
  codewiki lint     — health report
  codewiki serve    — local web viewer
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from codewiki import __version__
from codewiki.config import CodeWikiConfig, load_config
from codewiki.llm.budget import Budget
from codewiki.llm.client import LLMClient
from codewiki.llm.retry import with_retry

app = typer.Typer(
    name="codewiki",
    help="Business-oriented knowledge base generator for source code.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)
console = Console()


# ---------------------------------------------------------------------------
# Shared options callback
# ---------------------------------------------------------------------------

_cfg_state: dict = {}


def _load_cfg(
    config_file: Optional[Path] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    dry_run: bool = False,
) -> CodeWikiConfig:
    overrides: dict = {}
    if model:
        overrides["llm__model"] = model
    if base_url:
        overrides["llm__base_url"] = base_url
    if dry_run:
        overrides["run__dry_run"] = True
    return load_config(yaml_path=config_file, overrides=overrides or None)


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


@app.command()
def ping(
    config_file: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to codewiki.yaml"
    ),
    model: Optional[str] = typer.Option(None, "--model", "-m", help="Override LLM model"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="Override LLM base_url"),
    show_config: bool = typer.Option(False, "--show-config", help="Print resolved config"),
) -> None:
    """Verify the configured LLM endpoint returns a completion."""

    cfg = _load_cfg(config_file=config_file, model=model, base_url=base_url)

    if show_config:
        _print_config(cfg)

    console.print(f"[bold]CodeWiki v{__version__}[/bold] — pinging endpoint …")
    console.print(f"  base_url : [cyan]{cfg.llm.base_url}[/cyan]")
    console.print(f"  model    : [cyan]{cfg.llm.model}[/cyan]")

    budget = Budget()

    async def _run() -> str:
        async with LLMClient(cfg.llm) as client:
            messages = [
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Reply with exactly one sentence.",
                },
                {"role": "user", "content": "Say 'CodeWiki ping successful.' and nothing else."},
            ]
            response = await with_retry(client.chat, messages, retries=2)
            # Budget tracking (best-effort — not all endpoints return usage)
            budget.record(prompt_tokens=20, completion_tokens=10)
            return response

    try:
        reply = asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]✗ Ping failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    console.print(
        Panel(
            f"[green]✓ Ping successful[/green]\n\n[italic]{reply}[/italic]",
            title="LLM Response",
            border_style="green",
        )
    )
    console.print(f"  tokens (est.): {budget.total_tokens}")


# ---------------------------------------------------------------------------
# generate (stub — Phase 1+)
# ---------------------------------------------------------------------------


@app.command()
def generate(
    source: str = typer.Argument(..., help="Local directory path or Git URL to analyse."),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c"),
    model: Optional[str] = typer.Option(None, "--model", "-m"),
    base_url: Optional[str] = typer.Option(None, "--base-url"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Estimate cost only; write nothing."),
    max_files: Optional[int] = typer.Option(None, "--max-files"),
    output_dir: Optional[Path] = typer.Option(None, "--output", "-o"),
) -> None:
    """Generate the wiki for a codebase (Phase 1–3)."""
    cfg = _load_cfg(config_file=config_file, model=model, base_url=base_url, dry_run=dry_run)
    if max_files is not None:
        cfg.run.max_files = max_files
    if output_dir is not None:
        cfg.wiki.output_dir = output_dir

    console.print(
        "[yellow]⚠ 'generate' is not yet implemented — scheduled for Phase 1.[/yellow]"
    )
    console.print(f"  source     : {source}")
    console.print(f"  output_dir : {cfg.wiki.output_dir}")
    console.print(f"  dry_run    : {cfg.run.dry_run}")
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# update (stub — Phase 5)
# ---------------------------------------------------------------------------


@app.command()
def update(
    source: str = typer.Argument(..., help="Local directory (must already have wiki/)."),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Diff-aware incremental wiki refresh (Phase 5)."""
    console.print("[yellow]⚠ 'update' is not yet implemented — scheduled for Phase 5.[/yellow]")
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# chat (stub — Phase 4)
# ---------------------------------------------------------------------------


@app.command()
def chat(
    question: str = typer.Argument(..., help="Question to ask about the codebase."),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c"),
    file_back: bool = typer.Option(False, "--file-back", help="Save answer as a wiki page."),
) -> None:
    """Grounded Q&A over the wiki + code (Phase 4)."""
    console.print("[yellow]⚠ 'chat' is not yet implemented — scheduled for Phase 4.[/yellow]")
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# lint (stub — Phase 5)
# ---------------------------------------------------------------------------


@app.command()
def lint(
    config_file: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Wiki health report: stale, orphans, broken citations (Phase 5)."""
    console.print("[yellow]⚠ 'lint' is not yet implemented — scheduled for Phase 5.[/yellow]")
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# serve (stub — Phase 6)
# ---------------------------------------------------------------------------


@app.command()
def serve(
    port: int = typer.Option(8080, "--port", "-p"),
    config_file: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Launch the local web viewer (Phase 6)."""
    console.print("[yellow]⚠ 'serve' is not yet implemented — scheduled for Phase 6.[/yellow]")
    raise typer.Exit(0)


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the CodeWiki version."""
    console.print(f"codewiki {__version__}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_config(cfg: CodeWikiConfig) -> None:
    table = Table(title="Resolved Configuration", show_header=True)
    table.add_column("Key", style="bold cyan")
    table.add_column("Value")

    flat = {
        "llm.base_url": cfg.llm.base_url,
        "llm.model": cfg.llm.model,
        "llm.api_key": "***" if cfg.llm.get_api_key() else "(none)",
        "llm.temperature": cfg.llm.temperature,
        "llm.max_tokens": cfg.llm.max_tokens,
        "llm.embedding_model": cfg.llm.embedding_model or "(none)",
        "wiki.output_dir": str(cfg.wiki.output_dir),
        "wiki.strict_grounding": cfg.wiki.strict_grounding,
        "run.concurrency": cfg.run.concurrency,
        "run.dry_run": cfg.run.dry_run,
        "run.token_budget": cfg.run.token_budget or "(unlimited)",
    }
    for k, v in flat.items():
        table.add_row(k, str(v))

    console.print(table)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
