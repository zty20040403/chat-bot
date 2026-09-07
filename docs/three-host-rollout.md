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
| P4 | Each worker registers, receives a real job and serves its own expiring preview | Worker configuration in progress |
| P5 | Owner dispatch, external grant requirement, resource reservation, grant revocation and checkpoint recovery | Existing mechanisms; three-host live acceptance pending |
| P6 | Registered targets on all three, observed incident and recovery, searchable evidence; approved bounded remediation via the single Ops backend | Targets configured; backend integration and acceptance pending |
| P7 | Fixed revision preflight, approval, serial verification and rollback contracts for three hosts through the single Ops backend | Integration and acceptance pending |

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
