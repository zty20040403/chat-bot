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
| P4 | Each worker registers, receives a real job and serves its own expiring preview | Worker package built; three-host Nix configuration committed, encrypted credential push awaiting authorization; live acceptance pending |
| P5 | Owner dispatch, external grant requirement, resource reservation, grant revocation and checkpoint recovery | Existing mechanisms; three-host live acceptance pending |
| P6 | Registered targets on all three, observed incident and recovery, searchable evidence; approved bounded remediation via the single Ops backend | Ops bridge and explicit console authorization implemented; isolated PostgreSQL and desktop/mobile checks passed; production acceptance pending |
| P7 | Fixed revision preflight, approval, serial verification and rollback contracts for three hosts through the single Ops backend | Evidence protocol tested on three-host fixtures; upstream exact-source compatibility, durable orchestration and live acceptance pending |

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

The shared configuration commit `a5966aa` has not been pushed: the credential
publication gate needs explicit approval. Do not work around that gate by copying
credentials through another channel. This does not prevent code-only development
or isolated tests; it does prevent claiming the new three-host rollout is live.

## P7 Protocol Findings (2026-09-07)

The live catalog and pinned upstream source were both inspected. The adapter in
`src/cluster_control/deployment_ops_protocol.py` checks exact host, repository,
profile, flake attribute, Git commit, workspace revision/tree, artifact and runtime
baseline. It validates the upstream **BLAKE3** lock digest against file content and
also records our **SHA-256** lock digest; these are not interchangeable. Successful
job state alone is insufficient: a verification receipt must confirm both running
and persistent system profiles. Job results use bounded UTF-8 byte pagination.

The adapter is an evidence/protocol layer, not a deployed P7 executor. Its methods
do not approve or execute writes. The existing P7 contract scheduler still needs
to be connected to the Ops operation ledger and recovery policy.

One upstream compatibility issue must be resolved without weakening the contract:
`workspace.create` produces a clean workspace with `base_commit` but no
`commit_hash`. The pinned hub's `deploy.prepare` rejects this. Calling
`workspace.commit` always creates a new commit, even with the same tree. That would
change `self.rev` and violate the requested exact source identity. Do not hide the
difference or publish an unnecessary synthetic commit. Upstream should explicitly
accept the base commit of an unchanged, clean workspace; dirty workspaces must
still be rejected. The adapter can form the exact prepare request, but the
currently pinned hub will reject it; the orchestrator must surface this upstream
conflict rather than silently changing revisions.

Also, `workspace.create` currently pins the observed remote head, not an arbitrary
older commit. An old-commit deployment needs an explicit upstream exact-checkout
capability; it must never be approximated by deploying today's head. No shared
MaxOps package has been patched or switched as part of these isolated checks.
