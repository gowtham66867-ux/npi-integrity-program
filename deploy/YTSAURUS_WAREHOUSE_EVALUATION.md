# YTsaurus as the NPI detection warehouse

**Decision (2026-10-08): evaluate with synthetic data; do not replace the deployed warehouse yet.** YTsaurus is a plausible platform for a much larger, shared claims lake and distributed batch processing. The present NPI pilot is small and its deterministic detection pipeline is tightly coupled to DuckDB SQL. A production migration needs a separate benchmark, rule-parity proof, security design, and PHI authorization.

## Current baseline

Read-only inspection of `data/warehouse.duckdb` on 2026-10-08:

| Item | Observed |
|---|---:|
| Warehouse file | 75 MiB (`du -h`) |
| Synthetic claim lines | 588,445 |
| Claim-role rows | 787,316 |
| Providers | 5,665 |
| Graph edges | 13,532 |
| Detection rules | 18 |
| Rule-hit rows across two saved runs | 236 |
| Alerts across two saved runs | 102 |

Warm/local DuckDB samples on this machine (three runs, milliseconds; diagnostic only, not a controlled benchmark): claims grouped by rendering NPI and distinct patient, 19.8/15.3/11.2; claim-to-provider join count, 4.6/2.2/2.0; 50-row claim lookup, 10.7/4.8/4.6. These numbers cannot predict YTsaurus performance.

## Fit and migration shape

YTsaurus [static tables](https://ytsaurus.tech/docs/en/user-guide/storage/static-tables) fit immutable, versioned source snapshots and large historical claims. [YQL](https://ytsaurus.tech/docs/en/overview/about) is the closer match for distributed batch staging and detection. [CHYT](https://ytsaurus.tech/docs/en/user-guide/data-processing/chyt/yt-tables) can query schematized YTsaurus tables through ClickHouse SQL, but it is not a DuckDB compatibility layer. The Python client provides a way to load and read tables, and YTsaurus dynamic tables exist for keyed mutable data; neither eliminates the SQL and application rewrite here.

Proposed synthetic-only proof of concept:

1. Provision an isolated x86_64 Linux YTsaurus test cluster and private access path. The official local demo is not production or performance infrastructure. Keep the existing Cloud Run service and Firestore case store untouched.
2. Upload versioned raw/staged tables with explicit schemas under a run namespace. Preserve original SHA-256 manifest, source version, file and row provenance, and exact as-of snapshot selection. Keep patient IDs and claim data synthetic.
3. Port the normalization/build steps to YQL, including macros, as-of views, `CREATE OR REPLACE TABLE`, and the graph construction. Port the 18 rule queries one by one. Thirteen rules use ordered list/flatten operations, 11 use date functions, three use `QUALIFY`, and all 18 use typed array/struct literals. These require semantic review, especially ordered evidence IDs and deterministic fingerprints.
4. Materialize results as immutable per-run tables. Keep the current reviewer API backed by DuckDB/Firestore until a read adapter or export can return identical alerts, evidence, scores, and rerun results.
5. Compare the same synthetic inputs and rule versions against DuckDB. Require 18/18 rule result parity (including order-sensitive evidence), identical alert IDs/fingerprints and scorecards, and as-of replay. Benchmark end-to-end ingest, build, detect, evidence lookup, concurrency, failure recovery, and total cost at current and projected volumes. Only migrate if a measured need justifies the cluster.

The [YTsaurus installation guide](https://ytsaurus.tech/docs/en/admin-guide/install-ytsaurus) says Kubernetes deployment needs at least three nodes of at least 4 vCPU and 8 GB RAM each. The [local quick-start](https://ytsaurus.tech/docs/en/overview/try-yt) warns that Apple Silicon requires x86 emulation and may not work reliably; this machine is arm64 and its Docker daemon was unavailable, so no YTsaurus cluster or comparative benchmark was run. A three-node baseline is materially larger than the current single 75 MiB DuckDB artifact; actual cloud cost must be quoted before provisioning.

## PHI/security gate

This pilot remains synthetic-only and is **not approved for PHI**. No data was uploaded to YTsaurus. A self-managed YTsaurus cluster would require a documented BAA/contractual chain for the entities and cloud services that handle ePHI, a risk analysis, private networking, IAM, encryption at rest, backup/restore, retention and deletion, audit collection and review, and incident response. The [YTsaurus native-encryption guide](https://ytsaurus.tech/docs/en/admin-guide/native-encryption) says internal RPC traffic is plaintext by default and describes TLS/mTLS configuration; default installation must not be presumed sufficient. Confirm version and configuration in a test cluster. Do not infer HIPAA compliance from the availability of technical controls. The existing [HIPAA readiness assessment](HIPAA_READINESS.md) remains the release gate.

## License check

The [YTsaurus core repository](https://github.com/ytsaurus/ytsaurus/blob/main/LICENSE), [Kubernetes operator](https://github.com/ytsaurus/ytsaurus-k8s-operator/blob/main/LICENSE), and [web UI](https://github.com/ytsaurus/ytsaurus-ui/blob/main/LICENSE) each declare **Apache License 2.0**, not MIT. Apache 2.0 permits commercial and private use and includes redistribution, attribution/NOTICE, modification-notice, and patent terms; review the [license text](https://www.apache.org/licenses/LICENSE-2.0) for the exact obligations. The current warehouse engine, [DuckDB](https://github.com/duckdb/duckdb/blob/main/LICENSE), uses the MIT license. The NPI Integrity repository currently has no top-level LICENSE/COPYING/NOTICE file and no license metadata in `pyproject.toml`; this check does not establish a license for TexMed's own code. Third-party dependencies and container contents need a separate bill-of-materials review before distributing a product. Open-source licensing is separate from HIPAA authorization.

## Recommendation

Keep DuckDB for this pilot. Revisit YTsaurus when projected claim volume or multi-team concurrency makes the current architecture inadequate, and then use the synthetic parity harness above before any deployment decision. Do not route production claims or PHI through a demo cluster.
