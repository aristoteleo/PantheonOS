"""Private launch descriptions shared by terminal and native Desktop owners.

A launch names the product bundle, the owner's setup, the profile directory,
the workspace and (optionally) the private credential file. Starting it saves
the composed deployment at the profile's controller.
"""
import json
from pathlib import Path

from pantheon.apps.dependency_assembly import AssemblyError, DEPLOYMENT_BYTES
from pantheon.apps.owner_journal import OwnerJournal


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate field')
        result[key] = value
    return result


class PrivateJournal(OwnerJournal):
    error_type = AssemblyError
    maximum_bytes = 2 * DEPLOYMENT_BYTES + 4096

    def read(self, path):
        path = Path(path)
        self._private(path)
        with path.open('rb') as stream:
            raw = stream.read(self.maximum_bytes + 1)
        if len(raw) > self.maximum_bytes:
            raise AssemblyError('Private local record exceeds its storage limit')
        try:
            return json.loads(raw, object_pairs_hook=unique_fields)
        except (ValueError, TypeError, RecursionError):
            raise AssemblyError('Invalid private local record') from None


class LaunchJournal(PrivateJournal):
    maximum_bytes = 16384


def private_json(path):
    path = Path(path)
    return PrivateJournal(path.parent).read(path)


def validate_launch(value):
    required = {'protocol', 'launcher', 'bundle', 'setup', 'profile', 'workspace'}
    if (not isinstance(value, dict) or not required <= value.keys()
            or value.keys() - required - {'credentials'}
            or type(value['protocol']) is not int or value['protocol'] != 1
            or not isinstance(value['launcher'], list) or not 1 <= len(value['launcher']) <= 8
            or any(not isinstance(v, str) or not v or len(v) > 4096 or '\0' in v for v in value['launcher'])
            or not Path(value['launcher'][0]).is_absolute()
            or any(not isinstance(value[k], str) or not value[k] or len(value[k]) > 4096
                   or '\0' in value[k] or not Path(value[k]).is_absolute()
                   for k in ('bundle', 'setup', 'profile', 'workspace', *(['credentials'] if 'credentials' in value else [])))):
        raise AssemblyError('Use a private launch description with explicit absolute product and workspace paths')
    return value


def read_launch(path):
    path = Path(path)
    if not path.is_absolute():
        raise AssemblyError('Choose an absolute launch description path')
    return validate_launch(LaunchJournal(path.parent).read(path))
