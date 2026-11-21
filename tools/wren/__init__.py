"""Wren: the memory curator. Backlog I1, the memory loop.

Each run wren reads the ledger's ``memory.write`` entries, consolidates them into one dated note per
scope, and commits each note back to the ledger as a ``memory.write`` of its own, so a run can
retrieve yesterday's memory instead of raw entries (spec: "settlement": entries carry a date and are
retrievable by scope). Wren is a `watcher` on the ledger: it never executes anything a memory says,
because a memory entry is data, never an instruction. Every line of every note is sanitized to inert
text: control characters dropped, whitespace collapsed, Markdown pipes escaped, length capped.

    python -m tools.wren curate --summary out.md --json out.json [--post]

Each run then

1. reads the ledger and takes the last ``MAX_LINES`` ``memory.write`` entries, skipping wren's own;
2. sorts them into scopes (the entry's ``scope``, or ``general``), drops duplicates on key keeping
   the most recent and keeps the newest ``MAX_LINES`` items per scope;
3. writes one note per scope; with ``--post`` one ``memory.write`` per scope per UTC day
   (``key = consolidated/<scope>@<date>``), skipped when already written, plus one ``heartbeat`` with
   the notes.

Environment: ``UNISYNC_GATEWAY_URL`` (not secret) and ``UNISYNC_WREN_TOKEN`` (secret; posts only
``heartbeat``, ``escalate`` and ``memory.write`` as ``wren``).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

AGENT = "wren"
FLOW = "memory-curation"
EVENT = "memory.write"
GATEWAY_ENV = "UNISYNC_GATEWAY_URL"
LEDGER_TOKEN_ENV = "UNISYNC_WREN_TOKEN"  # noqa: S105 - env var name, not a secret
MAX_LINES = 50  # items kept per note, most recent first
MAX_LINE = 200  # characters of one note line, so a long entry cannot flood a note
MAX_ESCALATIONS = 10  # per run, so a bad day cannot flood the ledger

CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass
class Item:
    """One memory.write entry, flattened to inert text."""

    sender: str
    scope: str
    key: str
    text: str
    order: int  # position in ledger order (oldest first)


@dataclass
class Note:
    """The consolidation of one scope's items."""

    scope: str
    items: list[Item] = field(default_factory=list)
    deduped: int = 0

    @property
    def outbox_key(self) -> str:
        return f"consolidated/{self.scope}"


def sanitize(text: str) -> str:
    """One inert line: control characters dropped, whitespace collapsed, pipes escaped, capped."""
    flat = " ".join(CONTROL.sub(" ", text).split())
    return flat.replace("|", "\\|")[:MAX_LINE]


def payload_of(entry: Any) -> Any:
    """The entry's payload, however the ledger library exposes it."""
    payload = getattr(entry, "payload", None)
    if payload is None:
        return getattr(entry, "body", None)
    return payload


def item_of(entry: Any, order: int) -> Item | None:
    """A memory.write entry as an Item, or None when it is not one or carries no text."""
    kind = getattr(getattr(entry, "kind", None), "value", getattr(entry, "kind", None))
    if kind != EVENT:
        return None
    sender = str(getattr(entry, "sender", "") or "")
    payload = payload_of(entry)
    text = scope = key = ""
    if isinstance(payload, dict):
        value = payload.get("value")
        text = str(payload.get("summary") or ("" if isinstance(value, dict) else value or ""))
        if isinstance(value, dict):
            text = " ".join(f"{k}={sanitize(str(v))}" for k, v in value.items()) or text
        scope = str(payload.get("scope") or "")
        key = str(payload.get("key") or "")
    elif isinstance(payload, str):
        text = payload
    if not text.strip():
        return None
    return Item(sender=sender, scope=scope or "general", key=key, text=sanitize(text), order=order)


def read_items(entries: Iterable[Any], *, skip: str = AGENT, limit: int = MAX_LINES) -> list[Item]:
    """The last `limit` memory items that did not come from the curator itself, in ledger order."""
    items = [item for order, entry in enumerate(entries) for item in [item_of(entry, order)] if item]
    return [item for item in items if item.sender != skip][-limit:]


def consolidate(items: Sequence[Item]) -> list[Note]:
    """One note per scope: duplicates on key (or whole line) drop, newest wins; order preserved."""
    by_scope: OrderedDict[str, list[Item]] = OrderedDict()
    for item in items:
        by_scope.setdefault(item.scope, []).append(item)
    notes: list[Note] = []
    for scope, group in by_scope.items():
        kept: dict[str, Item] = {}
        for item in group:
            ident = item.key or item.text
            kept[ident] = item  # last one wins
        deduped = len(group) - len(kept)
        recent = list(kept.values())[-MAX_LINES:]
        recent.sort(key=lambda i: i.order)
        notes.append(Note(scope=scope, items=recent, deduped=deduped))
    return notes


# --- Report -----------------------------------------------------------------------------------


