# ACORN Example

This example is a tiny target repository fixture for trying the R2A MVP.

Useful commands from the R2A project root:

```bash
r2a init --repo examples/acorn
r2a plan --repo examples/acorn --goal "add HNSW oversampling baseline"
r2a check --repo examples/acorn
r2a workflow --repo examples/acorn --goal "add HNSW oversampling baseline" --executor codex --auto-approve
```

The `mock_results` directory intentionally contains one good CSV and one bad CSV so the Manager Stage can demonstrate a failing check.
