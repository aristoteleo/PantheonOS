"""A conversation as an append-only tree (docs/agent-core.md).

The log (one JSON record per line) is never rewritten:

* A message record carries ``id`` and ``parent_id``. Appending a message makes
  it a child of the current head and moves the head to it.
* A head record ``{"_head": <id or null>}`` moves the head without adding a
  message: revert moves it to the parent of a message, switching branches
  moves it to a leaf of another branch.
* A message record whose ``id`` is already in the tree is a revision of that
  message (the latest wins); it keeps the node's place in the tree.

The conversation is the path from the root to the head. Messages off that
path are earlier branches, kept so they can be returned to.

Lines written before this format have no ``parent_id``: each follows the
message before it (the conversation was a list). One without an ``id`` is
named after its line.
"""
from __future__ import annotations

import time

HEAD = "_head"


def is_head_record(record: dict) -> bool:
    return isinstance(record, dict) and HEAD in record and "role" not in record


class ConversationTree:
    def __init__(self):
        self.records: list[dict] = []          # the log, in order
        self.nodes: dict[str, dict] = {}       # id -> current record of the message
        self.order: dict[str, int] = {}        # id -> creation order
        self.children: dict[str | None, list[str]] = {}
        self.head: str | None = None
        self.path: list[dict] = []             # root → head

    # -- building --------------------------------------------------------------

    @classmethod
    def from_log(cls, lines: list[dict]) -> "ConversationTree":
        tree = cls()
        for number, record in enumerate(lines):
            if not isinstance(record, dict):
                continue
            if is_head_record(record):
                tree._set_head(record.get(HEAD))
                tree.records.append(record)
                continue
            if record.get("role") is None:
                continue  # a partial line from an interrupted write
            if "parent_id" not in record:
                # Written before the tree: the next item of a list.
                record = {**record, "parent_id": tree.head}
                if not record.get("id") or record["id"] in tree.nodes:
                    record["id"] = f"legacy-{number}"
            tree._add(record)
        tree._rebuild_path()
        return tree

    @classmethod
    def from_messages(cls, messages: list[dict]) -> "ConversationTree":
        """A tree whose conversation is exactly messages (a list)."""
        tree = cls()
        for message in messages:
            if isinstance(message, dict) and message.get("role") is not None:
                tree.append(dict(message))
        return tree

    def _add(self, record: dict) -> None:
        self.records.append(record)
        node_id = record["id"]
        if node_id in self.nodes:  # a revision
            self.nodes[node_id] = record
            return
        self.nodes[node_id] = record
        self.order[node_id] = len(self.order)
        self.children.setdefault(record.get("parent_id"), []).append(node_id)
        self.head = node_id

    def _set_head(self, node_id: str | None) -> None:
        self.head = node_id if node_id in self.nodes else None

    def _rebuild_path(self) -> None:
        path, seen, node = [], set(), self.head
        while node is not None and node in self.nodes and node not in seen:
            seen.add(node)
            record = self.nodes[node]
            path.append(record)
            node = record.get("parent_id")
        path.reverse()
        self.path = path

    # -- changes (each appends records; the caller persists them) --------------

    def append(self, message: dict) -> dict:
        """Add a message after the head (or revise an existing one)."""
        if message.get("id") in self.nodes:
            return self.revise(message)
        message["parent_id"] = self.head
        self._add(message)
        self.path.append(message)
        return message

    def revise(self, message: dict) -> dict:
        """A new version of an existing message, same place in the tree."""
        old = self.nodes[message["id"]]
        message = {**message, "parent_id": old.get("parent_id")}
        self._add(message)
        self.path = [message if m.get("id") == message["id"] else m for m in self.path]
        return message

    def move_head(self, node_id: str | None) -> dict:
        if node_id is not None and node_id not in self.nodes:
            raise KeyError(node_id)
        record = {HEAD: node_id, "timestamp": time.time()}
        self.records.append(record)
        self.head = node_id
        self._rebuild_path()
        return record

    # -- reading ---------------------------------------------------------------

    def latest_leaf(self, node_id: str) -> str:
        """The most recent message of the branch that starts at node_id."""
        node = node_id
        while self.children.get(node):
            node = max(self.children[node], key=self.order.__getitem__)
        return node

    def siblings(self, node_id: str) -> list[str]:
        """The messages sharing node_id's parent, oldest first (node_id included)."""
        return list(self.children.get(self.nodes[node_id].get("parent_id"), [node_id]))

    def branches(self) -> dict[str, dict]:
        """For each message on the path where the conversation branched: its
        alternatives (index, count, ids, oldest first)."""
        out: dict[str, dict] = {}
        for message in self.path:
            node_id = message.get("id")
            alternatives = self.siblings(node_id)
            if len(alternatives) > 1:
                out[node_id] = {"index": alternatives.index(node_id), "count": len(alternatives),
                                "siblings": alternatives}
        return out
