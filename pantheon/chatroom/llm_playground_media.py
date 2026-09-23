"""Multimodal Playground calls and bounded, chunked media transfer.

Media stays out of the RPC response and JSON editor. Credentials are used only
for the selected provider's API, never on provider-returned download URLs.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
import re
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx

OPERATIONS = {"text", "image", "video", "speech", "transcription", "embedding", "rerank"}
PLATFORM_OPERATIONS = {"text", "image", "embedding"}
LIMIT = 64 * 1024 * 1024
CHUNK = 192 * 1024
AUDIO_LIMIT = 16 * 1024 * 1024
PARAMETERS = {
    "text": set(),
    "image": {"n", "size", "quality", "resolution", "aspect_ratio", "output_format", "background", "seed"},
    "video": {"duration", "seconds", "resolution", "aspect_ratio", "size", "generate_audio", "seed"},
    "speech": {"voice", "response_format", "speed"},
    "transcription": {"audio_asset", "language"},
    "embedding": {"dimensions"},
    "rerank": {"documents", "top_n", "return_documents"},
}
CATALOG_PATHS = {
    "image": "images/models", "video": "videos/models",
    "speech": "models?output_modalities=speech",
    "transcription": "models?output_modalities=transcription", "embedding": "embeddings/models",
}
_catalog_cache: dict[str, tuple[float, list]] = {}
_catalog_lock = asyncio.Lock()


def route_reason(source: str, sdk: str, operation: str) -> str:
    if operation == 'rerank':
        return 'Select a published Fleet rerank model for this operation.'
    if source == "platform" and operation not in PLATFORM_OPERATIONS:
        return "The current platform proxy does not route this operation. Select OpenRouter BYOK in Call source."
    if operation != "text" and source not in {"platform", "openrouter", "openai"}:
        return "This provider's multimodal API is not connected to Playground yet."
    if source == "openai" and operation == "video":
        return "Select OpenRouter BYOK for video generation."
    return ""


async def media_catalog() -> tuple[list[dict], list[str]]:
    """Cache each endpoint independently; preserve old results on partial failure."""
    async with _catalog_lock:
        errors = []
        async with httpx.AsyncClient(timeout=8) as client:
            async def fetch(operation, path):
                cached = _catalog_cache.get(operation)
                if cached and time.monotonic() - cached[0] < 900:
                    return
                try:
                    response = await client.get("https://openrouter.ai/api/v1/" + path)
                    response.raise_for_status()
                    _catalog_cache[operation] = (time.monotonic(), response.json()["data"])
                except Exception:
                    errors.append(f"{operation} catalog unavailable; showing cached entries when available.")
            await asyncio.gather(*(fetch(op, path) for op, path in CATALOG_PATHS.items()))
        cards = []
        for operation, (_, rows) in _catalog_cache.items():
            for row in rows:
                arch = row.get("architecture") or {}
                for source in ("platform", "openrouter"):
                    cards.append(dict(
                        model="openrouter/" + row["id"], name=row.get("name", row["id"]),
                        source=source, vendor=row["id"].split("/")[0], operations=[operation],
                        input_modalities=arch.get("input_modalities", []),
                        output_modalities=arch.get("output_modalities", [operation]),
                        supported_parameters=row.get("supported_parameters") or {},
                        supported_voices=row.get("supported_voices") or [],
                        pricing=row.get("pricing") or row.get("pricing_skus") or {},
                        description=row.get("description", ""), created=row.get("created"),
                        context=row.get("context_length"), capabilities={},
                        operation_reasons={operation: route_reason(source, "openai", operation)},
                        metadata_source="OpenRouter media catalog",
                    ))
        return cards, errors


class MediaStore:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="pantheon-playground-")
        self.items: dict[str, dict] = {}

    def prune(self):
        total = sum(item["size"] for item in self.items.values())
        for key, item in list(self.items.items()):
            if time.monotonic() - item["created"] > 3600 or total > 192 * 1024 * 1024 or len(self.items) >= 24:
                total -= item["size"]
                self.remove(key)

    def remove(self, key):
        if key in self.items:
            self.items.pop(key)["path"].unlink(missing_ok=True)

    def create(self, mime: str, kind: str, name: str, upload=False):
        self.prune()
        key = uuid.uuid4().hex
        self.items[key] = dict(path=Path(self.directory.name) / key, mime_type=mime,
                               type=kind, name=name, size=0, created=time.monotonic(), upload=upload)
        self.items[key]["path"].touch()
        return key

    def get(self, key):
        item = self.items.get(key)
        if not item or time.monotonic() - item["created"] > 3600:
            raise ValueError("Media expired. Run the request again.")
        return item

    def metadata(self, key):
        return {"id": key, **{k: self.get(key)[k] for k in ("mime_type", "type", "name", "size")}}

    def add(self, data: bytes, mime: str, kind: str, name: str):
        if len(data) > LIMIT:
            raise ValueError("Media exceeds the 64 MiB preview limit.")
        key = self.create(mime, kind, name)
        self.items[key]["path"].write_bytes(data)
        self.items[key]["size"] = len(data)
        return self.metadata(key)

    def read(self, key: str, offset: int):
        item = self.get(key)
        if not isinstance(offset, int) or not 0 <= offset <= item["size"]:
            raise ValueError("Invalid media offset")
        with item["path"].open("rb") as file:
            file.seek(offset)
            data = file.read(CHUNK)
        return dict(data=base64.b64encode(data).decode(), size=item["size"], offset=offset,
                    done=offset + len(data) == item["size"], mime_type=item["mime_type"])

    def upload(self, key: str, data: str, offset: int, name: str):
        suffix = Path(name).suffix.lower().lstrip(".")
        if suffix not in {"mp3", "wav", "flac", "m4a", "ogg", "webm", "aac"}:
            raise ValueError("Use MP3, WAV, FLAC, M4A, OGG, WebM or AAC audio.")
        if not isinstance(data, str) or len(data) > CHUNK * 2:
            raise ValueError("Audio chunk too large")
        try:
            payload = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Invalid audio data") from None
        if not key:
            if offset != 0:
                raise ValueError("Invalid initial audio offset")
            key = self.create("audio/" + suffix, "audio", "input." + suffix, upload=True)
        item = self.get(key)
        if not item["upload"] or offset != item["size"] or offset + len(payload) > AUDIO_LIMIT:
            raise ValueError("Invalid upload offset or audio exceeds 16 MiB")
        with item["path"].open("ab") as file:
            file.write(payload)
        item["size"] += len(payload)
        return self.metadata(key)


def validate(operation, params):
    if operation not in OPERATIONS:
        raise ValueError("Unknown operation")
    if not isinstance(params, dict) or len(json.dumps(params)) > 16000:
        raise ValueError("Parameters must be a small JSON object")
    unknown = params.keys() - PARAMETERS[operation]
    if unknown:
        raise ValueError("Unsupported parameters: " + ", ".join(sorted(unknown)))
    if "n" in params and (type(params["n"]) is not int or not 1 <= params["n"] <= 4):
        raise ValueError("Generate between 1 and 4 images per run")
    for key in ("duration", "seconds"):
        if key in params and (type(params[key]) not in (int, float) or not 1 <= params[key] <= 30):
            raise ValueError("Video duration must be between 1 and 30 seconds")
    if "dimensions" in params and (type(params["dimensions"]) is not int or not 1 <= params["dimensions"] <= 16384):
        raise ValueError("Embedding dimensions must be between 1 and 16,384")
    if operation == "speech" and (not isinstance(params.get("voice"), str) or not params["voice"].strip()):
        raise ValueError("Choose a voice for speech generation")
    if operation == "speech" and params.get("response_format", "mp3") not in {"mp3", "wav", "opus", "flac", "aac"}:
        raise ValueError("Use a browser-playable audio format: mp3, wav, opus, flac or aac")
    if operation == "transcription" and not params.get("audio_asset"):
        raise ValueError("Upload an audio file before transcribing")
    if operation == 'rerank':
        documents = params.get('documents')
        if (not isinstance(documents, list) or not 1 <= len(documents) <= 128
                or any(not isinstance(item, str) or not item.strip() for item in documents)):
            raise ValueError('Provide 1–128 nonempty documents in Parameters JSON')
        if ('top_n' in params and (type(params['top_n']) is not int or not 1 <= params['top_n'] <= len(documents))
                or type(params.get('return_documents', False)) is not bool):
            raise ValueError('Invalid rerank result count or document option')


async def _json(client, method, url, headers, **kwargs):
    response = await client.request(method, url, headers=headers, **kwargs)
    if response.is_error:
        raise ValueError(f"Provider HTTP {response.status_code}: {response.text[:800]}")
    payload = response.json()
    reported = response.headers.get("x-litellm-response-cost") or response.headers.get("llm_provider-x-litellm-response-cost")
    if reported:
        try:
            cost = float(reported)
            if math.isfinite(cost) and cost >= 0:
                payload.setdefault("usage", {}).setdefault("cost", cost)
        except (ValueError, TypeError):
            pass
    return payload


async def download(client, url, store, kind, name, headers=None):
    if not url.startswith("https://") and not (headers and url.startswith("http://")):
        raise ValueError("Provider returned an unsupported media URL")
    key = store.create("application/octet-stream", kind, name)
    try:
        # httpx strips Authorization on cross-origin redirects. Never put credentials
        # on arbitrary URLs returned inside a provider response.
        async with client.stream("GET", url, headers=headers or {}, follow_redirects=True) as response:
            response.raise_for_status()
            store.items[key]["mime_type"] = response.headers.get("content-type", "application/octet-stream").split(";")[0]
            with store.items[key]["path"].open("wb") as file:
                async for chunk in response.aiter_bytes():
                    store.items[key]["size"] += len(chunk)
                    if store.items[key]["size"] > LIMIT:
                        raise ValueError("Media exceeds the 64 MiB preview limit")
                    file.write(chunk)
        return store.metadata(key)
    except BaseException:
        store.remove(key)
        raise


async def complete(route, model, prompt, operation, params, store, progress):
    reason = route_reason(route.source, route.sdk, operation)
    if reason:
        raise ValueError(reason)
    if route.source == "platform" and operation == "image" and params.keys() - {"n", "size", "quality"}:
        raise ValueError("The current platform image adapter supports n, size and quality. Use OpenRouter BYOK for other image parameters.")
    started = time.monotonic()
    base = (route.base or "https://api.openai.com/v1").rstrip("/")
    headers = {"Authorization": "Bearer " + route.key}
    requested = model if route.source == "platform" else model.split("/", 1)[1]
    params = dict(params)
    body = {"model": requested, **params}
    media, output, data = [], "", None
    async with httpx.AsyncClient(timeout=httpx.Timeout(150, connect=15)) as client:
        if operation == "image":
            body["prompt"] = prompt
            if route.source == "openai" and "gpt-image" not in requested:
                body.setdefault("response_format", "b64_json")
            path = "/images" if route.source == "openrouter" else "/images/generations"
            response = await _json(client, "POST", base + path, headers, json=body)
            for index, item in enumerate(response.get("data", [])):
                mime = item.get("media_type") or "image/" + params.get("output_format", "png")
                if item.get("b64_json"):
                    if len(item["b64_json"]) > LIMIT * 1.4:
                        raise ValueError("Image exceeds the 64 MiB preview limit")
                    extension = {"image/svg+xml": "svg", "image/jpeg": "jpg"}.get(mime, mime.split("/")[-1])
                    media.append(store.add(base64.b64decode(item["b64_json"], validate=True), mime, "image", f"image-{index + 1}.{extension}"))
                elif item.get("url"):
                    media.append(await download(client, item["url"], store, "image", f"image-{index + 1}"))
            if not media:
                raise ValueError("Provider returned no images")
        elif operation == "speech":
            body.update(input=prompt, response_format=params.get("response_format", "mp3"))
            response_http = await client.post(base + "/audio/speech", headers=headers, json=body)
            if response_http.is_error:
                raise ValueError(f"Provider HTTP {response_http.status_code}: {response_http.text[:800]}")
            media = [store.add(response_http.content, response_http.headers.get("content-type", "audio/mpeg"), "audio", "speech." + body["response_format"])]
            response = {"generation_id": response_http.headers.get("x-generation-id")}
        elif operation == "transcription":
            asset = store.get(body.pop("audio_asset"))
            if not asset["upload"] or not 0 < asset["size"] <= AUDIO_LIMIT:
                raise ValueError("Upload a non-empty audio file")
            if route.source == "openrouter":
                body["input_audio"] = dict(data=base64.b64encode(asset["path"].read_bytes()).decode(), format=asset["name"].split(".")[-1])
                response = await _json(client, "POST", base + "/audio/transcriptions", headers, json=body)
            else:
                with asset["path"].open("rb") as file:
                    response = await _json(client, "POST", base + "/audio/transcriptions", headers, data=body, files={"file": (asset["name"], file, asset["mime_type"])})
            output = response.get("text", "")
        elif operation == "embedding":
            body.update(input=prompt, encoding_format="float")
            response = await _json(client, "POST", base + "/embeddings", headers, json=body)
            data = response.get("data", [])
            output = "Embedding dimensions: " + str(len(data[0].get("embedding", [])) if data else 0)
        elif operation == "video":
            if "seconds" in body:
                body["duration"] = body.pop("seconds")
            body["prompt"] = prompt
            response = await _json(client, "POST", base + "/videos", headers, json=body)
            job_id = response.get("id")
            if not isinstance(job_id, str) or not re.fullmatch(r"[\w.-]{1,200}", job_id):
                raise ValueError("Provider returned an invalid video job id")
            job_url = base + "/videos/" + quote(job_id, safe="")
            while response.get("status") not in {"completed", "failed", "cancelled", "expired"}:
                progress.update(status=response.get("status", "pending"), job_id=job_id)
                await asyncio.sleep(3)
                response = await _json(client, "GET", job_url, headers)
            if response.get("status") != "completed":
                raise ValueError("Video job " + str(response.get("status")) + ": " + str(response.get("error", ""))[:500])
            progress.update(status="downloading", job_id=job_id)
            media = [await download(client, job_url + "/content?index=0", store, "video", "video.mp4", headers)]
        else:
            raise ValueError("Unsupported media operation")
    usage = response.get("usage") or {}
    return dict(success=True, model=model, returned_model=response.get("model"), route=route.public(),
                operation=operation, output=output, data=data, media=media, usage=usage,
                job_id=response.get("id") if operation == "video" else None,
                elapsed_ms=round((time.monotonic() - started) * 1000),
                reported_cost_usd=usage.get("cost"), cost_note="Provider-reported cost when available; see provider billing for final charges.")
