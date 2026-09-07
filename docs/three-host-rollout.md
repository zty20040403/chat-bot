# Three-host P1-P7 rollout

Scope: h310, h610 and tank. Owner: Kenneth (QQ 3526452465).
Management writes require an authenticated, contract-bound console approval.
Worker resource borrowing is separate from host root permissions.

## Acceptance Ledger

| Phase | Required evidence | Current state |
| --- | --- | --- |
| P1 | Fresh authenticated status for all three hosts | Management catalog and root identity checks verified; refresh read observations in final audit |
| P2 | Diagnostic evidence names the actual target and observer; a probe from each worker | Pending three-worker deployment |
| P3 | Prepare, review, approve, execute and query a harmless command on each host | Verified on all three with `id -u`, 2026-09-07 |
| P4 | Each worker registers, receives a real job and serves its own expiring preview | Worker package built; encrypted credential publication approved on 2026-09-07; configuration publication and live acceptance pending |
| P5 | Owner dispatch, external grant requirement, resource reservation, grant revocation and checkpoint recovery | Existing mechanisms; three-host live acceptance pending |
| P6 | Registered targets on all three, observed incident and recovery, searchable evidence; approved bounded remediation via the single Ops backend | Ops bridge and explicit console authorization implemented; isolated PostgreSQL and desktop/mobile checks passed; production acceptance pending |
| P7 | Fixed revision preflight, approval, serial verification and rollback contracts for three hosts through the single Ops backend | Durable Ops runner integrated; isolated PostgreSQL scenarios passed; upstream exact-source compatibility and live acceptance pending |

Do not describe a configured host as a verified runtime. Do not perform destructive
service or network tests on classmates' workloads. Use disposable previews and
the gaoji worker for controlled recovery tests. Keep evidence and operation IDs.

## Worker Layout

The control plane and PostgreSQL stay on h610. Its control API listens only on the
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

The shared configuration commit `a5966aa` (rebased as `5b888c5` onto `2418d67`)
has not yet been pushed. On 2026-09-07 the owner explicitly approved publishing
the two SOPS-encrypted worker credentials to `imdomestic/nix-config`. No plaintext
credentials may be published. Deployment still requires the freshness check and
three-host runtime acceptance; authorization alone is not evidence of a rollout.

## P7 Protocol Findings (2026-09-07)

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

The pinned upstream has a compatibility issue:
`workspace.create` produces a clean workspace with `base_commit` but no
`commit_hash`. The pinned hub's `deploy.prepare` rejects this. Calling
`workspace.commit` always creates a new commit, even with the same tree. That would
change `self.rev` and violate the requested exact source identity. Do not hide the
difference or publish an unnecessary synthetic commit. Upstream should explicitly
accept the base commit of an unchanged, clean workspace; dirty workspaces must
still be rejected. The opt-in patch `nix/patches/ops-exact-source.patch` implements
that contract in both hub and executor. The adapter independently rejects dirty
or inconsistent workspace state. The currently running unpatched hub still rejects
a clean workspace; the orchestrator surfaces that conflict rather than changing
the requested revision.

Also, `workspace.create` currently pins the observed remote head, not an arbitrary
older commit. The patch adds explicit `source_commit`, requiring a checked remote
head and a real commit reachable from it. The adapter supplies that parameter for
old-commit requests; it must never substitute today's head. Seven upstream Rust
tests passed, including actual Git object/ancestry checks and Hub HTTP deployment
regressions. `nix/ops-compat-package.nix` packages the opt-in patch with the existing
upstream test suite. Its Linux Nix build with the upstream nixpkgs pin passed all
74 Nextest tests on h610. The shared configuration now has local package bindings
for the h610 hub and all three executors, plus the three Ops deployment targets.
The final consuming derivation and three-host switch acceptance remain pending;
no production MaxOps package has been switched by these checks.
