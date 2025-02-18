import io
import base64
from pathlib import Path

from PIL import Image

from ..types import VisionInput


def vision_input(prompt: str, image_paths: list[str] | str, from_path: bool = False) -> VisionInput:
    if isinstance(image_paths, str):
        image_paths = [image_paths]
    if from_path:
        return path_to_vision(prompt, image_paths)
    else:
        return VisionInput(
            images=image_paths,
            prompt=prompt,
        )


def path_to_image_url(path: str) -> str:
    img = Image.open(path)
    with io.BytesIO() as buffer:
        img.save(buffer, format="PNG")
        buffer.seek(0)
        return f"data:image/png;base64,{base64.b64encode(buffer.getvalue()).decode()}"


def path_to_vision(prompt: str, image_paths: list[str] | str | Path | list[Path]) -> VisionInput:
    if isinstance(image_paths, (str, Path)):
        image_paths = [image_paths]
    return VisionInput(
        images=[path_to_image_url(path) for path in image_paths],
        prompt=prompt,
    )


def vision_to_openai(vision: VisionInput) -> list[dict]:
    messages = [{"role": "user", "content": [{"type": "text", "text": vision.prompt}]}]
    for img in vision.images:
        messages[0]["content"].append({
            "type": "image_url",
            "image_url": {"url": img},
        })
    return messages

