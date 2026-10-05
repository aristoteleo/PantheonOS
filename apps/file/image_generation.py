"""Files-owned image jobs through its scoped original Model Services client."""
import asyncio
from collections.abc import Mapping
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import uuid

from PIL import Image
from pantheon.apps.model_sampling import ModelClientOwner
from pantheon.models.client import parse_ref, parse_route_ref

MIMES = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}
EXTENSIONS = {'image/png': 'png', 'image/jpeg': 'jpeg', 'image/webp': 'webp'}


class ImageGeneration(ModelClientOwner):
    def __init__(self, spec, credentials, workspace, *, state_dir, client_factory=None):
        try:
            if (not isinstance(spec, Mapping) or set(spec) != {'credential', 'model', 'aliases', 'timeout_seconds'}
                    or not isinstance(spec['credential'], str) or spec['credential'] not in credentials
                    or not isinstance(spec['aliases'], Mapping) or len(spec['aliases']) > 32
                    or any(not isinstance(k, str) or not 0 < len(k) <= 200 for k in spec['aliases'])
                    or type(spec['timeout_seconds']) is not int or not 1 <= spec['timeout_seconds'] <= 600):
                raise ValueError
            for ref in [spec['model'], *spec['aliases'].values()]:
                if not isinstance(ref, str):
                    raise ValueError
                (parse_route_ref if ref.startswith('fleet-route://') else parse_ref)(ref)
            self.workspace = Path(workspace).resolve(strict=True)
            if not self.workspace.is_dir():
                raise ValueError
        except (ValueError, TypeError, KeyError, OSError):
            raise ValueError('Image generation needs explicit model bindings and an existing workspace') from None
        self.model, self.aliases, self.timeout = spec['model'], dict(spec['aliases']), spec['timeout_seconds']
        self.records = Path(state_dir) / 'image-jobs'
        self.records.mkdir(parents=True, exist_ok=True, mode=0o700)
        super().__init__(credentials[spec['credential']], client_factory=client_factory)

    def _record(self, job, value):
        """Retain recovery identities before sending mutations; never save keys/prompts."""
        path = self.records / (job + '.json')
        with tempfile.NamedTemporaryFile(dir=self.records, prefix='.receipt-', mode='w', delete=False) as stream:
            temp = Path(stream.name)
            try:
                json.dump(value, stream, allow_nan=False)
                stream.flush(); os.fsync(stream.fileno())
                os.replace(temp, path)
            finally:
                temp.unlink(missing_ok=True)

    def _references(self, paths, stack):
        """Freeze workspace images in temporary streams before resolving a model."""
        if paths is None:
            return []
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list) or not 1 <= len(paths) <= 16:
            raise ValueError('Use one to sixteen reference image paths')
        frozen, total = [], 0
        for value in paths:
            if not isinstance(value, str) or not value:
                raise ValueError('Use workspace image paths')
            path = (self.workspace / value).resolve(strict=True)
            if not path.is_relative_to(self.workspace) or not path.is_file():
                raise ValueError('Reference image is outside this Files workspace')
            target = stack.enter_context(tempfile.TemporaryFile())
            size = 0
            with path.open('rb') as source:
                while block := source.read(1024 * 1024):
                    size += len(block); total += len(block)
                    if size >= 50 * 1024 * 1024 or total > 128 * 1024 * 1024:
                        raise ValueError('Reference images exceed the transfer budget')
                    target.write(block)
            target.seek(0)
            with Image.open(target) as image:
                mime = MIMES.get(image.format)
                if not mime or image.width * image.height > 40_000_000:
                    raise ValueError('Use bounded PNG, JPEG or WebP reference images')
                image.verify()
            target.seek(0)
            frozen.append((target, mime))
        return frozen

    def _output_directory(self):
        directory = self.workspace / 'generated-images'
        directory.mkdir(exist_ok=True)
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.workspace):
            raise ValueError('Image output directory must remain inside the workspace')
        # Check local publication before submitting a potentially paid operation.
        # Check again at publication in case the workspace changed while waiting.
        with tempfile.TemporaryFile(dir=directory):
            pass
        return directory

    async def generate(self, prompt, reference_images=None, model=None, model_args=None):
        if self._closed or len(self._pending) >= 2:
            return {'success': False, 'error': 'Image generation is stopping or busy'}
        if len(list(self.records.glob('*.json'))) >= 128:
            return {'success': False, 'error': 'Resolve retained image jobs in Model Services before generating more images'}
        try:
            selected = self.model if model is None else self.aliases.get(model, model)
            if selected not in {self.model, *self.aliases.values()}:
                raise ValueError('Model is not included in this Files App image bindings')
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 32768:
                raise ValueError('Supply a nonempty image prompt of at most 32768 characters')
            params = {} if model_args is None else model_args
            if not isinstance(params, dict) or len(json.dumps(params, allow_nan=False).encode()) > 8192:
                raise ValueError('Supply bounded image parameters')
            params = json.loads(json.dumps(params, allow_nan=False))
        except (ValueError, TypeError):
            return {'success': False, 'error': 'Invalid image prompt, parameters or configured model selection'}
        task = asyncio.create_task(self._generate(selected, prompt, reference_images, params))
        self._pending.add(task)
        try:
            return await task
        finally:
            self._pending.discard(task)

    async def _generate(self, selected, prompt, references, params):
        job = 'files-' + uuid.uuid4().hex
        job_ref, submitted = '', False
        uploaded, cleanup_pending = [], False
        output, result = None, None
        journal = None
        try:
            self._output_directory()
            with ExitStack() as stack:
                # The copies are bounded and kept off the model/control channel.
                # This worker must finish before its streams can be closed.
                freeze = asyncio.create_task(asyncio.to_thread(self._references, references, stack))
                try:
                    frozen = await asyncio.shield(freeze)
                except asyncio.CancelledError:
                    while not freeze.done():
                        try: await asyncio.shield(freeze)
                        except asyncio.CancelledError: pass
                        except Exception: break
                    if not freeze.cancelled(): freeze.exception()
                    raise
                async with self.client.inference(selected, 'image') as session:
                    job_ref = f'fleet-job://{session.deployment}/{job}'
                    journal = {'protocol': 1, 'job_ref': job_ref, 'model': selected,
                               'upload_keys': [], 'input_refs': uploaded, 'state': 'preparing'}
                    self._record(job, journal)
                    try:
                        async with asyncio.timeout(self.timeout):
                            for index, (source, mime) in enumerate(frozen):
                                journal['upload_keys'].append(f'{job}-{index}')
                                self._record(job, journal)
                                item = await session.wire.upload(source, request_key=f'{job}-{index}', kind='image', mime=mime)
                                uploaded.append(item['ref'])
                                self._record(job, journal)
                            inputs = {'text': prompt}
                            if uploaded: inputs['images'] = uploaded
                            # Once sent, a lost acknowledgement is ambiguous. Never
                            # replay this paid operation under a fresh identity.
                            submitted = True
                            journal['state'] = 'submitted'
                            self._record(job, journal)
                            receipt = await session.submit(inputs, request_id=job, parameters=params)
                            while receipt['state'] in {'queued', 'running', 'cancelling'}:
                                await asyncio.sleep(.25)
                                receipt = await session.status(job, wait_seconds=5)
                            if receipt['state'] != 'succeeded':
                                return {'success': False, 'error': 'Image job did not succeed; inspect its retained receipt',
                                        'job_ref': job_ref, 'state': receipt['state']}
                            artifact = receipt['result']['artifacts'][0]
                            if artifact['mime'] not in EXTENSIONS or artifact['size'] > 32 * 1024 * 1024:
                                raise ValueError('Unsupported image output')
                            directory = self._output_directory()
                            output = directory / (job + '.' + EXTENSIONS[artifact['mime']])
                            with tempfile.NamedTemporaryFile(dir=directory, prefix='.image-', delete=False) as target:
                                staged = Path(target.name)
                                try:
                                    await session.wire.download(artifact['ref'], target)
                                    target.flush(); os.fsync(target.fileno())
                                    with Image.open(staged) as image:
                                        if MIMES.get(image.format) != artifact['mime'] or image.width * image.height > 40_000_000:
                                            raise ValueError('Invalid generated image')
                                        image.verify()
                                    # The unique destination is never user-supplied.
                                    os.replace(staged, output)
                                finally:
                                    staged.unlink(missing_ok=True)
                            journal.update(state='copied', images=[str(output)])
                            self._record(job, journal)
                            result = {'success': True, 'images': [str(output)], 'model_used': selected,
                                      'usage': receipt['result'].get('usage', {}), '_metadata': dict(session.route)}
                            try:
                                await session.remove(job)
                                submitted = False
                            except Exception:
                                cleanup_pending = True
                    finally:
                        # Cancellation must request cancellation on the pinned
                        # deployment, even if submit acknowledgement was lost.
                        async def finish():
                            nonlocal cleanup_pending
                            if submitted and result is None:
                                try:
                                    async with asyncio.timeout(3):
                                        cancelled_job = await session.cancel(job)
                                        while cancelled_job['state'] in {'queued', 'running', 'cancelling'}:
                                            await asyncio.sleep(.1)
                                            cancelled_job = await session.status(job)
                                except Exception: cleanup_pending = True
                            for ref in uploaded:
                                try: await session.wire.remove(ref)
                                except Exception: cleanup_pending = True
                        cleanup = asyncio.create_task(finish())
                        cancelled = False
                        while not cleanup.done():
                            try: await asyncio.shield(cleanup)
                            except asyncio.CancelledError: cancelled = True
                        cleanup.result()
                        journal.update(state='copied' if result is not None else 'needs_attention',
                                       cleanup_pending=cleanup_pending)
                        self._record(job, journal)
                        if cancelled: raise asyncio.CancelledError
            if cleanup_pending:
                result.update(job_ref=job_ref, cleanup_pending=True)
            else:
                (self.records / (job + '.json')).unlink(missing_ok=True)
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            return {'success': False, 'error': 'Image generation through Model Services failed; it was not retried',
                    **({'job_ref': job_ref} if job_ref else {})}
