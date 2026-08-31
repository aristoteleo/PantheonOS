"""Function-signature descriptions — the tool wire format's single home.

Formerly the vendored third-party ``funcdesc`` package (its PyPI release
died in the June 2026 supply-chain attack); now owned in-tree. The JSON
shape ``Description.to_json`` emits is a WIRE CONTRACT: NATS tool
registration, the manifests' ``provides.tools``, and the Go side
(``fleet/appsvc``, which builds the same shape from embedded app.json)
all speak it. Change it only with all three in hand —
``tests/test_funcdesc_wire.py`` is the tripwire.
"""

from .desc import Description, Value, SideEffect
from .parse import parse_func
from .mark import (
    mark_input, mark_output, mark_side_effect,
    Val, Outputs,
)

__all__ = [
    "Description", "Value", "SideEffect",
    "parse_func",
    "mark_input", "mark_output", "mark_side_effect",
    "Val", "Outputs",
]
