"""Owner-side scalar edits for imported Agent/Team model declarations.

Template instructions are data. Never serialize an entire template through YAML:
doing so loses comments, formatting, and instruction section boundaries.
"""
import json
import os
from pathlib import Path
import re

import frontmatter
from frontmatter.default_handlers import YAMLHandler
import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode


def apply_edits(raw, edits):
    text = raw.decode('utf-8')
    for start, end, replacement in reversed(edits):
        text = text[:start] + replacement + text[end:]
    return text.encode('utf-8')


def template_edits(raw, *, path, selection=None, relocations=None):
    """Return bounded character edits and consumed explicit mapping identities."""
    text = raw.decode('utf-8')
    stripped = text.lstrip()
    handler = frontmatter.detect_format(stripped, frontmatter.handlers)
    if handler is None:
        return [], set()  # Plain notes in a template directory are not recipes.
    if not isinstance(handler, YAMLHandler):
        raise ValueError('Template model migration requires YAML frontmatter; convert this template explicitly')
    offset = len(text) - len(stripped)
    boundaries = list(re.finditer(r'^-{3,}[^\S\r\n]*\r?$', stripped, re.MULTILINE))
    if len(boundaries) < 2 or boundaries[0].start() != 0:
        raise ValueError('Template frontmatter is incomplete')
    start, end = boundaries[0].end(), boundaries[1].start()
    header = stripped[start:end]
    if len(header.encode('utf-8')) > 1024 * 1024:
        raise ValueError('Template frontmatter exceeds its conversion limit')
    try:
        # Reject alias/merge ambiguity and excessive nesting before composition.
        depth = 0
        for count, event in enumerate(yaml.parse(header, Loader=yaml.SafeLoader)):
            if count > 10000 or getattr(event, 'anchor', None) is not None:
                raise ValueError('Template frontmatter aliases or complexity require explicit conversion')
            if isinstance(event, (yaml.MappingStartEvent, yaml.SequenceStartEvent)):
                depth += 1
                if depth > 64:
                    raise ValueError('Template frontmatter nesting exceeds its conversion limit')
            elif isinstance(event, (yaml.MappingEndEvent, yaml.SequenceEndEvent)):
                depth -= 1
        root = yaml.compose(header, Loader=yaml.SafeLoader)
    except yaml.YAMLError:
        raise ValueError('Invalid template frontmatter') from None

    def mapping(node):
        if not isinstance(node, MappingNode):
            raise ValueError('Template metadata must be a mapping')
        result = {}
        for key, value in node.value:
            name = string(key)
            if name in result or name == '<<':
                raise ValueError('Ambiguous template metadata requires explicit conversion')
            result[name] = value
        return result

    def string(node):
        if not isinstance(node, ScalarNode) or node.tag != 'tag:yaml.org,2002:str':
            raise ValueError('Template identities and models must be strings')
        return node.value

    def check(node):
        if isinstance(node, MappingNode):
            for value in mapping(node).values():
                check(value)
        elif isinstance(node, SequenceNode):
            for value in node.value:
                check(value)

    check(root)
    metadata = mapping(root)
    if 'id' not in metadata or not string(metadata['id']).strip():
        raise ValueError('Template requires a stable identity before model migration')
    edits, used, ids = [], set(), set()

    def replace(node, target):
        replacement = json.dumps(target, ensure_ascii=False)
        token = header[node.start_mark.index:node.end_mark.index]
        if node.style in ('|', '>'):
            # A block scalar consumes its final line break.
            replacement += '\r\n' if token.endswith('\r\n') else '\n' if token.endswith('\n') else ''
        edits.append((offset + start + node.start_mark.index,
                      offset + start + node.end_mark.index, replacement))

    def reference(node):
        source = string(node)
        # Match FileBasedTemplateManager: namespaced IDs are not file paths.
        if relocations is None or not (source.startswith(('/', './', '../')) or source.endswith('.md')):
            return
        resolved = str((Path(path).parent / source).resolve())
        if resolved not in relocations or path not in relocations:
            raise ValueError('Team path reference is outside the backed-up template library; include it before migration')
        destination = relocations[resolved]
        # Preserve relative spelling when its meaning survives relocation. For
        # cross-root references, write a new relative path within App-owned data.
        if not Path(source).is_absolute():
            base = Path(relocations[path]).parent
            if os.path.normpath(base / source) == destination:
                return
            destination = os.path.relpath(destination, base)
            if not destination.startswith('.'):
                destination = './' + destination
        if destination != source:
            replace(node, destination)

    def agent(fields, default_id=None):
        identity = string(fields['id']).strip() if 'id' in fields else default_id
        if not identity or identity in ids:
            raise ValueError('Template member identities are missing or duplicated')
        ids.add(identity)
        if selection is None:
            return
        if 'model' not in fields:
            selection.template_model(path, identity, '')
            return  # Retain the original implicit/inherited-model declaration.
        node = fields['model']
        source = string(node)
        target, consumed = selection.template_model(path, identity, source)
        if consumed is not None:
            used.add(consumed)
        if target != source:
            replace(node, target)

    kind = string(metadata['type']).lower() if 'type' in metadata else ''
    if kind in ('team', 'chatroom'):
        agents = metadata.get('agents')
        if not isinstance(agents, SequenceNode):
            raise ValueError('Team migration requires a list of member IDs or paths')
        entries = [string(entry) for entry in agents.value]
        if len(entries) != len(set(entries)):
            raise ValueError('Team member references are duplicated')
        for entry, node in zip(entries, agents.value):
            if isinstance(metadata.get(entry), MappingNode):
                agent(mapping(metadata[entry]), entry)
            else:
                reference(node)
    else:
        agent(metadata)
    return sorted(edits), used
