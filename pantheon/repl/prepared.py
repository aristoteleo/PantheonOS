"""CLI client for the same prepared Agent App used by Fleet.

The launcher supplies identity, dependencies, model credentials and a private
data mount. This adapter does not invent grants, provision a Fleet node, adopt
legacy data or fall back to ambient settings. Bundled local infrastructure and
legacy data conversion are separate launcher responsibilities.
"""
from datetime import datetime
from pathlib import Path
import asyncio
import shlex

from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.chatroom.launch import ConfiguredAgentApplication
from pantheon.dependency_provider import _drain_call
from .core import Repl


def select_resume(chats, selection):
    """Preserve CLI recency/index/ID/name-prefix selection over App-owned data."""
    def activity(chat):
        try:
            return datetime.fromisoformat(chat.get('last_activity_date')).timestamp()
        except (TypeError, ValueError, OverflowError):
            return float('-inf')
    rows = sorted(chats, key=activity, reverse=True)
    if not rows:
        raise ValueError('No chat sessions found in this Agent App')
    if selection is True:
        return rows[0]['id']
    value = str(selection)
    if value.isdigit() and 1 <= int(value) <= len(rows):
        return rows[int(value) - 1]['id']
    for row in rows:
        if row['id'].startswith(value) or str(row.get('name') or '').lower().startswith(value.lower()):
            return row['id']
    raise ValueError('Chat not found in this Agent App')


class PreparedRepl(Repl):
    def __init__(self, app, *, template=None, chat_id=None, resume=False, model=None):
        self._template_path = Path(template).expanduser().resolve() if template else None
        self._resume_selection, self._requested_model = resume, model
        self._resume_selected = False
        super().__init__(chatroom=app, chat_id=chat_id,
                         history_file=app.app_data.root / 'cli' / 'history')
        self.resume_command = 'pantheon cli --app-data ' + shlex.quote(str(app.app_data.root)) + ' --resume'

    async def _prepare_chat(self):
        app = self._chatroom
        if self._requested_model is not None:
            valid, message = app._validate_model_provider(self._requested_model)
            if not valid:
                raise ValueError(message)
        if self._resume_selection is not False and self._resume_selection is not None:
            result = await app.list_chats()
            if not result.get('success'):
                raise RuntimeError('Cannot list Agent App conversations for resume')
            self._chat_id = select_resume(result['chats'], self._resume_selection)
        elif self._chat_id is not None:
            result = await app.list_chats()
            if not result.get('success') or not any(row['id'] == self._chat_id for row in result['chats']):
                raise ValueError('Chat not found in this Agent App')
        self._resume_selected = self._chat_id is not None
        if self._template_path is not None:
            template = app.template_manager.parse_template_content(
                self._template_path.read_text(encoding='utf-8'), file_path=self._template_path)
            if self._chat_id is None:
                result = await app.create_chat('repl-session', template_obj=template.to_dict())
                if not result.get('success'):
                    raise RuntimeError('Cannot create Agent App conversation with this template')
                self._chat_id = result['chat_id']
            else:
                result = await app.setup_team_for_chat(self._chat_id, template.to_dict())
                if not result.get('success'):
                    raise RuntimeError('Cannot apply this template to the resumed conversation')

    async def _assemble_team(self):
        await super()._assemble_team()
        if self._requested_model is not None:
            first = next(iter(self._team.agents), None)
            if first is None:
                raise ValueError('No Agent available for the requested model')
            result = await self._chatroom.set_agent_model(
                chat_id=self._chat_id, agent_name=first, model=self._requested_model)
            if not result.get('success'):
                raise ValueError('Cannot apply the requested model to this Agent')


async def run_prepared(*, app_data, template=None, workspace=None, chat_id=None,
                       resume=False, resync=False, message=None, model=None,
                       log_level=None, quiet=None):
    configuration = load_runtime_configuration(required=True)
    # Same owned composition and data admission as the ordinary App backend.
    app = ConfiguredAgentApplication('agent', data_dir=Path(app_data).expanduser().absolute(),
                                     configuration=configuration, allow_in_place_restart=False)
    client_owns_cleanup = False
    try:
        if workspace is not None:
            active = app.app_data.projects.active_project
            if active is None or Path(workspace).expanduser().absolute() != Path(active.path):
                raise ValueError('--workspace must match this prepared App; prepare a new project binding to change it')
        if resync:
            app.template_manager.force_sync_factory_templates()
        settings = app._settings()
        quiet = quiet if quiet is not None else settings.get('repl.quiet', False)
        log_level = log_level or settings.get('repl.log_level', 'CRITICAL')
        from pantheon.utils.log import set_level
        set_level(log_level)
        repl = PreparedRepl(app, template=template, chat_id=chat_id, resume=resume, model=model)
        client_owns_cleanup = True
        await repl.run(message=message, once=message is not None,
                       disable_logging=bool(quiet and log_level != 'DEBUG'), log_level=log_level)
    finally:
        if not client_owns_cleanup:
            await _drain_call(asyncio.create_task(app.cleanup()))
