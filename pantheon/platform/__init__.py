"""Platform services independent of the optional Pantheon-Agent App.

Keep imports here lazy: starting Fleet management must not start Agent teams,
memory routing, model inference, or chat background workers.
"""
