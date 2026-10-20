"""Shared, bounded JSONL framing for inventory and captured-history conversion."""

# Real histories can contain >16 MiB inline images/tool results in one message.
# This is a per-line byte ceiling (including its newline), not a history limit.
# JSON parsing/encoding may use several times this amount; do not accumulate
# yielded lines or raise the independent metadata/whole-document limits with it.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024


def message_lines(stream):
    """Yield original bytes, rejecting oversized lines before JSON decoding."""
    while line := stream.readline(MAX_MESSAGE_BYTES + 1):
        if len(line) > MAX_MESSAGE_BYTES:
            raise ValueError('Legacy message exceeds the JSONL migration byte limit')
        yield line
