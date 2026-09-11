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
- [x] Show inspection, findings, authorization, execution, verification and
      delivery in one live task detail view, with commands and evidence.
- [x] Run an actual read-only h610/h310/tank inspection and confirm final delivery
      (task 59; unresolved acceptance is recorded below, not silently promoted).
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

## Historical Authorization Evidence

2026-09-11: task 59 revision 2 delivered its 7,363-byte revised report once;
the QQ file receipt and final text delivery 3076 were confirmed. Acceptance
remained 7/8: the older directory scan had been approved, but the reviewer could
not associate its logs with that historical approval. Repeating the command or
changing the task to "completed" would not repair that missing evidence.

The follow-up adds a fixed read-only controller receipt endpoint. The host looks
up only intents already persisted for the same task, requester, conversation and
permitted upstream run. Old revisions require an explicit revision checkpoint
for that same run. The original host-generated idempotency key is recomputed,
and operation, target, parameter hash and any known native job ID must match.
No model-provided job lookup, new approval or command resubmission is involved.

The controller checks the original contract hash, resource version, consumed
approval and dispatch ordering. Historical expiry does not erase an approval
that was valid at dispatch. It returns parameter hashes, not raw command bodies.
The task keeps a separate historical evidence envelope with source revision,
request hash, original timestamps and local retrieval time. This proof can
establish past authorization; it cannot satisfy current health, service effect,
reboot or disk-change checks. Missing or mismatched receipts stay unverified.

Acceptance caching now incorporates permitted upstream evidence and excludes
the reviewer's own generated evidence. A newly recovered proof causes a new
review; simply rereading the same proof does not. The console labels historical
proofs separately and no longer presents one approved operation as proof that
all operations have approval.

Targeted tests cover legacy resolved/error receipt recovery, cross-task/run/user/
scope rejection, parameter and native job mismatches, non-replay, revision races,
idempotent linking, acceptance cache invalidation and historical/current-state
separation. These are local verification, not proof that the follow-up is deployed
or that all remaining crash and cleanup gates are complete.

Five file-outbox boundaries were additionally exercised using real SIGKILL of
separate Python processes and isolated PostgreSQL schemas: queued, prepared,
claimed, uploaded-before-ack-save, and acknowledged. The QQ transport in these
tests is simulated with a durable receipt file; it is not a production QQ test.
Queued/prepared deliveries recovered and sent once. Uploaded receipts were
reconciled without retransmission, and already acknowledged files were not sent
again. A process killed after the send claim but before any observable upload
remained explicitly unknown: absence of a receipt cannot prove no remote upload
happened. This is an honest remaining delivery ambiguity, not guaranteed delivery.

Revision ancestry is now read separately from the console's 200-checkpoint page,
including a regression case where more than 200 ordinary checkpoints precede the
revision. Long tasks must not silently lose their authorized historical context.

The integrated targeted suite passed 157 tests, including real PostgreSQL
authorization receipts. The separate process-loss test passed all five SIGKILL
subcases. Production browser acceptance and explicitly authorized disposable
cleanup are still outstanding.

## Live Revision 3 and Report Correction

2026-09-11 00:46 HKT: Bot `b03ad34`, nix-config `085d8c1` deployed to h610.
The two historical task-59 authorization receipts were recovered without command
replay. Max and the PostgreSQL node retained their prior activation times.

Task 59 revision 3 still failed acceptance (5/8). Final text was acknowledged
with native QQ message ID 509073170, but this revision did not deliver a file.
The specialist reused an older evidence ID, the compiler returned both a real
sandbox file and an incompatible cluster artifact ID, and the report copied
incorrect h310 observation timestamps. These are actual report-production defects,
not proof of a network upload failure or permission to loosen acceptance.

The follow-up adds one durable, bounded read-only report-correction pass. It can
read current scoped evidence but cannot execute commands, request authorization,
create/change files or send messages. Original files and unfinished execution
remain intact. A completed correction is revalidated on resume; an interrupted
or failed correction cannot spawn an unlimited new loop. Correction transcripts
are appended to the specialist's independent history, with separate checkpoints.
Uncorrectable content remains partial for the existing independent reviewer.

Cluster artifact references are separated from QQ attachments only when matched
to an actual successful, scoped upload receipt. Unrecognized handles still fail
capture; an upload reference is not a QQ receipt. Repair reservations and step
checkpoints are read using current-revision SQL, not the console's 200-row page.