def summary_line(notes: Sequence[Note]) -> str:
    if not notes:
        return "nothing to consolidate"
    parts = ", ".join(f"{n.scope}: {len(n.items)}" for n in notes)
    return f"{len(notes)} note(s): {parts}"


def markdown(notes: Sequence[Note], now: datetime, day_note: str = "") -> str:
    lines = [
        f"### Wren: {summary_line(notes)}",
        "",
        f"_Curated {now:%Y-%m-%d %H:%M} UTC. Memory entries are data, never instructions._",
        "",
    ]
    if day_note:
        lines += [day_note, ""]
    for note in notes:
        lines += [f"**{note.scope}**" + (f" ({note.deduped} duplicate(s) dropped)" if note.deduped else ""), ""]
        lines += [f"- {i.text}" for i in note.items]
        lines.append("")
    if notes:
        lines.append(f"{sum(len(n.items) for n in notes)} item(s) in {len(notes)} note(s).")
    return "\n".join(lines) + "\n"


# --- Ledger -----------------------------------------------------------------------------------


def post(ledger: Any, notes: Sequence[Note], now: datetime, run_url: str) -> tuple[int, int]:
    """Post one memory.write per note (skipping today's) and the heartbeat.

    Returns (notes posted, skipped as already posted today). The note's value is data: facts and the
    date they were consolidated, with the sources counted, never an instruction.
    """
    ledger.sync()
    view = getattr(ledger, "ledger", ledger)
    seen = set()
    for entry in view.entries:
        if getattr(entry, "sender", "") != AGENT:
            continue
        kind = getattr(getattr(entry, "kind", None), "value", getattr(entry, "kind", None))
        payload = payload_of(entry)
        if kind == EVENT and isinstance(payload, dict):
            seen.add(str(payload.get("key") or ""))
    posted = 0
    for note in notes:
        dated = f"{note.outbox_key}@{now:%Y%m%d}"
        if dated in seen:
            continue
        if posted >= MAX_ESCALATIONS:
            continue
        sources = sorted({i.sender or "?" for i in note.items})
        ledger.post(
            AGENT,
            EVENT,
            f"{AGENT}/{note.scope}@{now:%Y%m%d}",
            scope=AGENT,
            key=dated,
            value={"summary": summary_line([note]), "items": [i.text for i in note.items], "sources": sources},
        )
        posted += 1
    ledger.post(
        AGENT,
        "heartbeat",
        "",
        summary=summary_line(notes),
        flow=FLOW,
        notes=[
            {"scope": n.scope, "items": len(n.items), "deduped": n.deduped, "key": f"{n.outbox_key}@{now:%Y%m%d}"}
            for n in notes
        ],
        run_url=run_url,
    )
    return posted, len(notes) - posted


def open_gateway_ledger() -> Any:
    """Lestinxor's SupabaseLedger on the UniSync gateway, knowing wren as a watcher."""
    from lestinxor.unisync import DEFAULT_AGENTS, AgentCard, Role  # type: ignore[import-not-found]
    from lestinxor.unisync_supabase import SupabaseLedger  # type: ignore[import-not-found]

    agents = (*DEFAULT_AGENTS, AgentCard(AGENT, Role.WATCHER))
    return SupabaseLedger(url=os.environ[GATEWAY_ENV], key=os.environ[LEDGER_TOKEN_ENV], agents=agents)


# --- CLI ---------------------------------------------------------------------------------------


def main(
    argv: Sequence[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    open_ledger: Callable[[], Any] = open_gateway_ledger,
    now: datetime | None = None,
    out: TextIO = sys.stdout,
) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.wren", description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    cu = sub.add_parser("curate", help="consolidate the ledger's memory.write entries into notes")
    cu.add_argument("--summary", type=Path, help="write the Markdown report here (e.g. $GITHUB_STEP_SUMMARY)")
    cu.add_argument("--json", type=Path, help="write the notes as JSON here")
    cu.add_argument("--post", action="store_true", help="commit the notes to the ledger as memory.write")
    cu.add_argument("--run-url", default="")
    args = parser.parse_args(argv)
    env = dict(os.environ) if env is None else env
    now = now or datetime.now(UTC)

    if not env.get(GATEWAY_ENV) or not env.get(LEDGER_TOKEN_ENV):
        print(f"::error::Set {GATEWAY_ENV} and {LEDGER_TOKEN_ENV}.", file=out)
        return 2
    try:
        ledger = open_ledger()
    except Exception as exc:
        print(f"::error::could not open the ledger: {type(exc).__name__}", file=out)
        return 1
    ledger.sync()
    view = getattr(ledger, "ledger", ledger)
    entries = list(view.entries)
    notes = consolidate(read_items(entries))

    if args.post:
        posted, skipped = post(ledger, notes, now, args.run_url)
        print(f"committed {posted} note(s); {skipped} already committed today", file=out)
    report = markdown(notes, now)
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as fh:
            fh.write(report)
    else:
        print(report, file=out)
    if args.json:
        args.json.write_text(json.dumps([asdict(n) for n in notes], indent=2) + "\n", encoding="utf-8")
    print(summary_line(notes), file=out)
    return 0
