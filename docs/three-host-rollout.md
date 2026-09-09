# Three-host P1-P7 rollout

Scope: h310, h610 and tank. Owner: Kenneth (QQ 3526452465).
Management writes require an authenticated, contract-bound console approval.
Worker resource borrowing is separate from host root permissions.

## Acceptance Ledger

Last audit: 2026-09-09, Asia/Shanghai. This is runtime evidence, not
authorization for a new service change. Historical checks remain dated below.

| Phase | Required evidence | Current state |
| --- | --- | --- |
| P1 | Fresh authenticated status for all three hosts | Refreshed authenticated host observations were fresh and uncached for all three at epoch `1788925909`; this proves reachability at that time, not permanent health |
| P2 | Diagnostic evidence names the actual target and observer; a probe from each worker | Diagnostic runs 9/10/11 completed for h310/h610/tank; all three workers have successful HTTP probe jobs. Fault-injection coverage remains separate from production evidence |
| P3 | Prepare, review, approve, execute and query a harmless command on each host | Verified on all three with `id -u`, 2026-09-07 |
| P4 | Each worker registers, receives a real job and serves its own expiring preview | All three workers are deployed, registered and available; probe and preview jobs succeeded. Worker HTTP health is 200 and the expired preview is 404 on each host |
| P5 | Owner dispatch, external grant requirement, resource reservation, grant revocation and checkpoint recovery | Queue fix deployed at `e175de0`; all three normal probes succeeded while nine oversized/unauthorized jobs remained queued with zero attempts. Test jobs were cancelled afterwards. Seven isolated PostgreSQL regressions passed; live cross-lease task transfer remains untested |
| P6 | Registered targets on all three, observed incident and recovery, searchable evidence; approved bounded remediation via the single Ops backend | Each real Worker was stopped once and automatically started once by its bounded guardian; failed probe, repair receipt and HTTP 200 recovery recorded. Verified recovery case is searchable for all three with applicability rejection for unknown causes. Automatic case-driven action selection is not claimed |
| P7 | Fixed revision preflight, approval, serial verification and rollback contracts for three hosts through the single Ops backend | `8e62c4d` built, approved and serially verified on h310, tank and h610 through one durable deployment. h310/tank closures were unchanged; h610 updated only Bot/control services. Production rollback was deliberately not induced; isolated rollback tests remain separate evidence |

Do not describe a configured host as a verified runtime. Do not perform destructive
service or network tests on classmates' workloads. Use disposable previews and
the gaoji worker for controlled recovery tests. Keep evidence and operation IDs.

## Scoped Release and Live Recovery (2026-09-09)

Bot release `e175de051e7d47411b86834056099e5cfa3728e9` includes the queue and
receipt fixes plus the previously committed independent QQ alert switch.
Concurrent account/OTP work and migration 0026 were excluded. Exact-release
verification passed seven isolated PostgreSQL queue tests and 22 Ops tests
(the API import test needs a writable temporary cache and the legacy SQLite
test setting; it was verified separately without a production connection).
All 17 NixOS host configurations evaluated successfully using Mac/Linux where
their platform-specific dependencies were available.

Deployment `deploy_37b5423fc9a247ef95da58eed7d27196` used exact shared revision
`8e62c4d55f320fa68ac53cbdce52cad6730746ec`. It completed in serial order h310,
tank, h610. Before approval, remote freshness and the actual service diff were
checked. Only `gaoji.service` and `gaoji-cluster-control.service` changed, both on
h610; no database, classmate service or sandbox image changed. The control
service resumed its persisted deployment verification after its own restart.
h610's verified target is
`/nix/store/dzcif1g48zij5r78vwi0d78390kg46lv-nixos-system-h610-26.05.20260622.3426825`.
QQ WebSocket reconnected at 11:45:36 Asia/Hong_Kong.

Live queue acceptance used prefix `p5-release-e175de0-`. The normal jobs below
succeeded before any of the nine blocking candidates were cancelled:

| Host | Normal job | Probe latency |
| --- | --- | --- |
| h310 | `job_6a5fd0ed936c4bb0a84f41f7d28379d1` | 47 ms |
| h610 | `job_4e861ca4e68e4dd1af22ab3d837d6d1d` | 22 ms |
| tank | `job_bb96c4c9e2fb4e7d9a89fb072a2b5ff1` | 54 ms |

