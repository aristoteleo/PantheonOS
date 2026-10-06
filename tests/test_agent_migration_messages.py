"""Inventory and image conversion must enforce the same per-message ceiling."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest

from pantheon.chatroom import migration_messages
from pantheon.chatroom.migration import inspect_legacy
from pantheon.chatroom.migration_images import converted_message_lines
from test_agent_migration import legacy


@pytest.mark.parametrize('ending', [b'\n', b''])
def test_exact_limit_and_unterminated_lines(ending, monkeypatch):
    monkeypatch.setattr(migration_messages, 'MAX_MESSAGE_BYTES', 32)
    row = b'x' * (32 - len(ending)) + ending
    assert list(migration_messages.message_lines(BytesIO(row))) == [row]
    with pytest.raises(ValueError, match='byte limit'):
        list(migration_messages.message_lines(BytesIO(b'x' + row)))


def test_history_size_does_not_change_bounded_reads(monkeypatch):
    monkeypatch.setattr(migration_messages, 'MAX_MESSAGE_BYTES', 32)
    class BoundedStream(BytesIO):
        def readline(self, size=-1):
            assert size == 33
            return super().readline(size)
    assert len(list(migration_messages.message_lines(BoundedStream(b'{}\n' * 100)))) == 100


def test_inventory_and_conversion_reject_same_oversized_message(legacy, tmp_path, monkeypatch):
    monkeypatch.setattr(migration_messages, 'MAX_MESSAGE_BYTES', 32)
    raw = b'{"content":"' + b'x' * 32 + b'"}\n'
    (Path(legacy['home_memory']) / 'chat-a.jsonl').write_bytes(raw)
    assert any(i['code'] == 'invalid_conversation' for i in inspect_legacy(**legacy)['issues'])
    blob = tmp_path / 'blob'
    blob.write_bytes(raw)
    item = {'blob': 'blob', 'size': len(raw), 'sha256': sha256(raw).hexdigest()}
    with pytest.raises(ValueError, match='byte limit'):
        list(converted_message_lines(tmp_path, item, {'roots': {}, 'paths': {}}))