The diagnostic harness also exposed an import-time recovery side effect: opening
the plugin marked live turn 1360 crashed at 00:59:56, although its final archive
and QQ deliveries 3080/3081/3082 completed afterward. At 01:25 it was corrected
from those exact receipts and archive, with a transactional audit note; no task
or message was replayed. TurnJournal construction no longer performs recovery.
Only the real service startup does, before background workers begin.

The integrated targeted tests passed 210 cases, including file capture after
report correction, invalid cached corrections, read-only tool restrictions,
revision/cancellation fencing, evidence isolation, historical authorization,
artifact acceptance, durable outbox and interrupted external continuations.
The full step-resume path restores correction checkpoints before opening any
execution tools. A corrected evidence reference must have a recorded complete,
in-order read through the restricted evidence reader; copying an ID from the
index is not sufficient, including after recovery from a cached correction.
These checks alone are not a browser or authorized cleanup acceptance.

2026-09-11 01:45 HKT: Bot `07a18e7`, nix-config `47c9133` deployed to h610 as
`/nix/store/7fxczb0fxfyjfm82y2pk3ncvms68mqa5-nixos-system-h610-26.05.20260622.3426825`.
The shared GPU monitoring commits `3537c19` and `2c53572` were retained. Fourteen
NixOS host evaluations succeeded; gpd, m16 and x470 could not fetch their private
WireGuard input from h610 because SSH host verification failed. No trust entries
or credentials were changed. Only gaoji and its control service were restarted;
Max PID 3710160 and PostgreSQL PID 8264 retained their activation times.

Task 59 revision 4 passed the eight task-acceptance criteria. Its actual 8,307-byte
Markdown snapshot `c23f070c13402f84fdf4ef7050881c2b7bb707c26bc94ce67a171bd83ba5fca4`
was acknowledged with QQ file ID `81afe19b30904943991ec3cc4f026c9a`. It was uploaded
once; an initially unknown receipt was reconciled without retransmission. The
final text has native receipt 1349797214. The task remained partial because the
h310 specialist could see four active alerts but only three details. Investigation
found the compact fleet projection also truncated single-host inspection to three
alerts and five failed units. The follow-up keeps overview limits, explicitly
marks truncation and returns the complete provided snapshot for a single host.
The focused fleet/outcome suite passed 32 tests. This is not yet a new live h310
inspection, a production browser acceptance, or authorized cleanup acceptance.

2026-09-11 02:11 HKT: Bot `8ec164c`, nix-config `53b9815` deployed to h610 as
`/nix/store/9ajikv772460xm3rq4rxf340w16lyk7m-nixos-system-h610-26.05.20260622.3426825`.
The live projection returned all four h310 alerts with `alerts_truncated=false`.
Only the two gaoji services restarted; Max PID 473603 and PostgreSQL PID 8264
were unchanged. This confirms the projection fix, not a retroactive successful
verdict for task 59 revision 4.

## Reviewer Ownership Boundary

Inspection of revision 4's persisted run 148 found a further integration defect:
the reviewer correctly imported and checked the report, then listed the author's
original sandbox handle in its own artifacts. The normal capture step correctly
rejected access to that other isolated workspace, but consequently marked the
reviewer failed despite its valid per-file checks.

Independent acceptance now has an explicit host-selected review-only mode.
Only exact references to immutable, declared upstream artifacts are separated
from output artifacts; they are recorded as references, not new files or proof
of verification. Unknown handles, changed hashes/sizes and ambiguous references
fail without capture. The reviewer cannot produce a replacement delivery.
Regular author steps still export from their own isolated sandbox. The normal
checksum and actual independent-check gates remain necessary for file delivery.
The acceptance cache version changed so old decisions are not silently reused.

The focused artifact, report correction, runtime, file outbox and outcome suite
passed 111 tests, including the real review-step path and persisted cache reuse.
The production browser still shows an expired login; disposable cleanup remains
unauthorized. Live acceptance of this reviewer change is still pending deployment.

2026-09-11 02:25 HKT: Bot `1168a71`, nix-config `d09bcd9` deployed to h610 as
`/nix/store/z4kc9xcigddysdlz8xnv2pl0islb14k2-nixos-system-h610-26.05.20260622.3426825`.
The two gaoji services restarted; Max PID 473603 and PostgreSQL PID 8264 did not.
Task 59 revision 5 independently re-observed all three hosts without replaying
server commands. The h310 specialist succeeded with all four alert details.
Reviewer run 149 succeeded without re-exporting the author's sandbox. Acceptance
was 8/8, the new file was acknowledged after one upload, and final text receipt
11321751 was committed. The compiler remained partial: its bounded correction
pass exhausted three tool rounds before rereading all evidence. That remaining
report-production gap is not hidden by the passed checklist or file receipt.

