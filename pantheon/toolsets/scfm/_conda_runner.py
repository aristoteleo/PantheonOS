"""
SCFM conda-subprocess runner.

This script is intended to be executed *inside* a model-specific conda env
via:

    conda run -n scfm-<model> python /path/to/_conda_runner.py --payload payload.json

It intentionally avoids importing the top-level `pantheon` package (which may
pull in unrelated heavy deps). Instead, it bootstraps minimal package stubs so
we can import SCFM adapter modules from the repo source tree.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.machinery
import json
import os
import sys
import traceback
import types
from pathlib import Path
from typing import Any


def _stub_package(name: str, package_dir: Path) -> None:
    module = types.ModuleType(name)
    module.__path__ = [str(package_dir)]
    module.__package__ = name
    module.__file__ = str(package_dir / "__init__.py")
    module.__spec__ = importlib.machinery.ModuleSpec(
        name=name, loader=None, is_package=True
    )
    sys.modules[name] = module


def _bootstrap_repo_imports(repo_root: Path) -> None:
    pantheon_dir = repo_root / "pantheon"
    toolsets_dir = pantheon_dir / "toolsets"
    scfm_dir = toolsets_dir / "scfm"
    adapters_dir = scfm_dir / "adapters"

    _stub_package("pantheon", pantheon_dir)
    _stub_package("pantheon.toolsets", toolsets_dir)
    _stub_package("pantheon.toolsets.scfm", scfm_dir)
    _stub_package("pantheon.toolsets.scfm.adapters", adapters_dir)


def _load_adapter(model_name: str, checkpoint_dir: str | None):
    model_name = model_name.lower()
    mapping: dict[str, tuple[str, str]] = {
        "uce": ("pantheon.toolsets.scfm.adapters.uce", "UCEAdapter"),
        "scgpt": ("pantheon.toolsets.scfm.adapters.scgpt", "ScGPTAdapter"),
        "geneformer": ("pantheon.toolsets.scfm.adapters.geneformer", "GeneformerAdapter"),
        "scfoundation": ("pantheon.toolsets.scfm.adapters.scfoundation", "ScFoundationAdapter"),
        "scbert": ("pantheon.toolsets.scfm.adapters.scbert", "ScBERTAdapter"),
        "genecompass": ("pantheon.toolsets.scfm.adapters.genecompass", "GeneCompassAdapter"),
        "cellplm": ("pantheon.toolsets.scfm.adapters.cellplm", "CellPLMAdapter"),
        "nicheformer": ("pantheon.toolsets.scfm.adapters.nicheformer", "NicheformerAdapter"),
        "scmulan": ("pantheon.toolsets.scfm.adapters.scmulan", "ScMulanAdapter"),
        "tgpt": ("pantheon.toolsets.scfm.adapters.tgpt", "TGPTAdapter"),
        "cellfm": ("pantheon.toolsets.scfm.adapters.cellfm", "CellFMAdapter"),
        "sccello": ("pantheon.toolsets.scfm.adapters.sccello", "ScCelloAdapter"),
        "scprint": ("pantheon.toolsets.scfm.adapters.scprint", "ScPRINTAdapter"),
        "aidocell": ("pantheon.toolsets.scfm.adapters.aidocell", "AIDOCellAdapter"),
        "pulsar": ("pantheon.toolsets.scfm.adapters.pulsar", "PULSARAdapter"),
        "atacformer": ("pantheon.toolsets.scfm.adapters.atacformer", "AtacformerAdapter"),
        "scplantllm": ("pantheon.toolsets.scfm.adapters.scplantllm", "ScPlantLLMAdapter"),
        "langcell": ("pantheon.toolsets.scfm.adapters.langcell", "LangCellAdapter"),
        "cell2sentence": ("pantheon.toolsets.scfm.adapters.cell2sentence", "Cell2SentenceAdapter"),
        "genept": ("pantheon.toolsets.scfm.adapters.genept", "GenePTAdapter"),
        "chatcell": ("pantheon.toolsets.scfm.adapters.chatcell", "CHATCELLAdapter"),
    }

    if model_name not in mapping:
        raise ValueError(f"Unknown model adapter: {model_name}")

    module_name, class_name = mapping[model_name]
    module = importlib.import_module(module_name)
    adapter_cls = getattr(module, class_name)
    return adapter_cls(checkpoint_dir)


def _coerce_none(value: Any) -> Any:
    # JSON may include empty strings; normalize them
    if value == "" or value == "null":
        return None
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True, help="Path to JSON payload file")
    args = parser.parse_args()

    payload_path = Path(args.payload).expanduser()
    payload = json.loads(payload_path.read_text())

    model_name = payload["model_name"]
    task = payload["task"]
    adata_path = payload["adata_path"]
    output_path = payload["output_path"]

    checkpoint_dir = _coerce_none(payload.get("checkpoint_dir"))
    batch_key = _coerce_none(payload.get("batch_key"))
    label_key = _coerce_none(payload.get("label_key"))
    device = _coerce_none(payload.get("device")) or "auto"
    batch_size = payload.get("batch_size")

    # Mark provenance backend for BaseAdapter._add_provenance()
    os.environ.setdefault("SCFM_BACKEND", "conda-subprocess")

    # Repo root: <repo>/pantheon/toolsets/scfm/_conda_runner.py -> parents[3]
    repo_root = Path(__file__).resolve().parents[3]
    _bootstrap_repo_imports(repo_root)

    try:
        from pantheon.toolsets.scfm.registry import TaskType

        adapter = _load_adapter(model_name=model_name, checkpoint_dir=checkpoint_dir)
        result = adapter.run(
            task=TaskType(task),
            adata_path=adata_path,
            output_path=output_path,
            batch_key=batch_key,
            label_key=label_key,
            device=device,
            batch_size=batch_size or adapter.spec.hardware.default_batch_size,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": str(e), "model": model_name, "task": task}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
