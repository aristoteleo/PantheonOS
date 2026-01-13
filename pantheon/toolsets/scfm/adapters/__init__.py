"""
Model Adapters for Single-Cell Foundation Models

Each adapter handles:
- Loading checkpoints and tokenizers
- Preprocessing data according to model requirements
- Running inference
- Writing results to AnnData with standardized keys
"""

from .base import BaseAdapter

# Skill-Ready Models
from .uce import UCEAdapter
from .scgpt import ScGPTAdapter
from .geneformer import GeneformerAdapter

# Partial-Spec Models (Core)
from .scfoundation import ScFoundationAdapter
from .scbert import ScBERTAdapter
from .genecompass import GeneCompassAdapter
from .cellplm import CellPLMAdapter
from .nicheformer import NicheformerAdapter
from .scmulan import ScMulanAdapter

# Specialized & Emerging Models (2024-2025)
from .tgpt import TGPTAdapter
from .cellfm import CellFMAdapter
from .sccello import ScCelloAdapter
from .scprint import ScPRINTAdapter
from .aidocell import AIDOCellAdapter
from .pulsar import PULSARAdapter
from .atacformer import AtacformerAdapter
from .scplantllm import ScPlantLLMAdapter
from .langcell import LangCellAdapter
from .cell2sentence import Cell2SentenceAdapter
from .genept import GenePTAdapter
from .chatcell import CHATCELLAdapter

__all__ = [
    "BaseAdapter",
    # Skill-Ready
    "UCEAdapter",
    "ScGPTAdapter",
    "GeneformerAdapter",
    # Partial-Spec (Core)
    "ScFoundationAdapter",
    "ScBERTAdapter",
    "GeneCompassAdapter",
    "CellPLMAdapter",
    "NicheformerAdapter",
    "ScMulanAdapter",
    # Specialized & Emerging (2024-2025)
    "TGPTAdapter",
    "CellFMAdapter",
    "ScCelloAdapter",
    "ScPRINTAdapter",
    "AIDOCellAdapter",
    "PULSARAdapter",
    "AtacformerAdapter",
    "ScPlantLLMAdapter",
    "LangCellAdapter",
    "Cell2SentenceAdapter",
    "GenePTAdapter",
    "CHATCELLAdapter",
]
