"""Private launch descriptions shared by terminal and native Desktop owners."""
from pathlib import Path

from pantheon.apps.dependency_assembly import AssemblyError
from .local_profile_update import UpdateJournal


class LaunchJournal(UpdateJournal):
    maximum_bytes = 16384


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
