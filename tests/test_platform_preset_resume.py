from pantheon.platform import preset_resume

D = {n: c * 64 for n, c in (('agent', 'a'), ('alloc', 'b'), ('evo', 'c'), ('conn', 'd'), ('desk', 'e'))}


def app(name, scope, generation, refs=()):
    return {'node_id': 'n_ws', 'revision': D[name], 'scope': scope, 'generation': generation,
            'components': {'backend': {'values': {'x': [{'$app': r} for r in refs]}}}, 'bindings': {}}


def recipe():
    agent = app('agent', 'agent', 10, ['allocator'])
    agent['components']['backend']['values']['models'] = {'$model': 'connector'}
    return {'owner': 'f_1', 'operation_id': 'agent-setup-1', 'kind': 'model-services',
            'apps': {'agent': agent, 'allocator': app('alloc', 'allocator', 3),
                     'evolution': app('evo', 'evolution-controller', 14, ['agent']),
                     'desktop': app('desk', 'shared-desktop', 11)},
            'model_apps': {'connector': {'deployment_id': 'platform', 'name': 'P', 'models': [],
                                         'app': app('conn', 'model-platform', 20)}}}


def node(states, ops=()):
    """states: {name: (scope, generation, state)}; ops: [(name, scope, action, updated)]."""
    instances = {f'i-{n}': {'digest': D[n], 'scope': s, 'generation': g, 'state': st}
                 for n, (s, g, st) in states.items()}
    operations = {f'o{k}': {'request': {'digest': D[n], 'scope': s, 'action': a}, 'state': 'succeeded', 'updated_at': t}
                  for k, (n, s, a, t) in enumerate(ops)}
    return {'n_ws': {'instances': instances, 'operations': operations}}


ALL_LOST = {'agent': ('agent', 13, 'stopped'), 'alloc': ('allocator', 5, 'stopped'),
            'evo': ('evolution-controller', 16, 'stopped'), 'desk': ('shared-desktop', 13, 'stopped'),
            'conn': ('model-platform', 22, 'stopped')}
STARTS = [(n, s, 'start', '2026-10-07T10:17') for n, (s, _, _) in ALL_LOST.items()]


def test_node_loss_resumes_every_app_at_its_current_generation():
    spec = preset_resume.resume_recipe(recipe(), node(ALL_LOST, STARTS))
    assert set(spec['apps']) == {'agent', 'allocator', 'evolution', 'desktop'} and set(spec['model_apps']) == {'connector'}
    assert spec['apps']['agent']['generation'] == 13 and spec['model_apps']['connector']['app']['generation'] == 22
    assert spec['operation_id'].startswith('agent-setup-1-resume-')
    # The same lost state continues the same operation after a platform restart.
    assert preset_resume.resume_recipe(recipe(), node(ALL_LOST, STARTS))['operation_id'] == spec['operation_id']


def test_owner_or_idle_stop_is_kept_with_its_dependents():
    ops = STARTS + [('agent', 'agent', 'stop', '2026-10-07T10:20')]
    spec = preset_resume.resume_recipe(recipe(), node(ALL_LOST, ops))
    assert set(spec['apps']) == {'allocator', 'desktop'}  # evolution needs the stopped Agent


def test_nothing_resumes_while_any_app_is_live_or_missing():
    live = dict(ALL_LOST, desk=('shared-desktop', 13, 'ready'))
    assert preset_resume.resume_recipe(recipe(), node(live, STARTS)) is None
    missing = {k: v for k, v in ALL_LOST.items() if k != 'desk'}
    assert preset_resume.resume_recipe(recipe(), node(missing, STARTS)) is None
    failed = dict(ALL_LOST, alloc=('allocator', 5, 'failed'))
    assert preset_resume.resume_recipe(recipe(), node(failed, STARTS)) is None


def test_no_lost_app_means_no_resume():
    ops = [(n, s, 'stop', '2026-10-07T11:00') for n, (s, _, _) in ALL_LOST.items()]
    assert preset_resume.resume_recipe(recipe(), node(ALL_LOST, ops)) is None


def test_consumers_lose_a_stopped_model_provider():
    ops = STARTS + [('conn', 'model-platform', 'stop', '2026-10-07T10:20')]
    spec = preset_resume.resume_recipe(recipe(), node(ALL_LOST, ops))
    assert set(spec['apps']) == {'allocator', 'desktop'} and 'model_apps' not in spec and 'kind' not in spec


def test_an_owner_start_request_includes_stopped_apps_but_not_live_ones():
    ops = STARTS + [('agent', 'agent', 'stop', '2026-10-07T10:20')]
    spec = preset_resume.resume_recipe(recipe(), node(ALL_LOST, ops), everything=True)
    assert set(spec['apps']) == {'agent', 'allocator', 'evolution', 'desktop'}
    live = dict(ALL_LOST, desk=('shared-desktop', 13, 'ready'))
    assert preset_resume.resume_recipe(recipe(), node(live, ops), everything=True) is None


def test_a_partly_lost_preset_restarts_together():
    # The workspace lost its Apps while the brain kept the Agent running.
    partial = dict(ALL_LOST, agent=('agent', 13, 'ready'), alloc=('allocator', 5, 'ready'))
    assert preset_resume.resume_recipe(recipe(), node(partial, STARTS)) is None
    assert sorted(preset_resume.restart_stops(recipe(), node(partial, STARTS))) == ['agent', 'allocator']
    # Once the platform's own stops land, everything is lost and resumes as one.
    ops = STARTS + [('agent', 'agent', 'stop', '2026-10-07T11:00'), ('alloc', 'allocator', 'stop', '2026-10-07T11:00')]
    states = node(ALL_LOST, ops)
    for o in states['n_ws']['operations'].values():
        if o['request']['action'] == 'stop':
            o['request']['operation_id'] = 'preset-restart-1'
    spec = preset_resume.resume_recipe(recipe(), states)
    assert set(spec['apps']) == {'agent', 'allocator', 'evolution', 'desktop'}


def test_no_restart_while_anything_failed_or_busy():
    failed = dict(ALL_LOST, alloc=('allocator', 5, 'failed'), agent=('agent', 13, 'ready'))
    assert preset_resume.restart_stops(recipe(), node(failed, STARTS)) == []
    assert preset_resume.restart_stops(recipe(), node({k: (s, g, 'ready') for k, (s, g, _) in ALL_LOST.items()}, STARTS)) == []
