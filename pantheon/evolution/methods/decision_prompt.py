"""Pure planner-contract projection; no engine imports, I/O, model calls or tasks."""
import json

OPTIONAL_MARKER='\nOPTIONAL MEASUREMENT INSTRUMENT (not a fourth direction type):'

def build_decision_planner_prompt(native_prompt,eligible_anchor_ids,memory=''):
    """Integrate required-nullable evidence_request into native rendered JSON schema.

    Eligible IDs are caller supplied from active BASE/ESTABLISHED native branches.
    Requests are proposed measurement questions; scheduling and budgets remain separate.
    """
    prompt=native_prompt.split(OPTIONAL_MARKER,1)[0]
    old='"verbalized_probability": 0.0}'
    if old not in prompt:raise ValueError('Native planner output contract changed; cannot safely integrate evidence_request')
    prompt=prompt.replace(old,'"verbalized_probability": 0.0,\n      "evidence_request": null}',1)
    ids=list(dict.fromkeys(str(x) for x in eligible_anchor_ids))
    contract='''\n\n## Required measurement-decision output contract
Every direction MUST contain the explicit `evidence_request` field shown in the JSON
schema above. It is either null or one object with ALL fields below:
{"anchor_id":"exact eligible live ID", "mechanism":"one component/contrast",
 "decision_to_inform":"specific choice a measured OFF/ON contrast would inform",
 "predicted_contrast":"concrete expected difference on development measurements",
 "falsifier":"observed result that would count against the prediction",
 "expected_cost":{"candidate_evaluations":2,"template_model_calls":1}}

If eligible anchors exist, propose at least ONE non-null concrete measurement
question in this portfolio. Use null explicitly for directions with no useful local
measurement question. If no anchors are eligible, every evidence_request must be
null and explain that fact in the diagnosis. Do not invent IDs or create a new
architecture direction solely to satisfy this measurement contract. Retain the
native integration/exploration/redesign portfolio and its required redesign count.
The request may name a BASE/ESTABLISHED family distinct from its parent direction's
redesign target; state the exact component decision and anchor context. A negative
measurement in that context does not ban a different representation or family.
Questions are NOT evaluations: the controller may reject/defer them. Native planned
directions and native planning have priority; shared<=80 evaluations include<=8
probe arms. Do not claim the requested measurement was already made.
'''
    prompt+=contract+'\nEligible BASE/ESTABLISHED measurement anchor IDs (exact):\n'+json.dumps(ids)+'\n'
    if memory:prompt+='\nCONTEXT-SCOPED OBSERVED EVIDENCE (conditional, not universal):\n'+memory
    return prompt