## Concurrent File Receipts

Focused tests reproduced four failure cases in the two concurrent receipt paths:
an old reconciliation could downgrade acknowledged delivery to unknown; a late
upload result could be returned to the caller but fail to persist after a
reconciler changed sending to unknown; and a confirmed rejection could lose its
safe retry state for the same reason.

Receipt writes now lock the row and compare the observed payload. A confirmed
receipt is not downgraded. The sender may settle sending or unknown only for its
same upload attempt, and returns the persisted result when another path has won.
Stale reconciliation cannot replace a newer attempt or a safely queued retry.
The automatic retry boundary remains unchanged: only explicit not-sent results
may retry, while an unobservable upload stays unknown and is checked, not replayed.

The focused suite passed 109 tests. Two additional isolated PostgreSQL tests
passed, including five real SIGKILL boundaries with explicitly simulated QQ
transport. The local test database was stopped afterward. These checks do not
prove every production QQ crash boundary or authenticated browser interaction.

## Batched Evidence Correction

Revision 5's compiler cited eight distinct references, but its three-round
read-only correction finished only five. The correction now offers an authorized
batch reader for up to eight references, retaining the single reader and its
parent permission switch. Each reference still requires an in-order full read;
foreign evidence, skipped pages and incomplete source records receive no credit.
The pass remains single-attempt and read-only, with a 90-second ceiling, four to
eight model rounds based on the report, and at most 60,000 tool-result characters
(or the configured lower limit). It cannot repeat execution or hide unresolved
work. Large evidence may still exceed the bounded budget and must remain partial.

Inspection also found that a 12,000-character body plus JSON escaping and metadata
could exceed the tool transport limit, even when the reader claimed a full page.
Pagination now fits the entire serialized response; batches share that budget.
Only pages actually fitting the transport and cumulative budgets can count as
read. Batch and single pages can continue one another without skipping content.

The targeted evidence, report correction, policy, outcome, reviewer, runtime and
tool-loop suite passed 114 tests. The actual tool loop was exercised with mocked
model responses, eight paginated references and a smaller transport limit; the
model received parseable, complete pages and reconstructed the original evidence.
2026-09-11 03:04 HKT: Bot `08def6a`, nix-config `e9dbdaa` deployed to h610 as
`/nix/store/0fw8w024vkhib7qpcbnk7wr6ihiwyk2j-nixos-system-h610-26.05.20260622.3426825`.
Only gaoji and its control service restarted; Max PID 473603 and PostgreSQL PID
8264 retained their activation times. The sandbox image was reused.

Task 59 revision 6 performed fresh read-only inspection of all three hosts. Each
inspection step succeeded with scoped evidence. The real report correction read
12 references completely, but still cited two unread `ops_call` observations
(`evidence#3512f1e41dbabf8cfb89fa416f9a0aab` and
`evidence#d4a6f01442ad565c9afd566420088302`). They occur in findings and completed
claims, not only file metadata. The full-read gate correctly kept the report
incomplete. The model's separate warning about file-tool evidence initially
misled diagnosis; the persisted reference paths establish the actual gap.

The original compiler result also claimed it was only correcting a prior report,
not generating a new file. Correction transcripts currently append host-generated
read-only instructions to the normal execution session, and revisions reuse that
session. This is a phase-contamination risk to address, not a confirmed fixed
behavior. The independent reviewer triggered repair run 151; reviewer 152 was
still running at the last observation. No final revision-6 file receipt has yet
been confirmed. The browser remains at expired login and the Mac is locked;
authorized disposable cleanup and real production upload-loss boundaries remain
unaccepted. The overall checklist is intentionally not complete.

## Execution and Correction Contexts

At 03:19 HKT revision 6 was terminal partial, with 7/8 acceptance criteria.
The new file was acknowledged after one upload and final text receipt 1290047123
was committed. The remaining authorization criterion lacked an independent
review with valid evidence. This is not a lost-file case or a completed task.