CPU, memory and GPU candidates retained respectively
`worker_cpu_millis_capacity`, `worker_memory_bytes_capacity` and
`gpu_not_authorized`, with zero execution attempts. All nine were cancelled
through the API; no audit job remains open. Each successful probe committed
both started and completed checkpoints, but this does not prove live transfer
between workers.

Only idle `gaoji-cluster-worker.service` instances were stopped. Each guardian
was explicitly authorized for 10 minutes, one `service.start`, 15-second probes
and one failure threshold; its target hash was checked before creation.

| Host | Guardian | Automatic repair | Recovered incident |
| --- | --- | --- | --- |
| h310 | `guardian_8e7c18691e1447c0acb19c0d48d81127` | `op_f5a7e1afce0540bdb2a6e5be01cb3928` | `incident_9c4fc4769025458e9d65d2b1db3da711` |
| tank | `guardian_9346005dbdec435c8566c37ec6bc7bf0` | `op_2b351c9c665f46929557a136ae2ddd0f` | `incident_61268af7d69e4e0688062163dd84d342` |
| h610 | `guardian_02c38e5e31be49d696bbb8ffa8933b1a` | `op_52f2a8f220804150b16894beaf49f7dd` | `incident_42bb7f1c45d14f18b83bf056d7c42f5e` |

All three produced failed-probe evidence, exactly one successful repair receipt
and a subsequent HTTP 200. All temporary guardians were cancelled after recovery.
Case `case_5d407b618eb14ba1a8a7420b3fcf37d0` records the controlled stop, evidence
and bounded remedy. Its real search endpoint returns it as applicable on all
three for a confirmed operator stop; an unknown cause is explicitly inapplicable.
This is verified case retrieval, not autonomous learning or unreviewed repair.

After checks, the Bot DSN still selected writable h610 (`100.64.0.3`), both
PostgreSQL health services had result `success`/exit 0 and active timers, and QQ
notifications remained muted. No database role change occurred in this release.
Live cross-lease transfer, QQ file delivery and an induced production rollback
remain outside this acceptance; do not imply they were tested by stopping an
otherwise idle Worker.

## Runtime Evidence (2026-09-08)

Each row below refers to a persisted successful job, not merely a configured host.

| Host | HTTP probe job | Static preview job | Borrow grant (now revoked) |
| --- | --- | --- | --- |
| h310 | `job_ed86bf13344743a692c31b551ec3cdf1` | `job_12a72a432cf14f248dc2d73570954bc9` | `grant_1940f845b5b3471ead060b16b9193f64` |
| h610 | `job_e867b00e2f304ae5b08eb83b5bfcf9cf` | `job_a46b6e9ad0fd45cd8162447ff69efc3a` | `grant_0bca90107a3f425fa6ae955d3919fcc4` |
| tank | `job_9e9bf0f8a6a2441899507963efe60395` | `job_9ad79bc5c47c4de89c1fcce4369add2c` | `grant_fdfcd386e44b4477ada0c9cfdeb253cc` |

The corresponding expired previews returned HTTP 404 during this audit:

- h310: `preview_6c85af67021e4ef681f1279d10ee1591`
- h610: `preview_b596307dc4a542c7a4c2ecf4c220643f`
- tank: `preview_c2d12ff944224b8199da82e72a0c83d3`

This confirms preview expiry independently of the ledger. It does not prove QQ
file delivery or cross-lease checkpoint recovery. Observe-only guardians
`guardian_3ec4aea2afeb4fcdbb89a4e578b8bf95` (h310),
`guardian_b2a4caea54584d22b085d4394b232a6b` (h610), and
`guardian_411abf8611af40d99947f5cd1f42d41c` (tank) completed at expiry.
Case `case_47ae8da29d2c440b8ee60a8892822aaa` is verified; that status alone is not
evidence of automatic repair or successful reuse for a later incident.

### Exact-source preflight (Historical)

Deployment `deploy_a00c820357cd4671b5d9ac841d599bb9` built the same source and
expected remote revision, `32522e3466ee4897843d137bcc93767fc958e893`, on all three.
It subsequently ended in `failed`, without activation. Do not revive its stale
approval or use its old source revision for a new switch.

