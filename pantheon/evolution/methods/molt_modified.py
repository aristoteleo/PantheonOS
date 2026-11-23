"""Molt control: redesign writers receive the planner-selected working target.

This generalizes the separately tested cardiac target-writer patch to any code
filename. Native planner, scheduling, probation, rejection and ordinary prompts
are inherited without changes. Vanilla Molt remains a separate class.
"""
import hashlib
from . import molt as M


class ModifiedMoltVariator(M.MoltVariator):
    def _direction_prompt(self, ctx, item, path, eb, p):
        text = super()._direction_prompt(ctx, item, path, eb, p)
        if p.get('type') == 'redesign':
            old = M.REDESIGN_ASSIGNMENT.format(target=p['target'], target_score=p['target_score'])
            assert text.count(old) == 1
            assignment = (
                f"Start from the complete working TARGET program {p['target']} (score {p['target_score']}) shown below. "
                "Implement the planner's redesign by changing its specified algorithmic mechanism. "
                'Reuse useful existing preprocessing, helpers and infrastructure; update callers and coupled dependencies wherever needed. '
                "The target is your starting implementation, not an I/O-only reference. Keep the redesign's distinct algorithmic idea; "
                'do not simply copy the target unchanged or substitute its entire old method as a fallback.'
            )
            text = text.replace(old, assignment)
            assert item.context.parents[0].id == p['target']
            assert item.context.parents[0].genome.files[path] in text
        return text


class ModifiedMolt(M.Molt):
    name = 'molt_modified'
    variator_cls = ModifiedMoltVariator

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.target_context_audit = []

    def _direction_item(self, ctx, direction):
        item = super()._direction_item(ctx, direction)
        if item is not None and direction.get('type') == 'redesign':
            target = ctx.store.get(direction['target_branch_id'])
            item.context.parents = [target]
            item.meta['molt_prompt']['roles'] = {
                target.id: 'TARGET (starting working program; implement the redesign on this code)'
            }
            path = self.evolve_file or next(iter(target.genome.files))
            self.target_context_audit.append({
                'direction': direction['id'], 'target': target.id,
                'shown_programs': [target.id], 'path': path,
                'source_sha256': hashlib.sha256(target.genome.files[path].encode()).hexdigest(),
                'parent_ids': item.parent_ids, 'same_as_planner_target': True,
            })
        return item

    def state_dict(self):
        result = super().state_dict()
        result['modified_molt_writer'] = {'target_context_audit': self.target_context_audit}
        return result

    def load_state_dict(self, state):
        super().load_state_dict(state)
        self.target_context_audit = list(state.get('modified_molt_writer', {}).get('target_context_audit', []))
