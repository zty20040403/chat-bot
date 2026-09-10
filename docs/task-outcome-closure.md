# Task Outcome Closure

## Acceptance Checklist

This work extends the durable task runtime. Completing a command, verifying its
effect, and delivering the report are separate facts. None may stand in for the
others. Existing task revisions, ownership checks and approval grants remain in
force.

- [x] Deploy service/host outcome verification to h610 first.
- [x] Persist per-task acceptance criteria and immutable, scoped tool evidence.
- [ ] Verify host inspection coverage, service effects and disk before/after data.
- [x] Require structured findings, completed/unresolved work, authorization needs
      and next verification from specialists; validate their evidence references.
- [x] Bind supervisor conclusions to the acceptance matrix, including partial work.
- [ ] Recover final messages and files across process loss; resolve ambiguous
      receipts without blind retransmission. Test each crash boundary.
- [ ] Show inspection, findings, authorization, execution, verification and
      delivery in one live task detail view, with commands and evidence.
- [ ] Run an actual read-only h610/h310/tank inspection and confirm final delivery.
- [ ] Exercise authorized cleanup only on explicitly scoped disposable test data;
      compare before/after and verify unrelated services/files are untouched.
- [ ] Commit, deploy and verify the complete flow on h610.

## Boundaries

An LLM may propose criteria and assess content, but cannot manufacture trusted
tool observations or approve its own server changes. Typed server checks use
host-collected receipts. Evidence is bound to task, revision, scope and source
run, with full payload hashes. Unavailable, stale, contradictory or wrong-target
evidence is explicitly unverified. A read-only inspection can complete while
finding unhealthy services: finding a problem is not the same as fixing it.

Restart checks prove systemd or boot identity, not application endpoint health.
Disk differences are observed changes in free space, not proof that every byte
was exclusively reclaimed by one command. Live cleanup must not delete databases,
user files, in-use images or other people's services.

## First Deployment

2026-09-10: Bot `bd96c00`, nix-config `39469f8`, h610 generation
`/nix/store/qi650n1k85pywli2kj34xbh4chnhy3hr-nixos-system-h610-26.05.20260622.3426825`.
The dry activation and switch restarted gaoji and its control service. Max PID
3710160 and PostgreSQL node PID 8264 retained their original activation times.
The existing oneshot ollama-model-loader also ran during activation and exited.
This deployment is not acceptance of the remaining checklist.

## Local Verification

2026-09-10: before deployment, the full regression suite passed 889 tests,
including an isolated PostgreSQL migration,
scoped evidence persistence, concurrent file-outbox claims, crash recovery,
unknown receipt handling and task revision fencing. TypeScript and the production
UI build passed. Isolated Playwright runs at 1440px and 390px verified that SSE
updates preserve the open task dialog, selected evidence and keyboard focus,
with no dialog overflow or browser errors.

This is not a production acceptance result. Remaining review gates include
the actual bot inspection and group receipt checks, followed by explicitly
authorized disposable cleanup. No real cleanup was performed during these local
checks.

Follow-up verification passed 894 tests after adding explicit task-entry contract
preparation, host CPU/memory metrics, later-failure rejection, evidence-backed
cleanup estimates and durable permanent file-rejection notifications. The service
API continues to enforce the existing host observation policy. CPU figures are
five-minute rates, not instantaneous usage; unavailable samples stay unknown.

## Outcome Deployment and Live Gate

2026-09-10 22:25 HKT: Bot `d041a0e`, nix-config `627e443`, h610 generation
`/nix/store/hglhbi4xk74z0fldqiilv562x21ach05-nixos-system-h610-26.05.20260622.3426825`.
Both gaoji services started and the QQ WebSocket reconnected. Max and the
PostgreSQL node retained their prior PIDs and activation times. This deployed
the evidence, structured reports, outcome checks, file outbox and console panel;
the sandbox image was not rebuilt.

The first live inspection submission failed before creating a task: the explicit
entry prompt omitted the decision JSON schema, while its parser required all
fields. The model omitted or mistyped `answer`. Fixture-only planner tests had
missed that prompt mismatch. The follow-up supplies the same schema as automatic
routing and allows one validation-guided correction, never starting an invalid
task or silently inventing acceptance criteria. Evidence writes also invalidate
the live task detail resource. Real inspection, QQ receipts and authorized
cleanup remain acceptance gates, not completed claims.