| Host | Upstream change | Build completed (UTC) |
| --- | --- | --- |
| h310 | `01a07f75-60d3-7a81-817b-8511a0dbce1e` | 2026-09-08 05:47:52 |
| tank | `01a07f8f-59e4-7ba3-bd78-297073bce85e` | 2026-09-08 05:49:18 |
| h610 | `01a07f90-bd15-7bd2-a021-adf49dcd9856` | 2026-09-08 05:50:15 |

The earlier `c3080af` preflight was cancelled without activation after the source
was superseded. Other operators changed running generations during this work;
matching a target path is not proof that this deployment activated it. Any later
switch must re-fetch the remote and recheck the running baseline and authorization.

### Authorized Database Recovery and Two-host Activation

Following explicit authorization for tank maintenance and failover, **h610 is the
writable primary and tank is the streaming secondary**. The application DSN was
verified to select `100.64.0.3`, with `pg_is_in_recovery() = false`. Both nodes use
their independent `/run/qq-bot-postgres-node` socket and per-host configuration;
TLS paths are relative and there are no pending configuration restarts.

Deployment `deploy_bf024454f55a49469e2a940dad911ef3` activated shared Nix commit
`82997e4d00f83b9bd6966902a936c4c541617e14` serially on h610 then tank and completed
successfully. Both running and persistent profiles match their verified targets:

- h610: `/nix/store/yqlmcs38pky1nqdz99cgriq8nw6kyhx1-nixos-system-h610-26.05.20260622.3426825`
- tank: `/nix/store/1ah6kb0crmv8j9mmy5zh0wjdsgpljff9-nixos-system-tank-26.05.20260622.3426825`

The only service-definition change in this activation was
`qq-bot-postgres-health.service`: it no longer wants/starts the data node. The
timer remains active; six consecutive successful checks on each node and the
absence of health alerts on both Prometheus instances were verified around
2026-09-09 00:01 CST. `inactive` between checks is normal. The Bot is active and
QQ alert delivery remains muted. This deployment did not switch h310 or exercise
a rollback. The earlier same-generation two-host preflight
`deploy_29ebd05ed674446683b981e3cf6cec6a` was cancelled without activation.

The maintenance was not uninterrupted: tank's keeper performed an automatic
approximately 496 MB base backup during its transition, and the previous health
dependency interfered with stopping the node. The staged runtime configuration,
explicit timer pause during maintenance, and native recovery resolved this;
PGDATA was not manually deleted or the node re-enrolled. The shared Nix repository
records the sequence and wrong turns in `docs/incidents.md` and the procedure in
`docs/qq-bot-postgres-ha.md`. A nonzero switchover command result is not proof that
no role transition occurred; inspect actual roles before taking another action.

### P5 Queue Acceptance (2026-09-09)

Twelve minimal `probe.http` jobs were submitted with idempotency keys prefixed
`p5-audit-20260909-`: per host, one normal request and requests exceeding CPU,
memory, or GPU authorization. No stress workload was executed and resource
policies were not loosened. All nine ineligible jobs had `attempt = 0` and were
cancelled after observation. The h610/tank control jobs were initially blocked
behind oversized requests and ran only after these requests were cancelled:

| Host | Successful control job | HTTP result |
| --- | --- | --- |
| h310 | `job_675588bc67cf4105bfe470f2f8fc786e` | 200, 22 ms |
| h610 | `job_9f2408ad8665403e96ff3f72b067c64a` | 200, 26 ms |
| tank | `job_d80024cb37ba466e8eecb8558e84161b` | 200, 52 ms |

The defect was selecting a candidate before checking total available capacity,
then returning no work when it did not fit. The local fix checks capacity for
each candidate, keeps looking, paginates beyond 50 queued rows and filters other
workers' pinned jobs before locking them. It retains atomic reservations and
does not expand CPU, memory or GPU permissions. It has not yet been published or
deployed; the live audit cannot be counted as a passing full P5 acceptance.