Code inspection confirmed correction transcripts were appended to the execution
session, while explicit revisions reused that session and its frozen upstream
context. The next report writer consequently received prior read-only correction
instructions as conversation history. Correction now persists in separate phase
checkpoints with its own prompt; it assesses the original work, not whether the
correction pass itself generated a file. Feedback identifies unread/invalid
references inside the same bounded, non-streaming loop. It cannot reset the
execution budget, acquire approval or reopen command tools.

Explicit revision atomically archives selected steps' sessions and frozen
contexts, advances session versions, then starts those steps with fresh context.
Unselected steps retain their sessions. Prior results and immutable artifacts
remain available through the scoped previous-version handoff; archived raw
instructions are not fed to the new executor. Stale session writers fail their
version check. Process-loss recovery within the same revision is unchanged and
does not reopen execution after a correction checkpoint.

The focused suite passed 159 tests, including actual loop feedback, budget
exhaustion, fresh revision context, replay and untouched sibling sessions. An
isolated PostgreSQL migration/concurrency test also passed with revision archival,
stale-writer rejection and preservation of acknowledged file receipts. The test
database was stopped. Deployment and a fresh production acceptance are pending.

## Revision 7 Production Result

2026-09-11 03:41 HKT: Bot `e205356`, nix-config `36a75d4` deployed to h610 as
`/nix/store/m97g6g4azg7ri5dcy0wisbr2d3yxsmq5-nixos-system-h610-26.05.20260622.3426825`.
The gaoji and control services restarted and remain active. Max PID 473603 and
PostgreSQL node PID 8264 retained their activation times. The sandbox image was
reused. This supersedes the deployment-pending note above.

Task 59 revision 7 archived the four selected execution sessions and rebuilt
their contexts. The live phase audit found no prior correction instruction in
the new execution sessions. All three inspections succeeded. The compiler
created a new UTF-8 Chinese Markdown report, and its report validation passed
without a correction pass. The independent reviewer passed all eight acceptance
criteria. The task nevertheless remains partial because the report honestly
records missing current h610 directory measurements; a checklist alone does not
erase that unresolved work.

At 03:59 HKT the persisted QQ receipt confirmed the 7,053-byte file
`kb-59-r7-b60ee85966-report-20260911-readonly-acceptance.md` was acknowledged after
one upload. An ambiguous receipt was reconciled against the group file list;
no second upload was performed. Final text receipt 801317198 was committed.

Remaining acceptance work:

- Fresh h610 `/nix/store` usage and `/var/lib` breakdown. The report-generation
  rerun deliberately forbade new command execution and labeled earlier scans as
  historical; those figures are not a current space-attribution measurement.
  The live execution-profile catalog exposes a diagnostic profile with a
  7,200-second maximum, but the controller has its own shorter deadline and
  command submission still requires the existing approval flow. A profile query
  does not authorize or execute a scan.
- Actual production upload-in-flight process-loss boundaries. Isolated
  PostgreSQL/SIGKILL tests used simulated QQ transport. The actual earlier bot
  restart proved external-wait recovery, not every upload boundary.
- Authenticated production console interaction. The last browser observation
  was an expired login on the locked Mac. Backend projection and isolated SSE
  interaction tests do not replace this acceptance.
- Explicitly authorized disposable cleanup and before/after verification. The
  proposed h610 test directory `/var/tmp/gaoji-outcome-acceptance-20260910/`, with
  at most 256 MiB of generated test data, has not been approved or exercised.

No real cleanup, host reboot, unrelated service restart, or forced interruption
of a live upload was performed in revision 7. The full checklist remains open.

## Resumed Inspection and Expired QQ Login

2026-09-11 noon: revision 8 had ended at its task deadline without authorization.
The controller still held its old proposal as `awaiting_approval`, with attempt 0,
no approval reference and no backend job. The console incorrectly presented that
last observation as an active approval wait even though the task had ended.
The projection now marks that state unverified, retains `last_observed_status`,
and explains that the old request cannot resume the ended revision. Superseded
repair findings are also excluded from the current task view. This does not
cancel, approve or rewrite any controller record. The focused outcome and external
continuation suite passed 36 tests.

After the user's explicit request to continue, revision 9 was submitted once.
Its private approval message failed to send; group progress messages also timed
out in QQ's `NodeIKernelMsgService/sendMsg`. The gaoji NapCat container was running
and not OOM-killed. Restarting only `docker-napcat-chat-bot.service` at 12:15 HKT
revealed an explicit quick-login error: the QQ account identity had expired and
required a fresh QR login. No QQ password was read, no code was confirmed on the
user's behalf, and no server scan or cleanup was authorized. Login recovery and
fresh approval still require the user.

