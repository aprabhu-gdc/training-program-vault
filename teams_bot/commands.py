"""Slash-command registry and parser for the Teams bot.

Any message whose text begins with ``/<letter>`` is treated as *command intent*
and dispatched here — it is never forwarded to the wiki query path, so an admin
command typed by a non-admin can't be answered as a hallucinated question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    name: str
    admin_only: bool
    description: str
    usage: str


@dataclass(frozen=True)
class ParsedCommand:
    spec: CommandSpec | None  # None => text looked like a command but matched nothing
    raw_name: str
    args: str


# The command language. Ordered for a readable /help listing.
COMMANDS: dict[str, CommandSpec] = {
    "help": CommandSpec("help", admin_only=False, description="Show available commands", usage="/help"),
    "whoami": CommandSpec(
        "whoami",
        admin_only=False,
        description="Show your Teams identity (and Entra object ID for admin setup)",
        usage="/whoami",
    ),
    "sync": CommandSpec(
        "sync", admin_only=True, description="Refresh the vault from SharePoint", usage="/sync"
    ),
    "stopsync": CommandSpec(
        "stopsync", admin_only=True, description="Gracefully stop the running vault refresh", usage="/stopsync"
    ),
    "remove": CommandSpec(
        "remove",
        admin_only=True,
        description="Remove a wiki page (with confirmation)",
        usage="/remove wiki/path/to/page.md",
    ),
    "clean": CommandSpec(
        "clean",
        admin_only=True,
        description="Reconcile the index and prune stale state (hygiene pass)",
        usage="/clean",
    ),
    "lint": CommandSpec(
        "lint",
        admin_only=True,
        description="Audit the wiki for contradictions, orphans, and weak sourcing",
        usage="/lint",
    ),
}


_COMMAND_INTENT = re.compile(r"^/(?=[A-Za-z])")
_ARG_WRAPPERS = "`'\"“”‘’"


def looks_like_command(text: str) -> bool:
    """True when the text is command intent (starts with ``/`` then a letter)."""
    return bool(_COMMAND_INTENT.match(text.strip()))


def parse_command(text: str) -> ParsedCommand | None:
    """Parse a slash command, or return None when the text is not command intent.

    A returned ``ParsedCommand`` with ``spec is None`` means the text looked like
    a command (``/xyz``) but did not match the registry — the caller should reply
    "unknown command" rather than treat it as a wiki question.
    """

    stripped = text.strip()
    if not looks_like_command(stripped):
        return None

    token, _, remainder = stripped.partition(" ")
    raw_name = token[1:]  # drop the leading slash
    name = raw_name.lower()
    args = remainder.strip().strip(_ARG_WRAPPERS).strip()
    return ParsedCommand(spec=COMMANDS.get(name), raw_name=raw_name, args=args)
