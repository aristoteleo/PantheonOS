"""Join prepared releases, an Agent profile and control references for UI review.

This produces the existing model_services_agent_preset input. It does not select
models, alter tool/plugin settings, install Apps or change a saved startup preset.
"""
import argparse
import json
import os
from pathlib import Path

from pantheon.apps.agent_deployment import compose_deployment
from pantheon.apps.dependency_assembly import AssemblyError, _copy


def prepare_setup(*, targets, profile, control_credentials, operation_id):
    targets, profile, controls = _copy([targets, profile, control_credentials])
    if (not isinstance(profile, dict) or not {'agent', 'tools'} <= profile.keys()
            or profile.keys() - {'agent', 'tools', 'agent_credentials', 'extra_bindings', 'provider_apps'}
            or not isinstance(controls, dict)
            or set(controls) != {'protocol', 'owner', 'source', 'nodes'}
            or type(controls['protocol']) is not int or controls['protocol'] != 1
            or controls['source'] != 'platform-key' or not isinstance(controls['nodes'], dict)):
        raise AssemblyError('Supply the complete Agent profile and owner control credential descriptor')
    try:
        allocator = controls['nodes'][targets['allocator']['node_id']]
        models = controls['nodes'][targets['model-access']['node_id']]
        if set(allocator) != {'hub', 'controller'} or set(models) != {'hub', 'controller'}:
            raise ValueError
        spec = dict(owner=controls['owner'], operation_id=operation_id, targets=targets,
            agent=profile['agent'], tools=profile['tools'], credentials={
                'agent': profile.get('agent_credentials', {}), 'allocator': allocator,
                'model-access': {'hub': models['hub']}})
        for key in ('extra_bindings', 'provider_apps'):
            if key in profile:
                spec[key] = profile[key]
        # Use the existing composition validator; there is no parallel recipe
        # schema. Empty model policy here is not a runnable default or permission.
        # The UI must select and review actual directory publications afterward.
        compose_deployment(**spec, models={'deployments': {}, 'routes': {}, 'allow_wake': False})
        return _copy(spec)
    except (KeyError, TypeError, ValueError):
        raise AssemblyError('Setup targets, profile or node credential references do not match') from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('targets', 'profile', 'control-credentials', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--operation-id', required=True)
    args = parser.parse_args()
    try:
        if not args.output.is_absolute() or args.output.exists() or args.output.is_symlink():
            raise ValueError
        inputs = {}
        for name in ('targets', 'profile', 'control_credentials'):
            with getattr(args, name).open('rb') as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError
            inputs[name] = json.loads(raw)
        result = prepare_setup(**inputs, operation_id=args.operation_id)
        with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
    except (OSError, ValueError, AssemblyError):
        parser.exit(1, 'Could not prepare Agent setup. Check exact release targets, the complete profile '
            'and provisioned control nodes. No existing output was replaced.\n')


if __name__ == '__main__':
    main()
