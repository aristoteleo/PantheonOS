"""Prepared filesystem service. The legacy combined toolset remains available.

The sampling variant adds image inspection through an explicit Model Services
dependency, without an embedded Agent. A workspace is a default path, not an OS sandbox: owner grants must bound
paths or deploy this service under an appropriately restricted OS identity.
"""
from collections.abc import Mapping
import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace

from pantheon.apps.runtime_config import load_runtime_configuration
from pantheon.apps.toolset_backend import register_toolset
from pantheon.toolset import tool
from .file_manager import FileManagerToolSet

METHODS = frozenset(('read_file', 'write_file', 'update_file', 'glob', 'grep', 'apply_patch',
    'view_file_outline', 'list_files', 'stat_path', 'get_cwd', 'manage_path',
    'create_directory', 'delete_path', 'move_file', 'fetch_image_base64'))


def unconfigured_model(capability):
    label = 'image observation' if capability == 'sampling' else 'image generation'
    return {'success': False, 'code': 'model_not_configured', 'capability': capability,
            'configuration_path': f'files.{capability}',
            'error': f'No model is configured for {label}. Select a Model Services model '
                     f'for files.{capability} through an owner-reviewed configuration update.'}


class ManagedFiles(FileManagerToolSet):
    """Preview files on this provider, without an ambient Agent image store."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._preview_slots = asyncio.Semaphore(2)
        self._image_generation = None
        self._unconfigured_models = frozenset()

    @tool
    async def generate_image(self, prompt: str, reference_images: list[str] | None = None,
                             model: str | None = None, model_args: dict | None = None) -> dict:
        """Generate or edit an image through this Files App's Model Services binding.

        Args:
            prompt: Detailed generation or editing instructions.
            reference_images: Workspace image paths in reference order.
            model: A configured image-model alias or Fleet model reference; omit for the configured default.
            model_args: Parameters supported by the selected image service.
        """
        if 'image_generation' in self._unconfigured_models:
            return unconfigured_model('image_generation')
        if self._image_generation is None:
            return {'success': False, 'error': 'This Files App has no image generation dependency'}
        result = await self._image_generation.generate(prompt, reference_images, model, model_args)
        if result.get('success'):
            preview = await self.fetch_image_base64(result['images'][0])
            if preview.get('success'):
                result.update(base64_uri=[preview['data_uri']], hidden_to_model=['base64_uri'])
        return result

    async def cleanup(self):
        try:
            await super().cleanup()
        finally:
            if self._image_generation is not None:
                await self._image_generation.close()

    @tool
    async def observe_images(self, question: str, image_paths: list[str]) -> dict:
        """Answer a question using images from this Files App's workspace.

        Args:
            question: The question to answer about the images.
            image_paths: Image paths in this Files App's configured workspace.
        """
        if 'sampling' in self._unconfigured_models:
            return unconfigured_model('sampling')
        if isinstance(image_paths, str):
            image_paths = [image_paths]
        if (not isinstance(question, str) or not question.strip() or len(question) > 65536
                or not isinstance(image_paths, list) or not 1 <= len(image_paths) <= 8):
            return {'success': False, 'error': 'Supply a question and one to eight image paths'}
        blocks = [{'type': 'text', 'text': question}]
        size = len(question.encode())
        for path in image_paths:
            preview = await self.fetch_image_base64(path)
            if not preview.get('success'):
                return preview
            uri = preview['data_uri']
            size += len(uri)
            if size > 15 * 1024 * 1024 or uri.startswith('data:image/svg+xml;'):
                return {'success': False, 'error': 'Image sampling requires bounded raster images'}
            blocks.append({'type': 'image_url', 'image_url': {'url': uri}})
        result = await self.get_context().call_agent(
            messages=[{'role': 'user', 'content': blocks}],
            system_prompt='Answer the question based on the supplied images.')
        if not result.get('success'):
            return result
        return {'success': True, 'content': result['response'], 'model_used': result.get('model'),
                'image_count': len(image_paths), 'usage': result.get('usage', {}),
                '_metadata': result.get('_metadata', {})}

    @tool(exclude=True)
    async def fetch_image_base64(self, image_path: str, max_size: int = 1568) -> dict:
        """Return a bounded image preview from this Files workspace.

        Args:
            image_path: Image path inside the configured workspace.
            max_size: Raster preview's longest edge, from 1 to 4096 pixels.
        """
        if not isinstance(image_path, str) or not image_path or type(max_size) is not int or not 1 <= max_size <= 4096:
            return {'success': False, 'error': 'Supply an image path and a preview size from 1 to 4096'}

        def encode():
            from pantheon.utils.vision import get_image_base64
            path = self._resolve_path(image_path).resolve()
            root = self.path.resolve()
            if not path.is_relative_to(root):
                raise ValueError('Image is outside the configured Files workspace')
            formats = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'}
            if not path.is_file() or path.suffix.lower() not in formats:
                raise ValueError('Image is missing or has an unsupported format')
            limit = 10 * 1024 * 1024
            if path.suffix.lower() in {'.gif', '.svg'}:
                with path.open('rb') as stream:
                    raw = stream.read(limit + 1)
                if not raw or len(raw) > limit:
                    raise ValueError('Image is empty or exceeds the preview byte limit')
                mime = 'gif' if path.suffix.lower() == '.gif' else 'svg+xml'
                return f'data:image/{mime};base64,' + base64.b64encode(raw).decode('ascii')
            return get_image_base64(str(path), max_size=max_size, max_bytes=limit, max_pixels=40_000_000)

        async with self._preview_slots:
            # Keep the admission slot until the worker finishes, even if its
            # request is cancelled, so disconnected viewers cannot pile up decodes.
            pending = asyncio.create_task(asyncio.to_thread(encode))
            try:
                uri = await asyncio.shield(pending)
                return {'success': True, 'image_path': image_path, 'data_uri': uri}
            except asyncio.CancelledError:
                while not pending.done():
                    try:
                        await asyncio.shield(pending)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                if not pending.cancelled():
                    pending.exception()
                raise
            except Exception:
                return {'success': False, 'error': 'Image preview unavailable; check its path, format and size in this Files workspace'}


def create_service(config, *, model_sampling=False, image_generation=None, unconfigured_models=()):
    if (not isinstance(config, Mapping) or set(config) - {'workspace', 'limits'}
            or not isinstance(config.get('workspace'), str) or not Path(config['workspace']).is_absolute()):
        raise ValueError('Files needs an explicit absolute workspace')
    workspace = Path(config['workspace'])
    if not workspace.is_dir():
        raise ValueError('Files workspace must exist on its node')
    limits = config.get('limits', {})
    ceilings = dict(max_file_read_chars=8*1024*1024, max_file_read_lines=100000, max_glob_results=100000)
    defaults = dict(max_file_read_chars=50000, max_file_read_lines=800, max_glob_results=1000)
    if (not isinstance(limits, Mapping) or limits.keys() - ceilings.keys()
            or any(type(value) is not int or not 1 <= value <= ceilings[key] for key, value in limits.items())):
        raise ValueError('Invalid Files response limits')
    unconfigured_models = frozenset(unconfigured_models)
    if (unconfigured_models - {'sampling', 'image_generation'}
            or ('sampling' in unconfigured_models and not model_sampling)
            or ('image_generation' in unconfigured_models and image_generation is not None)):
        raise ValueError('Conflicting Files model capability configuration')
    service = ManagedFiles('file_manager', workspace,
        file_settings=SimpleNamespace(**(defaults | dict(limits))), template_fallback=False)
    service._unconfigured_models = unconfigured_models
    methods = METHODS | {'observe_images'} if model_sampling else METHODS
    if image_generation is not None or 'image_generation' in unconfigured_models:
        methods = methods | {'generate_image'}
        service._image_generation = image_generation
    service.functions = {name: value for name, value in service.functions.items() if name in methods}
    if service.functions.keys() != methods:
        raise ValueError('Files package methods differ from the source toolset')
    return service


async def register(ctx):
    configuration = load_runtime_configuration(required=True)
    service = create_service(configuration.values.get('files'))
    await register_toolset(ctx, service)


async def register_sampling(ctx):
    """Prepared variant: defer explicitly, or validate a binding before admission."""
    await register_capabilities(ctx, observation=True)


async def register_capabilities(ctx, *, observation=False, generation=False):
    from pantheon.apps.model_sampling import ToolModelSampling
    configuration = load_runtime_configuration(required=True)
    sampling = images = None
    unconfigured = {name for name, enabled in (('sampling', observation), ('image_generation', generation))
                    if enabled and configuration.values.get(name) == {'state': 'unconfigured'}}
    try:
        if observation and 'sampling' not in unconfigured:
            sampling = ToolModelSampling(configuration.values.get('sampling'), configuration.credentials)
        if generation and 'image_generation' not in unconfigured:
            from .image_generation import ImageGeneration
            images = ImageGeneration(configuration.values.get('image_generation'), configuration.credentials,
                                     configuration.values.get('files', {}).get('workspace', ''), state_dir=ctx.state_dir)
        service = create_service(configuration.values.get('files'), model_sampling=observation,
                                 image_generation=images, unconfigured_models=unconfigured)
    except BaseException:
        if images is not None:
            await images.close()
        if sampling is not None:
            await sampling.close()
        raise
    await register_toolset(ctx, service, sampling=sampling)
