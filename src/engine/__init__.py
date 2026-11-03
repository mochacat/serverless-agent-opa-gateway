"""The engine subpackage: in-process OPA/Rego evaluation + session state.

- :mod:`~src.engine.policy_engine` — the deterministic ``PolicyEngine``
  (DESIGN §4.4) that evaluates ``policies/*.rego`` in-process via regopy.
- :mod:`~src.engine.session_store` — the swappable store supplying trajectory
  (D) and rate (E) state, injected by the gateway.
"""
