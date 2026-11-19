"""Exploratory warm-start portfolio with an explicit whole-program neighborhood.

Separately named ablation, not a claimed improvement. Three model-proposed
neighborhoods coexist with one unrestricted algorithmic rewrite. Markers are
immutable and the writer still sees the entire current best program.
"""
import copy
from .molt2_lns import Molt2LNS, validate_regions
from .molt2_lns_vns2 import Molt2LNSVNS2


def whole_program_regions(files):
    regions = []
    for path, code in sorted(files.items()):
        start = None
        lines = code.splitlines()
        for index, line in enumerate(lines, 1):
            if 'EVOLVE-BLOCK-' in line:
                if start is not None:
                    regions.append({'path':path,'start_line':start,'end_line':index-1})
                    start = None
            elif start is None:
                start = index
        if start is not None:
            regions.append({'path':path,'start_line':start,'end_line':len(lines)})
    # Explicitly refuse unsupported fragmented programs; never silently drop files.
    return validate_regions(files, regions, 3)


class Molt2LNSGlobal(Molt2LNSVNS2):
    name = 'molt2_lns_global'

    async def _make_portfolio(self, ctx):
        anchor = self._anchor(ctx)
        if anchor is None:
            return
        masks = whole_program_regions(anchor.genome.files)
        self.scope = 3
        await Molt2LNS._make_portfolio(self, ctx)
        if not self.portfolios or not self.portfolios[-1]['families']:
            return
        record = self.portfolios[-1]
        family = {
            'name':'coordinated_algorithm_rewrite',
            'decision':'The joint choice of algorithm, representation, move set and acceptance/search policy.',
            'hypothesis':'The local edit interfaces can block beneficial changes that require several components to change together.',
            'replacement':'Use the complete inherited best program as a working reference. Infer a qualitatively different coordinated algorithm from the task and implement it across all approved regions. Preserve useful working parts where appropriate; do not merely tune constants.',
            'preserve':'Task input/output contract, evaluation rules, source files and immutable markers.',
            'regions':masks,
        }
        index = len(record['families'])-1
        arm = f"round{record['round']}/family{index}"
        operator = self.operators[arm]
        operator.update(strategy=copy.deepcopy(family),initial_regions=copy.deepcopy(masks),scope=3)
        record['families'][index] = copy.deepcopy(family)
        record['explicit_global_arm'] = arm