At 12:22:56 HKT Bot `0a69ad9` and nix-config `7c2247e` were deployed as
`/nix/store/0i7i3a5ixgp4y95f79jj5qcv7r1kh3wj-nixos-system-h610-26.05.20260622.3426825`.
Both gaoji services are active; the sandbox image was reused. Max PID 473603,
Max NapCat PID 1396 and PostgreSQL node PID 8264 retained their activation times.
The clean temporary build worktree was removed. At 12:25 the task remained at
revision 9 waiting for authorization, with its evidence and completed steps
preserved and no final delivery queued. The production console remained at an
expired login page. These observations do not close the remaining live acceptance
gates.

## QR Recovery and Revision 10

2026-09-11 13:05:07 HKT: account 3580515978 reconnected after the user scanned
the new QR. Revision 9 had already ended; its final text receipt 55915319 was
committed, and its expired controller proposal still had attempt 0 and no
backend job. It was not revived or approved. Revision 10 was created once after
the requested continuation, and its fresh private approval succeeded.

The authorized h610 job `01a08ee3-280e-73a0-b4b5-2b337b3622f4` ran once from
13:14:06 to 13:15:12. Its saved output establishes 164,852,035,584 available bytes
on the root filesystem (66% used) and 67,645,728,403 apparent bytes under
`/nix/store`. The latter is logical size, not allocated disk space or a cleanup
estimate. The diagnostic profile ran as UID 983 with a restricted PATH:
`/var/lib` traversal failed on protected directories, and docker/journalctl were
absent. Its 10,036,842,496-byte partial `/var/lib` result is not the total.
The successful final shell exit did not promote these failed subcommands.

Revision 10 remained partial. h310/tank inspection steps completed, but the
independent host coverage gate passed only h310. h610's later resource query
was 250 seconds apart from its disk/service snapshot; tank's fleet response was
stale. Tank's additional metrics also exposed a precision bug: sample
1789103538.193 was rejected against whole-second capture time 1789103538.
Freshness now tolerates only the subsecond difference within that capture second;
the 90-second age limit, wrong-host checks and rejection of future seconds remain.
The snapshot-gap gate is unchanged and now explains the specific observation to
refresh without requesting another directory traversal. Operator instructions
also distinguish read-only intent from execution identity, required tools and
partial disk statistics. These are guidance changes, not added privileges or a
guarantee that arbitrary scripts have been semantically preflighted.

The focused suite passed 63 tests. At 13:28 HKT, the 9,787-byte report
`kb-59-r10-3a5b28b2cb-h610-h310-tank-readonly-acceptance-20260911.md` was confirmed
with QQ file ID `2bcd74e125e84ff190e7dead632928e0`, after exactly one upload and
receipt reconciliation. The final text receipt was 1823582979. Neither this
delivery nor the sample precision fix completes the missing privileged read-only
directory measurements, authenticated console interaction, production upload
process-loss boundaries or explicitly authorized disposable cleanup. Deployment
of the precision/guidance changes and a fresh post-deploy observation are pending.

## Current Revision Authorization and Deployment Verification

2026-09-11: Bot `98f341a` was deployed to h610. After a separate DAE recovery,
the system switch completed and live h610/h310/tank metric projection returned
available CPU and memory, including fractional sample timestamps. Authenticated
console inspection showed revision 10's file acknowledged and final text
committed. A model-scope dropdown stayed open across live updates; the selected
task detail also survived the Bot restart. No old report was sent again.

The live lifecycle view still showed authorization unverified. Persisted receipt
inspection established that the current operation had approval and a dispatch
receipt, but old revision-8/9 requests never approved or dispatched were also
included in the current-stage boolean. The projection now scores only the
current revision's authorization. Historical receipts remain visible with their
source revision and their original passed/unverified result. They cannot grant
permission for a new revision or satisfy current health checks. Neither stored
receipts nor task acceptance results are rewritten. The focused receipt/outcome
suite passed 34 tests, including mixed old/current requests and missing current
approval. Full browser evidence-panel acceptance, fresh protected directory
measurements, live upload process-loss boundaries and approved disposable
cleanup remain open; this change does not close those gates.

## Authenticated Console Acceptance and Deployment

