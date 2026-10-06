"""Package existing filesystem operations as an independent native Fleet App.

No Agent, settings discovery, model SDK, Hub client or remote ToolSet bus is
included. Legacy builtins remain unchanged; this is an opt-in Files package.
"""
import argparse
import json
from pathlib import Path
import shutil
import tempfile


def build(output: Path, platform: str, *, model_sampling=False, image_generation=False, transport=None):
    if transport is not None and not (model_sampling or image_generation):
        raise ValueError('A model transport requires a model-assisted package variant')
    from pantheon.apps.portable import definition
    from pantheon.apps.schema import parse_manifest
    from pantheon.apps.builtin.file.managed import METHODS
    source = Path(__file__).resolve().parent
    runtime = source.parents[1] / 'pantheon'
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.files-build-', dir=output.parent) as temp:
        package = Path(temp) / 'package'
        package.mkdir()
        manifest = json.loads((source / 'app.json').read_text())
        manifest.update(version='0.6.15', runtime='process', surface='headless',
                        execution={'protocol': 1, 'manifest': 'fleet.json'})
        manifest['entry'] = {'backend': 'backend/__init__.py'}
        methods = METHODS | {'observe_images'} if model_sampling else METHODS
        if image_generation:
            methods = methods | {'generate_image'}
        manifest['provides']['tools'] = [t for t in manifest['provides']['tools'] if t['name'] in methods]
        if model_sampling:
            observed = next(t for t in manifest['provides']['tools'] if t['name'] == 'observe_images')
            observed['params'] = [p for p in observed['params'] if p['name'] != 'node_id']
            for parameter in observed['params']:
                parameter['required'] = True
                parameter.pop('default', None)
            observed['description'] = 'Observe raster images in this Files workspace through its bound Model Services dependency.'
            manifest['provides']['interfaces'].append({'name': 'image-observation', 'version': 1, 'tools': ['observe_images']})
        if image_generation:
            generated = next(t for t in manifest['provides']['tools'] if t['name'] == 'generate_image')
            generated['description'] = 'Generate or edit workspace images through explicitly bound Model Services; selectors must be configured aliases or Fleet references.'
            for parameter in generated['params']:
                parameter['required'] = parameter['name'] == 'prompt'
                if parameter['required']:
                    parameter.pop('default', None)
            manifest['provides']['interfaces'].append({'name': 'image-generation', 'version': 1, 'tools': ['generate_image']})
        if model_sampling or image_generation:
            manifest['dependencies'] = {'model-services-control': {'uses': ['model-inference@1']}}
        manifest['provides']['interfaces'].append({'name': 'image-preview', 'version': 1,
                                                 'tools': ['fetch_image_base64']})
        # The legacy fs/outline contracts cover only a subset of the public
        # service. Publish the remaining file operations for ordinary consumers
        # without changing those existing contracts or granting image inference.
        covered = {name for interface in manifest['provides']['interfaces'] for name in interface['tools']}
        remaining = sorted(methods - covered)
        if remaining:
            manifest['provides']['interfaces'].append({'name': 'file-management', 'version': 1,
                                                       'tools': remaining})
        manifest['notes'] = 'Prepared filesystem service. Model-assisted capabilities use explicit Model Services dependencies.'
        parse_manifest(manifest)
        (package / 'app.json').write_text(json.dumps(manifest, indent=2)+'\n')
        vendor = package / 'backend/_vendor/pantheon'
        modules = ('toolset.py', 'utils/log.py', 'utils/misc.py', 'utils/file_paths.py', 'utils/vision.py',
                   'apps/runtime_config.py', 'apps/toolset_backend.py',
                   'internal/package_runtime/context.py', 'remote/backend/base.py')
        for name in modules:
            dest = vendor / name; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(runtime / name, dest)
        shutil.copytree(runtime / 'funcdesc', vendor / 'funcdesc', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for name in ('file_manager.py','apply_patch.py','grep_glob.py','tree_sitter_parser.py','managed.py'):
            dest = vendor / 'apps/builtin/file' / name; dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, dest)
        if image_generation:
            shutil.copyfile(source / 'image_generation.py', vendor / 'apps/builtin/file/image_generation.py')
        dest = vendor / 'apps/builtin/fleet/local_node.py'; dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source.parent / 'fleet/local_node.py', dest)
        for name in ('', 'utils', 'apps', 'apps/builtin', 'apps/builtin/file', 'apps/builtin/fleet',
                     'internal', 'internal/package_runtime', 'remote', 'remote/backend'):
            (vendor / name / '__init__.py').write_text('')
        entry = 'register_sampling as register' if model_sampling else 'register'
        (package / 'backend/__init__.py').write_text(
            'from pantheon.apps.builtin.file.managed import register_capabilities\n'
            f'async def register(ctx):\n    await register_capabilities(ctx, observation={model_sampling!r}, generation=True)\n'
            if image_generation else f'from pantheon.apps.builtin.file.managed import {entry}\n')
        (package / 'requirements.txt').write_text('loguru==0.7.3\nrich==14.3.2\npydantic==2.12.5\n'
            'diff-match-patch==20241021\ntree-sitter==0.25.2\ntree-sitter-python==0.25.0\ntree-sitter-javascript==0.25.0\npillow==12.1.0\n')
        if model_sampling or image_generation:
            from pantheon.models.package import bundle_client
            bundle_client(vendor, platform=platform, transport=transport)
            shutil.copyfile(runtime / 'apps/model_sampling.py', vendor / 'apps/model_sampling.py')
            with (package / 'requirements.txt').open('a') as stream:
                stream.write('httpx==0.28.1\n')
        adapter = package / '.fleet-runtime'; adapter.mkdir()
        shutil.copyfile(source.parent / 'desktop/app_runtime.py', adapter / 'app_runtime.py')
        for name in ('host.py','install.py','launch.py'):
            shutil.copyfile(runtime / 'apps/portable_runtime' / name, adapter / name)
        execution = definition(manifest, platform)
        execution['components'][0]['configuration'] = {'values': {'files': {'required': True}}}
        if model_sampling:
            execution['components'][0]['configuration']['values']['sampling'] = {'required': True}
        if image_generation:
            execution['components'][0]['configuration']['values']['image_generation'] = {'required': True}
        if model_sampling or image_generation:
            # Configured capabilities validate their credential before RPC
            # admission; explicitly unconfigured ones need no model authority.
            execution['components'][0]['configuration']['credentials'] = {'models': {'required': False}}
        execution['hooks']['before_stop']['component'] = 'backend'
        (package / 'fleet.json').write_text(json.dumps(execution, indent=2)+'\n')
        (package / 'README.md').write_text(
            '# Prepared Files App\n\n'
            'This Fleet package exports its reviewed app.json surface. Supply an absolute existing '
            'workspace in prepared values.files; it does not discover Agent settings.\n\n'
            'Both sampling and image_generation bindings accept optional trust_roots_pem '
            'for the selected Model Services dependency. Invalid explicit trust fails startup; '
            'no process-wide certificate environment changes are needed.\n\n'
            'An owner can explicitly defer either model capability with {"state":"unconfigured"} '
            'as its entire configuration value. Its tool stays exported and returns model_not_configured '
            'without reading image inputs or calling a model. Missing or malformed values still fail startup. '
            'Enabling a capability requires a new owner-prepared configuration and App start, not a '
            'package replacement. The local product profile currently rejects changes to its saved '
            'composition; a reviewed profile-update flow remains required there. When both capabilities '
            'are unconfigured, no models credential is required.\n\n'
            + ('The model-sampling variant additionally exposes workspace-local raster observe_images. '
               'Supply values.sampling with credential="models", an explicit Fleet model or route, '
               'max_tokens (1..32768) and max_requests_per_call (1..16). Fleet must issue the models '
               'credential through an ordinary model-inference dependency for this Files instance. '
               'Keep shared Files model access independent of any one Agent lifetime. '
               'No parent history, ambient provider fallback or model SDK is included. '
               'Cross-node image references and PDF inspection are not yet exported.\n' if model_sampling else
               'The base surface exposes filesystem operations and image previews. '
               'Use the explicit sampling variant when model-assisted image observation is needed.\n'))
        if image_generation:
            with (package / 'README.md').open('a') as stream:
                stream.write('\nImage generation adds generate_image and image-generation@1. Supply values.image_generation '
                    'with credential="models", model (an explicit Fleet reference), aliases (selector-to-Fleet-reference '
                    'mapping), and timeout_seconds (1..600). Include every selected image model in the Files-owned '
                    'model grant. Reference PNG/JPEG/WebP files must be in the Files workspace. Results are saved '
                    'under generated-images after checksum and image verification. Failed jobs are never retried; '
                    'job_ref identifies the pinned service for inspection. Successfully copied jobs are removed; '
                    'cleanup_pending reports remote cleanup still needed. Private image-jobs records in the App '
                    'state directory retain job/upload identities across Files restart without storing prompts or '
                    'credentials; restart does not resubmit jobs. At 128 retained records, reconcile them before '
                    'admitting more work. Successful results include the ordinary bounded Files image preview. '
                    'The API connector supports OpenAI-compatible '
                    'Images generation/editing; SGLang supports generation only. Native Gemini image calls and legacy '
                    'ambient model-name discovery remain migration work.\n')
        package.rename(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--platform', required=True)
    parser.add_argument('--model-sampling', action='store_true')
    parser.add_argument('--image-generation', action='store_true')
    parser.add_argument('--transport', type=Path)
    args = parser.parse_args()
    build(args.output, args.platform, model_sampling=args.model_sampling,
          image_generation=args.image_generation, transport=args.transport)
