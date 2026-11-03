"""The Agent's skills, read without a conversation.

`pantheon skills list|view` outside a Pantheon Agent conversation (a person,
or a coding agent with PantheonOS access) reads the same skills a conversation
sees: the project's, the user's (global) and the ones shipped with the Agent.
Reading only: changing skills stays with the Agent's conversations.
"""
import re
from pathlib import Path

LIMIT = 500


def _store(settings):
    from pantheon.skills.store import SkillStore
    excluded = [s.strip() for s in (settings.get_env('PANTHEON_EXCLUDED_SKILLS', '') or '').split(',') if s.strip()]
    return SkillStore(Path(settings.skills_dir), Path(settings.pantheon_dir) / 'skills-runtime',
                      global_skills_dir=Path(settings.global_skills_dir),
                      factory_skills_dir=Path(settings.factory_skills_dir),
                      excluded_skills=excluded, create_dirs=False)


def list_skills(settings) -> dict:
    headers = sorted(_store(settings).scan_headers(limit=LIMIT), key=lambda h: h.path)
    return {'success': True, 'count': len(headers),
            'skills': [{'name': h.path, 'title': h.name, 'description': h.description, 'tags': h.tags,
                        'scope': h.scope} for h in headers]}


def view_skill(settings, name: str, file: str = '') -> dict:
    if not isinstance(name, str) or not name.strip():
        return {'success': False, 'error': 'Name a skill: pantheon skills view <name> (pantheon skills list shows them)'}
    store = _store(settings)
    entry = store.load_skill(name.strip())
    if entry is None:
        hint = ''
        try:
            suggestions = store.suggest_for(name)
        except Exception:
            suggestions = []
        commands = []
        for suggestion in suggestions:
            found = re.search(r"name='([^']*)'(?:, file_path='([^']*)')?", suggestion)
            if found:
                commands.append(' '.join(['pantheon skills view', *filter(None, found.groups())]))
        if commands:
            hint = ' Try: ' + ' | '.join(commands) + '.'
        return {'success': False,
                'error': f"No skill '{name}'.{hint} pantheon skills list shows the skills and their names."}
    if file:
        try:
            content = store.load_file(entry.path, file)
        except ValueError as error:
            return {'success': False, 'error': str(error)}
        if content is None:
            files = ', '.join(_files(entry)) or 'none'
            return {'success': False, 'error': f"No file '{file}' in skill '{entry.path}' (its files: {files})"}
        return {'success': True, 'name': entry.path, 'file': file, 'content': content}
    result = {'success': True, 'name': entry.path, 'title': entry.name, 'description': entry.description,
              'scope': entry.scope, 'content': entry.content}
    if files := _files(entry):
        result['files'] = files
        result['hint'] = f'Read a file of this skill: pantheon skills view {entry.path} <file>'
    return result


def _files(entry) -> list[str]:
    linked = entry.linked_files or {}
    if isinstance(linked, dict):
        return [name if bucket == '.' else f'{bucket}/{name}' for bucket, names in linked.items() for name in names]
    return list(linked)