`tests/test_worker_queue_postgres.py` passed seven checks in a unique disposable
PostgreSQL schema: physical and owner limits, three-host GPU rejection, existing
reservations, concurrent no-overcommit, queue pagination, preserving another
worker's reason, and committed-checkpoint transfer h310 -> h610 -> tank with
stale-receipt rejection and no repeated execution. The checkpoint test advances a
test clock; it does not interrupt any real worker. The schema was removed.

The authoritative upstream test job
`01a081d4-9827-78d3-8a59-6dbbe0abfaa6` returned `succeeded`, exit code 0, and seven
passing tests. Local operation `op_92fa8dd180d54ebdb5b9a80044b901c2` instead ended
`needs_attention` after an observation HTTP 409. The existing unshipped
`OpsManagementService._observe_job` change handles these transient reads. Do not
repeat the test effect based on that local status; reconcile the same upstream
handle. Publication must keep the concurrent account/OTP work separate.

Follow-up isolated receipt verification on 2026-09-09 passed eight selected tests
from `tests/test_ops_management.py`. The new HTTP 409 case covers each of h310,
h610 and tank and asserts one execution submission, two reads of the same job
handle and a successful final state. Other cases cover bounded repeated rejection,
403 without retries, unrelated handles, real job failure, observation expiry and
missing submission receipts. These use `httpx.MockTransport` and an in-memory
operation store, not a new production effect; they validate the local observation
fix without claiming it has been deployed or reconciling the historical live row.

The owner approved the scoped release and h310/h610/tank updates on 2026-09-09,
including bounded stop/start recovery checks of `gaoji-cluster-worker.service`
only. Account/OTP edits are excluded; database and classmates' services must not
be interrupted for these checks. Publication, live queue acceptance, controlled
recovery, case reuse and remaining P7 acceptance are still pending execution.
Authorization is not evidence of a completed rollout. The subsequent execution
is recorded in the scoped-release section above; this paragraph is historical.

## Worker Layout

The control plane runs on h610. PostgreSQL has data nodes on h610 and tank; the
preferred primary is h610, but actual roles must be checked, as above. The control API listens only on the
Tailscale address; the firewall opens port 8091 only on `tailscale0`. Each worker
has a separate encrypted credential and no Bot API keys, database credentials or
Ops management credential. Port 8092 serves short-lived static previews.

Workers use a dedicated Python/FastAPI/httpx runtime, Poppler and FFmpeg, not the
entire Bot or its Docker sandbox image. h310 and h610 allocate 2 CPU/2 GiB each;
tank allocates 4 CPU/4 GiB. The limits protect the shared host and do not affect
root operations through Ops. Resources come from `lib/gaoji-workers.nix` in the
shared Nix repository.

The owner's authenticated QQ identity is an explicitly configured worker-owner
alias, evaluated at each claim. It does not change the recorded actor or scope.
Other users still need grants; models cannot supply aliases through task payloads.

## Bounded Repair

The console distinguishes observation from limited repair. Limited repair requires
an authenticated administrator to review an exact registered target, service,
action (start/restart), expiry and attempt limit. A target hash rejects approval
of a target changed during review. Chat tools can still create observation-only
guardians; they cannot approve a repair policy.

The guardian uses MaxOps, not a second SSH/root backend. It binds the catalog and
credential identity at authorization. Reserving an attempt and approving its Ops
operation share one PostgreSQL transaction. Dispatch checks the policy again;
paused, cancelled, expired or changed authorizations cannot start new actions.
Already-started operations are tracked rather than blindly replayed. An attempt
whose outcome is unknown still consumes its reservation. A later successful HTTP
probe, not submission of a restart, is the evidence of recovery.

Local acceptance uses `tests/test_guardian_ops.py` with
`TEST_OPS_POSTGRES_DSN` pointing to a test-capable PostgreSQL account. It creates
and removes a unique schema, models all three hosts, injects a transaction
interruption, and checks repeat/cancel behavior with a mocked Ops transport.
`tools/verify_ops_ui.cjs` verifies native review dialogs on desktop/mobile and
ensures SSE refreshes do not replace a pending authorization.

The owner approved publication of the two SOPS-encrypted worker credentials on
2026-09-07, and all three workers are now deployed. No plaintext credentials may
be published. A later deployment still requires freshness and runtime checks;
the earlier authorization does not authorize unrelated service changes.

