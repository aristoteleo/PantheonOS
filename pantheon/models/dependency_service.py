"""Owner-side Model Services control facade for ordinary App dependencies.

The gateway pins consumer/provider generations and injects ``policy_id``; never
expose this object as an unauthenticated public endpoint. Policies authorize
whole connector publications, not individual models within a connector.
The data-plane issuer MUST enforce the same consumer lifetime on issued grants.
There is deliberately no fallback to the owner's workload-connect credential.
"""
from copy import deepcopy
import re

from .errors import ControlError


_ID = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')


class ModelServiceControl:
    def __init__(self, client, *, policies, issue_connection=None):
        from pantheon.apps.dependency_assembly import _identity, _copy
        # Identity validation is shared with ordinary App dependency assembly.
        self.client, self.issue_connection = client, issue_connection
        self.policies = _copy(policies)
        if not isinstance(self.policies, dict) or not 1 <= len(self.policies) <= 64:
            raise ValueError('Supply explicit model consumer policies')
        for name, policy in self.policies.items():
            if (not isinstance(name, str) or not _ID.fullmatch(name) or not isinstance(policy, dict)
                    or set(policy) != {'consumer', 'deployments', 'routes', 'allow_wake'}
                    or type(policy['allow_wake']) is not bool):
                raise ValueError('Invalid model consumer policy')
            _identity(policy['consumer'])
            deployments, routes = policy['deployments'], policy['routes']
            if (not isinstance(deployments, dict) or len(deployments) > 64
                    or not isinstance(routes, dict) or len(routes) > 128):
                raise ValueError('Invalid authorized model publications')
            for deployment, binding in deployments.items():
                if (not isinstance(deployment, str) or not _ID.fullmatch(deployment)
                        or not isinstance(binding, dict)
                        or set(binding) != {'node_id', 'instance_id', 'revision', 'generation', 'component', 'port'}
                        or binding['component'] != 'backend' or binding['port'] != 'http'):
                    raise ValueError('Pin an exact model connector binding')
                _identity({k: binding[k] for k in ('node_id', 'instance_id', 'revision', 'generation')})
            if any(not isinstance(k, str) or not _ID.fullmatch(k) or type(v) is not int or v < 1
                   for k, v in routes.items()):
                raise ValueError('Pin model route revisions')

    async def model_services_control(self, *, policy_id, operation, arguments):
        """Only operation/arguments are caller supplied; policy is gateway-bound."""
        if not isinstance(policy_id, str) or policy_id not in self.policies:
            raise ValueError('Unknown model dependency policy')
        status, result = 200, {}
        try:
            result = await self._request(self.policies[policy_id], operation, arguments)
        except ControlError as error:
            status = error.status if type(error.status) is int and 400 <= error.status <= 599 else 503
        except Exception:
            status = 503
        return {'protocol': 1, 'operation': operation, 'status': status, 'result': result}

    async def _request(self, policy, operation, args):
        shapes = {'deployments': set(), 'routes': set(), 'resolve': {'route_id', 'requirements'},
                  'connect': {'binding'}, 'direct_connect': {'binding', 'peer_id'},
                  'engine_idle': {'deployment_id', 'action', 'revision'}}
        if not isinstance(operation, str) or operation not in shapes or not isinstance(args, dict) or set(args) != shapes[operation]:
            raise ControlError(403)
        if not policy['deployments']:
            # An explicitly empty policy needs no Hub/GPU availability. It
            # permits an initial BYOK/budget-only Agent without ambient access.
            if operation in ('deployments', 'routes'):
                return {operation: []}
            raise ControlError(403)
        rows = {row['deployment_id']: row for row in await self.client.deployments()
                if row['deployment_id'] in policy['deployments']
                and row.get('binding') == policy['deployments'][row['deployment_id']]}
        if operation == 'deployments':
            return {'deployments': list(rows.values())}
        if operation in ('routes', 'resolve'):
            routes = [route for route in await self.client.routes()
                      if policy['routes'].get(route['route_id']) == route['revision']
                      and all(c['deployment_id'] in rows for c in route['candidates'])]
            if operation == 'routes':
                return {'routes': routes}
            route = next((r for r in routes if r['route_id'] == args['route_id']), None)
            if route is None:
                raise ControlError(403)
            result = await self.client.hub_request('POST', '/api/model-services/routes/' + route['route_id'] + '/resolve', args['requirements'])
            # A concurrent directory or route update must not expand the grant.
            if result.get('route') != route or any(
                    c['deployment'] != {**rows.get(c['deployment']['deployment_id'], {}), 'models': [c['model']]}
                    for c in result.get('candidates', [])):
                raise ControlError(409)
            return result
        if operation in ('connect', 'direct_connect'):
            row = next((r for r in rows.values() if r.get('binding') == args['binding'] and r['state'] == 'ready'), None)
            if row is None:
                raise ControlError(403)
            peer = args.get('peer_id')
            if operation == 'direct_connect' and (not isinstance(peer, str) or not re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,128}', peer)):
                raise ControlError(400)
            if self.issue_connection is None:
                raise ControlError(503)
            return await self.issue_connection(consumer=deepcopy(policy['consumer']),
                                               deployment=deepcopy(row), peer_id=peer)
        row = rows.get(args['deployment_id'])
        if (row is None or row['revision'] != args['revision'] or args['action'] not in ('status', 'wake')
                or not policy['allow_wake']):
            raise ControlError(403)
        result = await self.client.hub_request('POST', '/api/model-services/' + row['deployment_id'] + '/engine-idle',
                                             {'action': args['action'], 'revision': args['revision']})
        if result['deployment'].get('binding') != row['binding']:
            raise ControlError(409)
        return result
