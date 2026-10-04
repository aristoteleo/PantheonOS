"""Compatibility entry point for the owner-side Agent App preset composer."""
from pantheon.apps.agent_deployment import compose_deployment, main

__all__ = ['compose_deployment', 'main']

if __name__ == '__main__':
    main()
