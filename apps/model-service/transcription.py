"""Pinned Speaches/OpenAI-compatible multipart audio transcription adapter.

Only a leased, sealed artifact on this connector can become a file part. No
caller paths, remote URLs, base64 audio, or arbitrary multipart headers.
"""
import hashlib
import uuid

MAX_AUDIO = 64 * 1024 * 1024
MIMES = {'audio/wav': 'wav', 'audio/mpeg': 'mp3', 'audio/flac': 'flac',
         'audio/ogg': 'ogg', 'audio/webm': 'webm', 'audio/mp4': 'm4a'}


def validate_input(plan, store):
    # Called under the job admission transaction, before granting a lease.
    row = store.row(plan['inputs'][0])
    if (row['state'] != 'ready' or row['kind'] != 'audio' or row['mime'] not in MIMES
            or not 0 < row['size'] <= MAX_AUDIO):
        raise ValueError('Transcription input must be sealed audio of at most 64 MiB')
    plan['audio'] = store.metadata(row)


def request(connector, plan, call, store):
    audio = plan['audio']
    boundary = 'fleet-' + uuid.uuid4().hex
    prefix = b''
    for name, value in plan['payload'].items():
        prefix += (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                   f'{value}\r\n').encode()
    prefix += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
               f'filename="audio.{MIMES[audio["mime"]]}"\r\nContent-Type: {audio["mime"]}\r\n\r\n').encode()
    suffix = f'\r\n--{boundary}--\r\n'.encode()

    def chunks():
        yield prefix
        offset, digest = 0, hashlib.sha256()
        while offset < audio['size']:
            if call['cancelled']:
                raise ConnectionAbortedError('Audio submission cancelled')
            receipt, data = store.read(audio['id'], offset, min(64 * 1024, audio['size'] - offset))
            if not data or receipt != audio:
                raise ValueError('Audio artifact changed during submission')
            offset += len(data)
            digest.update(data)
            yield data
        if digest.hexdigest() != audio['sha256']:
            raise ValueError('Audio checksum changed during submission')
        yield suffix

    return connector.inference_request(plan['path'], None, call, body=chunks(),
        content_type='multipart/form-data; boundary=' + boundary,
        content_length=len(prefix) + audio['size'] + len(suffix))

