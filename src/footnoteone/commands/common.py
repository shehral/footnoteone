"""What every command shares: the `--root` option, the exit-2 error path, the project loader and the burst
label checks."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, NoReturn

import typer

from footnoteone.config import ConfigError, ProjectConfig, load_config, load_intents
from footnoteone.metrics import load_manifests
from footnoteone.schema import Intent, Manifest
from footnoteone.store import JsonlStore

# The default comes from a factory so a command can take `root: RootOption` with no `= ...` of its own.
RootOption = Annotated[
    Path,
    typer.Option(
        "--root",
        help="Project folder that holds footnote.toml and intents.yaml (default: the current folder).",
        default_factory=lambda: Path("."),
        show_default=False,
    ),
]


def _count(n: int, noun: str) -> str:
    """`n` and the noun, plural unless n is 1: "1 page", "3 pages"."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def fail(message: str) -> NoReturn:
    """Print `message` on stderr and exit with code 2, the code for a problem with the project's setup."""
    typer.echo(message, err=True)
    raise typer.Exit(2)


def _load[T](root: Path, name: str, loader: Callable[[Path], T]) -> T:
    """`loader(root)`; a ConfigError, or a file that is not UTF-8 or cannot be read, exits 2 naming it."""
    try:
        return loader(root)
    except ConfigError as exc:
        fail(str(exc))
    except UnicodeDecodeError as exc:
        fail(f"{Path(root) / name}: not UTF-8 text ({exc.reason})")
    except OSError as exc:
        fail(f"{Path(root) / name}: cannot be read: {exc.strerror or exc}")


def load_project(root: Path) -> tuple[ProjectConfig, list[Intent]]:
    """footnote.toml and intents.yaml under `root`. A ConfigError, or a file that is not UTF-8 or cannot be
    read, is printed on stderr with the file's path and exits with 2."""
    return _load(root, "footnote.toml", load_config), _load(root, "intents.yaml", load_intents)


def stored_manifests(root: Path) -> list[Manifest]:
    """Every manifest's last record; [] without a .footnote/ folder, which a read never creates. A damaged
    manifests.jsonl raises ValueError naming its line."""
    directory = Path(root) / ".footnote"
    return list(load_manifests(JsonlStore(directory)).values()) if directory.is_dir() else []


def unknown_labels(labels: list[str], manifests: list[Manifest]) -> str | None:
    """The message for burst labels that no manifest carries, naming them and the labels there are; None
    when every label names a burst."""
    known = list(dict.fromkeys(m.label for m in sorted(manifests, key=lambda m: m.started_at)))
    unknown = [label for label in dict.fromkeys(labels) if label not in known]
    if not unknown:
        return None
    there = "no burst has been run yet (footnote run)"
    if known:
        there = f"the bursts are labeled {', '.join(known)}"
    return f"no burst is labeled {', '.join(unknown)}; {there}"
