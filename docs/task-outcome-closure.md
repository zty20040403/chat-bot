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

## Live Task 59 Findings

2026-09-10: the explicit-entry repair (`f64f54a`, nix-config `dcaf138`)
created task 59 through the real planner and durable queue. Its three operator
steps ran concurrently. The task correctly remained partial: the h610 directory
scan exceeded its 300-second upstream deadline. No cleanup was executed.

The actual UTF-8 Markdown report (5,143 bytes, SHA-256
`5500502dd8be4e65e7efa1305e6908e0d1d87a1de5e55ca4ccd175091cc6555f`)
was acknowledged in the QQ group file list. An initially ambiguous upload was
reconciled without a second upload. Final deliveries 3074 and 3075 both have
native message receipts; the latter reports the settled file receipt. This
proves live delivery and reconciliation, not a live process-loss test.

The live run exposed four gaps that fixture-only checks had not caught:

- Durable approval proposals held a worker while awaiting a code. They now
  return a persistent handle immediately and yield to external continuation.
- New planner contracts could omit typed outcome checks. Each acceptance
  criterion now requires an explicit check; legacy version-1 checkpoints
  remain readable. Full host inspection also has a coverage gate independent
  of the model's generic review.
- The compact monitoring client's metrics permission was absent. The Nix
  client configuration adds only `metrics:read`; native metric tool evidence
  is also accepted when its target, timestamps and observations match.
- A late confirmed file receipt left the stored report saying 0/1 delivered.
  Receipt settlement now rebuilds the report without erasing unresolved
  acceptance failures or promoting a partial task incorrectly.

The integrated follow-up passed 903 tests against the isolated PostgreSQL
database. Production console inspection remains pending an authenticated
session. Live process-loss recovery and explicitly authorized disposable
cleanup remain open gates; neither is implied by passing unit tests.

## Follow-up Deployment and Recovery

2026-09-10 23:19 HKT: Bot `0b7b837`, nix-config `eb8056a`, h610 generation
`/nix/store/a3sdvsyacycv13ml3d3avdcndnzp67a0-nixos-system-h610-26.05.20260622.3426825`.
Only gaoji, its control service and the MaxOps hub were changed by the switch;
the existing ollama model-loader oneshot also ran during activation. The sandbox
image was reused. All three host metric endpoints returned fresh data after the
read-only permission correction. Max PID 3710160 and PostgreSQL node PID 8264
retained their original activation times.

Task 59 revision 2 reuses the first scan's stored partial output and performs
bounded, separately timed read-only checks. At 23:23 HKT, a controlled restart of
only `gaoji.service` changed its PID from 309563 to 313025. The task, completed
h310/tank runs, and pending external operation
`op_9cc20d3b522a447d8677fd3edb1b6526` survived unchanged. After that exact read-only
operation was authorized and completed, the same h610 run resumed automatically;
the operation was not submitted twice. This verifies live continuation recovery
at the external-wait boundary, not every possible upload/crash boundary.

Reading the production console projection for task 59 exposed another real
integration error: native read receipts use `operation` for a string name,
whereas managed-operation receipts put an object there. The lifecycle view
incorrectly assumed the latter and raised `AttributeError`. It now uses the
same envelope decoder as durable continuations and does not label ordinary
metric reads as missing administrator approval. The targeted outcome,
continuation and receipt tests passed (45 tests). Browser interaction still
requires the user's authenticated session; testing this backend projection
does not substitute for a real browser acceptance.
