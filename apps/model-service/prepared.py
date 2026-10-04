"""Initialize the original Connector from an ordinary Fleet prepared start.

Bundled by pantheon.models.connector_package with the shared runtime-config
reader. No owner token, Agent settings, engine installation or model publication.
"""


def initialize(connector, configuration):
    if (set(configuration.values) != {'connector'} or configuration.credentials
            or not hasattr(configuration.values['connector'], 'items')):
        raise ValueError('Supply one prepared connector value and node credential references')
    value = dict(configuration.values['connector'])
    if (set(value) - {'engine', 'endpoint', 'secret_ref'}
            or not all(isinstance(v, str) for v in value.values())):
        raise ValueError('Prepared connectors accept only engine, endpoint and optional node credential reference')
    expected = connector.configuration(value)
    if connector.config is None:
        connector.configure(value)
    elif connector.config != expected:
        raise ValueError('Prepared connector conflicts with retained configuration; use explicit Model Services recovery')
    # A matching restart preserves the stored configuration and admission state.
    # It never reconfigures, clears recovery state, wakes or reloads an engine.


def main():
    from _fleet_runtime_config import load_runtime_configuration
    from server import main as serve
    serve(prepare=lambda connector: initialize(connector, load_runtime_configuration(required=True)))


if __name__ == '__main__':
    main()
