# Trusted provider mount

Place the operator-approved Python module implementing `TrustedPorts` here
(outside source control). Compose mounts this directory read-only at
`/studio/providers`; `PYTHONPATH` contains that directory.

The repository supplies the `TrustedPorts` contract in
`src/studio/app/production_factory.py`. A production module must use
authenticated principals and project-scoped providers, real GitHub credentials
and readback, independent QA, signed receipt verification, verified owner
policy and effect reconciliation. No example module can substitute for those
authorities. Store secrets in the deployment's private environment, never here.
