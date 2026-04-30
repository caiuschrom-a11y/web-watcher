"""web-watcher CLI."""

from __future__ import annotations

from pathlib import Path

import click

from .watch import watch


@click.group()
def cli() -> None:
    """24/7 web-page change monitor."""


@cli.command(name="check")
@click.argument("url")
def check_url(url: str) -> None:
    """Snapshot OR diff a single URL."""
    r = watch(url)
    click.echo(f"  {url}")
    click.echo(f"  changed: {r.changed}")
    if r.change_summary:
        click.echo(f"  summary: {r.change_summary}")


@cli.command(name="batch")
@click.argument("urls_file", type=click.Path(exists=True, path_type=Path))
def batch(urls_file: Path) -> None:
    """Run batch watch over a file of URLs (one per line)."""
    urls = [u.strip() for u in urls_file.read_text(encoding="utf-8").splitlines() if u.strip()]
    changed_count = 0
    for u in urls:
        try:
            r = watch(u)
        except Exception as e:
            click.echo(f"  ERROR {u}: {e}", err=True)
            continue
        if r.changed:
            changed_count += 1
            click.echo(f"  ✓ CHANGED {u}")
            click.echo(f"      {r.change_summary}")
    click.echo(f"\n{changed_count}/{len(urls)} pages changed")


if __name__ == "__main__":
    cli()