The build and deployment of code revision `77379c0` completed successfully.
The sandbox image was reused. Unrelated services retained their activation
times. An open HTTP stream was cancelled at the old process's graceful-shutdown
deadline; the process exited successfully and the live feed reconnected.

The authenticated production console distinguishes current authorization from
historical unapproved requests. Correcting that display leaves incomplete
acceptance, execution status and acknowledged delivery unchanged. Returned read
calls say returned, not waiting, and explicitly do not imply that a business
outcome was verified. The focused 34-test suite and TypeScript/production build
passed.

The actual browser was used to open the lifecycle dialog, inspect the real
execution arguments, and select raw host evidence. The evidence endpoint returned
the full receipt and its hash. The open evidence selector survived live updates.
The dialog, selected evidence and hash remained present across the service
switch; authorization changed through the reconnected live feed without closing
the dialog. Reloading the frontend then verified the new revision labels and
returned-call wording. The desktop dialog and raw evidence were visually checked
without text overlapping the controls. No frontend errors were recorded.
Earlier isolated desktop/mobile checks remain distinct from this production
browser check. No old report was resent.

The lifecycle-console checklist item is now accepted. Fresh protected-directory
measurements, real QQ process-loss boundaries, and disposable cleanup still are
not accepted. Further live tests need explicit approval for their exact paths,
data limits and destination, and must interrupt only an isolated test process.
Internal process identifiers, deployment paths and message receipts are omitted
from this public acceptance entry. This documentation-only update does not
require another service restart.

## Isolated Transport Harness and Bounded Cleanup

An opt-in [live file acceptance harness](live-file-outbox-acceptance.md) now
exercises five durable upload boundaries using owned child processes and a
dedicated test database. Its guard and process suite passed 14 tests against a
local PostgreSQL database and a simulated NapCat WebUI. Four simulated uploads
were observed, with no repeated recovery uploads. Missing pre-send receipts stay
unknown rather than being relabeled delivered. This is local verification, not
successful real QQ transport acceptance.

The user approved bounded live inspection, disposable test data and at most four
small test attachments. Real WebUI authentication and account validation passed,
but the QQ group-file-list action timed out before uploads. Supplying the action's
explicit default file count did not resolve the timeout. No real test attachments
were uploaded and no live crash boundary was exercised. The transport gate stays
open. Its preflight failures now persist a phase and safe error type, without
credentials or upstream response contents.

A separate operator-run disposable cleanup verified the exact generated payload,
deleted only that file and its empty test directory, and observed a matching
17 MiB increase in available space. The first fixture had a one-byte pattern-size
error and correctly refused deletion; its exact generated bytes were checked
before bounded cleanup. Bot, database and unrelated service identities remained
unchanged. This was explicitly authorized operator acceptance, not a completed
Bot-controller authorization/cleanup workflow, so that broader gate stays open.

Privileged read-only inspection returned filesystem, Docker and journal usage.
The full protected-directory traversal reached its deadline; it is not a valid
complete directory total. No existing images, volumes, databases or user files
were removed. All internal receipts, operational identifiers and private raw
reports remain outside the public repository. The harness is opt-in and does
not require a production restart merely to publish it.

## Offline QQ Is Not a Connected Transport

Follow-up live read-only inspection established the missing distinction:
OneBot reported `online=false`, and the separate WebUI login status reported
`isLogin=false`. Cached account and group metadata still returned successfully.
Those responses and a connected WebSocket do not prove the QQ account is online.
The real upload gate is blocked on user login, not accepted and not a reason to
replay previously delivered reports.

File delivery now checks account availability before preparing or claiming a
durable upload. Offline, malformed or unavailable status leaves the manifest
queued with a persisted reason and retry time, without spending upload or
artifact-preparation retry budgets. Reopening storage retains that state.
Current-revision, cancellation, job-ownership and concurrent-claim fences still
apply. A direct tool call reports not sent, not a fabricated queued delivery.
Already ambiguous uploads remain ambiguous while offline; reconciliation waits
for availability rather than repeatedly timing out or blindly sending again.
Old unambiguous receipts are not rewritten. The live acceptance harness now
checks online status before its group-file preflight.

The focused suite passed 93 tests, including a real local PostgreSQL offline-wait
reopen test, simulated QQ uploads and actual SIGKILL of owned test children.
The live production crash-boundary and Bot-controller cleanup gates remain open.
Restoring QQ login requires the user's own scan and confirmation.
