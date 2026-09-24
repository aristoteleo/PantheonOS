from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sys
import threading

import pytest

from pantheon.models import group_network
from test_model_engines import load


@pytest.fixture
def group(monkeypatch):
    monkeypatch.setitem(sys.modules, 'snapshots', load('snapshots'))
    monkeypatch.setitem(sys.modules, 'sglang_runtime', load('sglang_runtime'))
    monkeypatch.setitem(sys.modules, 'group_network', group_network)
    return load('sglang_group')


def plan():
    return dict(protocol=1, owner='f_' + 'a' * 16, group_id='sglang-group',
        recipe_id='sglang-0.5.20-linux-amd64', model_sha256='b' * 64,
        tensor_parallel_size=2, context_length=4096, parallel=2, rendezvous_port=18408,
        members=[dict(rank=rank, node_id=f'n_{rank}', generation=1, address=f'fd12::{rank+1}',
            control_port=18407, interface='eth0', physical_gpu_bytes=[24 << 30],
            resources=dict(memory_bytes=4 << 30, devices=[dict(id=f'GPU-{rank}', backend='cuda',
                memory_bytes=12 << 30, exclusive=True)])) for rank in range(2)])


def record():
    return dict(sha256='b' * 64, weights_bytes=2 << 30,
        tensor_parallel_weights={str(tp): (2 << 30) // tp for tp in (2, 4, 8)},
        config=dict(model_type='qwen2', num_hidden_layers=24, num_attention_heads=16,
                    num_key_value_heads=2, hidden_size=1024))


def test_two_nodes_use_global_tp_but_local_loading_budget_and_exact_private_interface(group):
    value = plan()
    ranks = [group.rank_launch(value, record(), rank, [24 << 30]) for rank in range(2)]
    for rank, compiled in enumerate(ranks):
        argv = compiled['argv']
        assert argv[argv.index('--tensor-parallel-size') + 1] == '2'
        assert argv[argv.index('--nnodes') + 1] == '2'
        assert argv[argv.index('--node-rank') + 1] == str(rank)
        assert argv[argv.index('--host') + 1] == '127.0.0.1'
        assert argv[argv.index('--dist-init-addr') + 1] == '[fd12::1]:18408'
        assert compiled['env']['NCCL_SOCKET_IFNAME'] == '=eth0'
        assert compiled['env']['NCCL_SOCKET_FAMILY'] == 'AF_INET6'
        assert compiled['env']['NCCL_IB_DISABLE'] == '1'
        assert compiled['env']['SGLANG_HOST_IP'] == f'fd12::{rank+1}'
        assert compiled['env']['CUDA_VISIBLE_DEVICES'] == f'GPU-{rank}'
        assert compiled['publishable'] == (rank == 0)
    assert ranks[0]['topology'] == ranks[1]['topology']
    assert ranks[0]['static_fraction'] == ranks[1]['static_fraction']
    # 4 GiB per node is sufficient for its single 3 GiB loading process; the old
    # single-node TP2 accounting would incorrectly require 6 GiB on every node.
    value['members'][1]['resources']['memory_bytes'] = 2 << 30
    with pytest.raises(ValueError, match='system memory'):
        group.rank_launch(value, record(), 0, [24 << 30])


def test_ipv4_and_member_order_have_canonical_binding(group):
    value = plan()
    for rank, member in enumerate(value['members']):
        member['address'] = f'10.2.1.{rank+1}'
    first = group.rank_launch(value, record(), 0, [24 << 30])
    value['members'].reverse()
    assert group.rank_launch(value, record(), 0, [24 << 30]) == first
    assert first['env']['NCCL_SOCKET_FAMILY'] == 'AF_INET'
    assert first['argv'][-1] == '10.2.1.1:18408'


@pytest.mark.parametrize('tp', [4, 8])
def test_multiple_local_gpus_preserve_global_tp_and_local_device_assignment(group, tp):
    value = plan()
    value['tensor_parallel_size'] = tp
    for rank, member in enumerate(value['members']):
        member['resources']['memory_bytes'] = 16 << 30
        member['resources']['devices'] = [dict(id=f'GPU-{rank}-{index}', backend='cuda',
            memory_bytes=12 << 30, exclusive=True) for index in range(tp // 2)]
        member['physical_gpu_bytes'] = [24 << 30] * (tp // 2)
    compiled = group.rank_launch(value, record(), 1, [24 << 30] * (tp // 2))
    assert compiled['argv'][compiled['argv'].index('--tensor-parallel-size') + 1] == str(tp)
    assert compiled['env']['CUDA_VISIBLE_DEVICES'] == ','.join(f'GPU-1-{index}' for index in range(tp // 2))


def test_capacity_and_launch_settings_are_part_of_certificate_binding(group):
    value = plan()
    first = group.rank_launch(value, record(), 0, [24 << 30])
    value['members'][1]['physical_gpu_bytes'] = [32 << 30]
    second = group.rank_launch(value, record(), 0, [24 << 30])
    assert first['topology']['launch_sha256'] != second['topology']['launch_sha256']
    assert second['static_fraction'] < first['static_fraction']
    value['context_length'] = 8192
    third = group.rank_launch(value, record(), 0, [24 << 30])
    assert third['topology']['launch_sha256'] != second['topology']['launch_sha256']
    with pytest.raises(ValueError, match='capacity changed'):
        group.rank_launch(value, record(), 1, [24 << 30])


@pytest.mark.parametrize('change', ['bad_rank', 'rank_bool', 'public', 'mixed', 'duplicate_ip',
    'duplicate_gpu', 'shared_gpu', 'local_count', 'bad_interface', 'loopback_interface',
    'protocol_bool', 'recipe', 'context', 'parallel', 'rendezvous', 'port_conflict',
    'control_conflict', 'nonuniform', 'extra', 'unmeasured', 'small_budget', 'model'])
def test_invalid_or_unsafe_plan_fails_before_any_rank_start(group, change):
    value, snapshot = plan(), record()
    member = value['members'][1]
    if change == 'bad_rank': member['rank'] = 3
    elif change == 'rank_bool': member['rank'] = True
    elif change == 'public': member['address'] = '8.8.8.8'
    elif change == 'mixed': member['address'] = '10.0.0.1'
    elif change == 'duplicate_ip': member['address'] = 'fd12::1'
    elif change == 'duplicate_gpu': member['resources']['devices'][0]['id'] = 'GPU-0'
    elif change == 'shared_gpu': member['resources']['devices'][0]['exclusive'] = False
    elif change == 'local_count': member['resources']['devices'].append(deepcopy(member['resources']['devices'][0]))
    elif change == 'bad_interface': member['interface'] = 'eth0,public0'
    elif change == 'loopback_interface': member['interface'] = 'lo'
    elif change == 'protocol_bool': value['protocol'] = True
    elif change == 'recipe': value['recipe_id'] = 'unverified-version'
    elif change == 'context': value['context_length'] = True
    elif change == 'parallel': value['parallel'] = 0
    elif change == 'rendezvous': value['rendezvous_port'] = 80
    elif change == 'port_conflict': value['rendezvous_port'] = 30000
    elif change == 'control_conflict': member['control_port'] = value['rendezvous_port']
    elif change == 'nonuniform': value['tensor_parallel_size'] = 4
    elif change == 'extra': member['command'] = 'anything'
    elif change == 'unmeasured': member['physical_gpu_bytes'] = [True]
    elif change == 'small_budget': member['resources']['devices'][0]['memory_bytes'] = 2 << 30
    elif change == 'model': snapshot['sha256'] = 'c' * 64
    with pytest.raises(ValueError):
        group.rank_launch(value, snapshot, 0, [24 << 30])


def test_heterogeneous_group_rejects_remote_static_memory_shortfall(group):
    value = plan()
    value['members'][1]['physical_gpu_bytes'] = [256 << 30]
    with pytest.raises(ValueError, match='static memory'):
        group.rank_launch(value, record(), 0, [24 << 30])


def test_environment_cannot_override_collective_or_model_identity(group):
    compiled = group.rank_launch(plan(), record(), 0, [24 << 30])
    env = group.environment(dict(PATH='/usr/bin', CUDA_HOME='/cuda', HF_TOKEN='secret',
        PANTHEON_APP_RPC_TOKEN='secret', SGLANG_DISTRIBUTED_INIT_METHOD_OVERRIDE='tcp://public:1234',
        NCCL_SOCKET_IFNAME='public0', GLOO_SOCKET_IFNAME='public0', HOST_IP='8.8.8.8',
        MASTER_ADDR='8.8.8.8', MASTER_PORT='1', WORLD_SIZE='99', RANK='99'), compiled)
    assert env['NCCL_SOCKET_IFNAME'] == '=eth0' and env['GLOO_SOCKET_IFNAME'] == 'eth0'
    assert env['PATH'] == '/usr/bin' and env['CUDA_HOME'] == '/cuda'
    assert not {'HF_TOKEN', 'PANTHEON_APP_RPC_TOKEN', 'SGLANG_DISTRIBUTED_INIT_METHOD_OVERRIDE',
                'HOST_IP', 'MASTER_ADDR', 'MASTER_PORT', 'WORLD_SIZE', 'RANK'} & env.keys()


def test_worker_readiness_uses_worker_endpoint_and_leader_checks_model(group):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            if self.path == '/v1/models':
                self.wfile.write(json.dumps({'data': [{'id': 'fleet-snapshot-' + 'b' * 64}]}).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        assert group.ready_rank(server.server_port, 'b' * 64, 1) == 'worker_ready'
        assert requests == ['/health']
        assert group.ready_rank(server.server_port, 'b' * 64, 0) == 'leader_ready'
        assert requests == ['/health', '/ready', '/v1/models']
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def platform_plan():
    value = plan()
    value.update(recipe_id='sglang-0.5.20-linux-amd64-process', network_mode='platform-private')
    for member in value['members']:
        member['generation'] = 2
    return value


def test_platform_private_process_ranks_use_node_layout_and_provider_interface(group):
    layout = dict(weights='/data/cache/model-service/snapshots/' + 'b' * 64, port=41234, home='/data/state')
    ranks = [group.rank_launch(platform_plan(), record(), rank, [24 << 30], layout) for rank in range(2)]
    for rank, compiled in enumerate(ranks):
        argv = compiled['argv']
        assert argv[argv.index('--model-path') + 1] == layout['weights']
        assert argv[argv.index('--port') + 1] == '41234'
        assert argv[argv.index('--host') + 1] == '127.0.0.1'
        assert argv[argv.index('--dist-init-addr') + 1] == '[fd12::1]:18408'
        assert compiled['env']['HOME'] == '/data/state'
        assert compiled['env']['NCCL_SOCKET_IFNAME'] == '=eth0'
        assert compiled['env']['SGLANG_HOST_IP'] == f'fd12::{rank+1}'
    with pytest.raises(ValueError, match='node-local'):
        group.rank_launch(platform_plan(), record(), 0, [24 << 30])
    with pytest.raises(ValueError, match='fixed in-container'):
        group.rank_launch(plan(), record(), 0, [24 << 30], layout)


@pytest.mark.parametrize('change', [
    {'recipe_id': 'sglang-0.5.20-linux-amd64'},
    {'underlay': ['[fd00::1]:51820', '[fd00::2]:51820']},
    {'network_mode': 'fleet-wireguard'},
    {'members': 'ipv4'},
    {'members': 'wg0'},
])
def test_platform_private_rejects_mixed_or_public_networking(group, change):
    value = platform_plan()
    if change.get('members') == 'ipv4':
        for rank, member in enumerate(value['members']):
            member['address'] = f'10.0.0.{rank+1}'
    elif change.get('members') == 'wg0':
        value['members'][1]['interface'] = 'wg0'
    else:
        value.update(change)
    with pytest.raises(ValueError):
        group.rank_launch(value, record(), 0, [24 << 30], dict(weights='/w', port=41234, home='/h'))
    # The process recipe is only valid with the provider network.
    container = plan(); container['recipe_id'] = 'sglang-0.5.20-linux-amd64-process'
    with pytest.raises(ValueError):
        group.rank_launch(container, record(), 0, [24 << 30])
