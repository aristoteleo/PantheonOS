"""Initialize the original Connector from an ordinary Fleet prepared start.

Bundled by pantheon.models.connector_package with the shared runtime-config
reader. No Agent settings or engine installation. With a ``directory`` value
(and its Hub credential) the Connector registers itself in the owner's model
directory; see directory.py.
"""


def initialize(connector, configuration):
    if ('connector' not in configuration.values or set(configuration.values) - {'connector', 'directory'}
            or set(configuration.credentials) - {'directory'}
            or ('directory' in configuration.values) != ('directory' in configuration.credentials)
            or not hasattr(configuration.values['connector'], 'items')):
        raise ValueError('Supply one prepared connector value and node credential references '
                         '(and, to self-register, a directory value with its Hub credential)')
    value = dict(configuration.values['connector'])
    if (set(value) - {'engine', 'endpoint', 'secret_ref'}
            or not all(isinstance(v, str) for v in value.values())):
        raise ValueError('Prepared connectors accept only engine, endpoint and optional node credential reference')
    expected = connector.configuration(value)
    if connector.config is None:
        connector.configure(value)
    elif connector.config != expected:
        if connector.config.get('managed'):
            raise ValueError('Prepared connector conflicts with retained configuration; use explicit Model Services recovery')
        # An attached service holds no engine state: the deployment's
        # configuration (e.g. a rotated credential reference) applies at start.
        connector.configure(value)
    # A matching restart preserves the stored configuration and admission state.
    # It never reconfigures, clears recovery state, wakes or reloads an engine.
    if 'directory' in configuration.values:
        # Deployed by Fleet: register this running instance in the owner's
        # model directory (retried in the background until the Hub accepts it).
        from directory import Registration
        Registration(connector, configuration, configuration.values['directory'],
                     configuration.credentials['directory']).start()


def main():
    from _fleet_runtime_config import load_runtime_configuration
    from server import main as serve
    serve(prepare=lambda connector: initialize(connector, load_runtime_configuration(required=True)))


if __name__ == '__main__':
    main()
