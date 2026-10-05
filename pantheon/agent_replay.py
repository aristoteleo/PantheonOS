"""Immutable frontend state for the Agent App's ordinary event replay protocol."""
from dataclasses import dataclass, field
import json
import math

from pantheon.agent_client import AgentClientError


class ReplayResetRequired(AgentClientError):
    """Retention/epoch changed. Reload saved history, never replay a user prompt."""


@dataclass(frozen=True)
class EventFragment:
    event_id: str
    total: int
    fragments: tuple[str, ...] = ()
    size: int = 0


@dataclass(frozen=True)
class ReplayState:
    chat_id: str
    epoch: str
    sequence: int
    pending: EventFragment | None = None
    completed_ids: frozenset[str] = field(default_factory=frozenset)


def position(value):
    if (not isinstance(value, dict) or set(value) != {'epoch', 'sequence'}
            or not isinstance(value['epoch'], str) or not value['epoch']
            or type(value['sequence']) is not int or not 0 <= value['sequence'] < 2**63):
        raise AgentClientError('Invalid Agent replay cursor')
    return value['epoch'], value['sequence']


def event(value, chat_id):
    if (not isinstance(value, dict) or not isinstance(value.get('type'), str)
            or type(value.get('timestamp')) not in (int, float) or not math.isfinite(value['timestamp'])
            or not isinstance(value.get('data'), dict) or value['data'].get('chat_id') != chat_id
            or value['type'] == 'chunk' and not isinstance(value['data'].get('chunk'), dict)):
        raise AgentClientError('Invalid Agent event or conversation identity')
    if value['type'] in ('chunk', 'tool_delta'):
        delta = value['data']['chunk'] if value['type'] == 'chunk' else value['data']
        if delta.get('message_id') is not None and not isinstance(delta['message_id'], str):
            raise AgentClientError('Invalid Agent event message identity')
    return value


def decode_page(state, page, *, max_event_bytes=64 * 1024 * 1024):
    """Validate the entire page before advancing the caller's partial event.

    Event sequences are global across chats, so they must increase, but need not
    be consecutive. A partial JSON event can span pages and is never displayed.
    Completed history message IDs suppress overlapping text/tool argument deltas.
    """
    if type(max_event_bytes) is not int or max_event_bytes <= 0:
        raise ValueError('Supply a positive event size limit')
    if (not isinstance(page, dict) or type(page.get('protocol')) is not int or page['protocol'] != 1
            or page.get('chat_id') != state.chat_id or type(page.get('reset_required')) is not bool):
        raise AgentClientError('Invalid Agent replay page')
    if page['reset_required']:
        raise ReplayResetRequired('Agent replay expired; restore history before continuing observation')
    if (type(page.get('has_more')) is not bool or not isinstance(page.get('events'), list)
            or len(page['events']) > 128):
        raise AgentClientError('Invalid Agent replay page')
    epoch, sequence = position(page.get('cursor'))
    if epoch != state.epoch or sequence < state.sequence:
        raise AgentClientError('Agent replay cursor changed without a reset')
    previous, pending, events, size = state.sequence, state.pending, [], 0
    for row in page['events']:
        if (not isinstance(row, dict) or type(row.get('sequence')) is not int
                or not previous < row['sequence'] <= sequence
                or not isinstance(row.get('event_id'), str) or not row['event_id']
                or type(row.get('total')) is not int or not 0 < row['total'] <= max_event_bytes
                or type(row.get('part')) is not int or not 0 <= row['part'] < row['total']
                or not isinstance(row.get('json_fragment'), str)):
            raise AgentClientError('Invalid Agent event fragment')
        text = row['json_fragment']
        if not text or len(text) > 16 * 1024 or not text.isascii():
            raise AgentClientError('Invalid Agent event fragment encoding')
        size += len(text)
        if size > 128 * 1024:
            raise AgentClientError('Agent replay page exceeds its size limit')
        previous = row['sequence']
        if pending is None:
            if row['part'] != 0:
                raise AgentClientError('Agent replay begins inside an unknown event')
            pending = EventFragment(row['event_id'], row['total'])
        if (pending.event_id != row['event_id'] or pending.total != row['total']
                or len(pending.fragments) != row['part'] or pending.size + len(text) > max_event_bytes):
            raise AgentClientError('Agent event is incomplete or exceeds the requested size limit')
        pending = EventFragment(pending.event_id, pending.total, pending.fragments + (text,), pending.size + len(text))
        if len(pending.fragments) == pending.total:
            try:
                value = event(json.loads(''.join(pending.fragments)), state.chat_id)
            except (ValueError, UnicodeError):
                raise AgentClientError('Invalid Agent event JSON') from None
            delta = value['data']['chunk'] if value['type'] == 'chunk' else value['data']
            if (value['type'] not in ('chunk', 'tool_delta')
                    or delta.get('message_id') not in state.completed_ids):
                events.append(value)
            pending = None
    if not page['has_more'] and pending is not None:
        raise AgentClientError('Agent replay ended inside an unfinished event')
    if page['has_more'] and (not page['events'] or sequence != previous):
        raise AgentClientError('Agent replay made no progress')
    return {'events': events, 'state': ReplayState(state.chat_id, epoch, sequence, pending, state.completed_ids),
            'has_more': page['has_more']}
