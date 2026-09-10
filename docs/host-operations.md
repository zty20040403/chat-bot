# Verified Host Operations

## Scope

Services use the existing Ops `units.start/stop/restart/reload` contract. A model
selects a host and service, not a shell path. Host reboot is a separate fixed
action. Free commands remain available but require target-side preflight.

No new SSH credentials or public privileged HTTP endpoint are introduced. The
host helper is an ordinary, non-setuid program invoked through the existing
authorized Ops executor profile. It cannot grant a caller additional privilege.

## Invariants

- Preparation, approval, submission and verification are distinct states.
- Existing task authorization covers its steps. The Bot validates the task grant
  before approving an operation; control validates its scope, version, approval
  expiry and cancellation again at the durable submission checkpoint, including
  after a pre-dispatch crash and takeover. Revocation cannot undo an operation
  that has already been dispatched. Expiry before dispatch is reported as not
  executed, not as an uncertain outcome.
- The target's Nix configuration supplies its identity, shell and reboot program.
- Preflight does not execute the requested command. Scripts get a syntax check,
  not a claim that their business effects are safe or correct.
- Explicit missing paths fail with suggestions; no basename fallback executes a
  different program. Argument arrays never become interpolated shell source.
- Execution rechecks the input, executable bytes, working directory, effective
  identity/environment and boot ID against the saved preflight evidence.
- The executor's accepted job handle is not proof of successful execution.
- Host-side receipts prevent replay inside the original executor job. Control's
  durable checkpoint and the upstream idempotency key prevent a second dispatch.
  A crash after dispatch but before receipt remains unknown, never an automatic retry.
- Reboot completion requires a fresh observation of the same host with a changed
  boot ID. An unavailable observation is not proof of reboot or failure.
- State and external submission identities survive control/Bot restarts. No
  automatic second reboot, rollback reboot or retry under a new identity.

## Implementation Checklist

- [x] Target helper and Nix module: preflight, exact execution, fixed reboot, receipts.
- [x] Persistent control workflow and scoped authorization integration.
- [x] Dedicated service/reboot tools and protected free-command routing.
- [x] Console/API evidence and actionable user-facing results.
- [x] Unit tests, PostgreSQL takeover/concurrency tests and local fake-target tests.
- [x] Helper Nix package build and standalone module evaluation for all three hosts.
- [ ] Full Linux host configuration evaluation and executor-profile smoke checks.
- [ ] Pin the verified Bot commit and deploy; verify the installed helper on each host.

Tests must cover missing paths, hostile arguments, absent working directories,
syntax errors, changed binaries/environment/boot IDs, duplicate submissions,
lost receipts, cancellation, expiry, scope mismatch and restart recovery. Shared
hosts must not be rebooted as part of acceptance testing; use an isolated VM or
a fake reboot executable for effectful tests.

## Deployment Order

1. Publish the verified Bot revision and update the `qq-bot` input in nix-config.
2. Install the helper module on the managed targets (h310, h610 and tank).
3. Enable the matching `hostControlHelpers` map on cluster-control. An unconfigured
   or absent helper fails closed; it never falls back to unchecked execution.
4. Run harmless argv/preflight checks in the actual diagnostic and operator
   profiles, then inspect the same operation's evidence in the console.
5. Reboot acceptance on a shared host requires separate maintenance authorization.

Do not switch the edited nix-config while its lock still points at the old Bot
revision: that revision does not export the new host-control module.

## Limits

Preflight checks program availability, the selected path and target bytes,
working directory, execution identity/environment and (for scripts) shell syntax.
It does not prove that arbitrary shell-script commands, dynamic libraries or the
requested business outcome are correct. Preserve symlink invocation paths for
multicall tools and Python virtual environments. Nix store executables are
preferred; checking a mutable executable cannot eliminate every check/exec race.

The two target checks run in the same executor job, so PrivateTmp and credentials
are consistent. Diagnostic receipts use that job's existing systemd StateDirectory,
derived from its cgroup because the upstream intentionally clears STATE_DIRECTORY.
No privilege is added by the helper. Target receipts are not a replacement for the
PostgreSQL operation ledger or upstream job records.

Command success means the executor reported success and supplied valid target
evidence, not that every intended application-level effect was achieved. Reboot
verification specifically requires a fresh, matching-host observation of a changed
boot ID; an offline host or unchanged ID remains unverified. A checkpoint recorded
just before a crash can leave an operation uncertain even when nothing executed.
This deliberately sacrifices automatic retry rather than risk repeating a reboot.

## Verification Record (2026-09-10)

- Full Python suite: 842 tests passed, none skipped, including real PostgreSQL
  integration, authorization, concurrency, fencing and interrupted-job recovery.
  Each DB fixture uses an isolated schema in a disposable local PostgreSQL 17
  instance. Install `vector` in `public` before migrations so test schemas share
  the extension without depending on one another's lifetime. CI now does this.
- The real helper ran locally against a fake reboot program, including lost
  submission receipt recovery and missing-path error propagation to control.
  No actual host reboot was performed.
- TypeScript/Vite build and synthetic Playwright checks passed at 1440px and
  390px. The open detail view updates from waiting-for-reboot to verified over
  SSE; long paths and parameters stay within its bounds.
- The standalone helper Nix package built on aarch64-darwin. Module evaluation
  passed for the h310, h610 and tank identities. Evaluation of the actual
  nix-config options also confirmed each helper's identity and the three-target
  controller map. These are not full Linux configuration or live executor-profile
  acceptance tests.
- Read-only target checks confirmed all three Ops agents active and the standard
  systemctl path and boot-ID interface present. An existing h310 operator job
  confirmed the expected per-job StateDirectory convention. This did not execute
  the new helper in that profile or retry the existing failed job.
- Full h610 evaluation on the Mac requires a Linux `cabal2nix` derivation from
  another service. Copying the source snapshots to h610 for Linux verification
  was blocked by approval review and awaits explicit permission. Nothing was
  switched, installed on the targets, or rebooted during this verification.