## P7 Protocol Findings (Historical, 2026-09-07)

The findings below explain the compatibility work. The live exact-source builds
in the 2026-09-08 ledger supersede the earlier deployment blockers, but do not
constitute activation or rollback acceptance.

The live catalog and pinned upstream source were both inspected. The adapter in
`src/cluster_control/deployment_ops_protocol.py` checks exact host, repository,
profile, flake attribute, Git commit, workspace revision/tree, artifact and runtime
baseline. It validates the upstream **BLAKE3** lock digest against file content and
also records our **SHA-256** lock digest; these are not interchangeable. Successful
job state alone is insufficient: a verification receipt must confirm both running
and persistent system profiles. Job results use bounded UTF-8 byte pagination.

The protocol adapter itself only validates evidence. `OpsDeploymentRunner` now
connects it to the existing deployment and operation ledgers. Repositories select
one backend (`ops` or the existing `ssh`), never both. Ops targets bind an exact
upstream repository and deployment profile. The Nix module and runtime both reject
missing profiles or hosts outside the management grant.

The administrator's prepare request authorizes workspace creation, preparation
and building only. Activating requires a second approval of the full preflight
hash. Child proposals carry the parent deployment/host/stage and cannot be approved
through the generic operation approval endpoint. Approval locks and rechecks the
parent's lease, fence, immutable configuration and phase in the same PostgreSQL
transaction. Dispatch checks them again. Hosts are activated in contract order;
each must produce matching running and boot profile receipts before the next one.

The Ops runner can reclaim an expired execution lease and continue observing the
same durable child operation. It never creates a new idempotency key to retry a
lost submission. An unknown receipt is not evidence that nothing happened. Such
cases stop for reconciliation; ordinary terminal failures can roll back only the
previously verified targets, in reverse order, when the approved policy permits.
Cancellation prevents new actions and waits for an already-started effect to
settle. Cancelling twice does not prematurely release the execution lease.
Bounded guardians check deployment maintenance windows before reserving and
dispatching automatic service repairs.

`tests/test_ops_deployment_runner.py` runs against a unique temporary PostgreSQL
schema with a simulated Ops transport. It covers all three targets, preflight and
activation restarts, wrong approval hashes, ordered success, a second-host failure
with first-host rollback and third-host skipping, cancellation before dispatch,
lost activation receipts, and the unpatched upstream's clean-workspace rejection.
`tests/nix/ops-deployment.nix` evaluates the three-host module bindings without
real credentials or a system switch. These are not production acceptance results.

The upstream version inspected on 2026-09-07 had a compatibility issue:
`workspace.create` produces a clean workspace with `base_commit` but no
`commit_hash`. The pinned hub's `deploy.prepare` rejects this. Calling
`workspace.commit` always creates a new commit, even with the same tree. That would
change `self.rev` and violate the requested exact source identity. Do not hide the
difference or publish an unnecessary synthetic commit. Upstream should explicitly
accept the base commit of an unchanged, clean workspace; dirty workspaces must
still be rejected. The opt-in patch `nix/patches/ops-exact-source.patch` implements
that contract in both hub and executor. The adapter independently rejects dirty
or inconsistent workspace state. The unpatched hub then rejected a clean
workspace; the orchestrator surfaced that conflict rather than changing the
requested revision. The subsequent three-host exact-source builds demonstrate
that this preparation path now works in the deployed integration.

Also, `workspace.create` currently pins the observed remote head, not an arbitrary
older commit. The patch adds explicit `source_commit`, requiring a checked remote
head and a real commit reachable from it. The adapter supplies that parameter for
old-commit requests; it must never substitute today's head. Seven upstream Rust
tests passed, including actual Git object/ancestry checks and Hub HTTP deployment
regressions. `nix/ops-compat-package.nix` packages the opt-in patch with the existing
upstream test suite. Its Linux Nix build with the upstream nixpkgs pin passed all
74 Nextest tests on h610. The shared configuration now has local package bindings
for the h610 hub and all three executors, plus the three Ops deployment targets.
The consuming packages and three-host builds have since been verified. These
isolated checks did not themselves switch a production package; full three-host
activation and rollback acceptance remain pending as recorded above.
