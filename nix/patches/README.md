# Ops Exact-source Compatibility

`ops-exact-source.patch` targets the public MaxOps revision
`dae83357884ef343a2b2e4f971cb90cce7dbf455`. It is an opt-in source patch, not
a separate management backend. No tokens, inventory or Bot identities are added
to upstream. Its original MIT license and attribution remain unchanged.

The patch:

- lets a new clean workspace deploy its existing base commit without creating
  a different Git commit;
- keeps dirty or inconsistent workspaces ineligible in both hub and executor;
- adds optional `workspace.create.source_commit`, requiring an expected remote
  head, a full object ID, a real commit object and ancestry from the checked head;
- retains the original remote-head, immutable tree/revision, runtime/profile and
  activation checks; it does not force push or use a human checkout;
- adds protocol, actual-Git and Hub HTTP regression tests.

Use `../ops-compat-package.nix` with the pinned upstream package. Assign the same
result through native `services.maxops-hub.package` and
`services.maxops-executor.package` options on the participating machines.
The wrapper retains upstream Cargo/Nextest checks and adds Git to check inputs.
It does not enable services or change credentials. Do not apply it twice, combine
it with an already-fixed upstream, or silently accept patch failure after a pin
change. Review the upstream fix and remove this patch when upgrading.

Local verification used Rust 1.95.0 with the upstream lock unchanged. The two
protocol tests, actual-Git ancestry test and four Hub deployment tests passed.
The development `devenv` command was unavailable, so the installed Nix Rust
toolchain was used. A Linux Nix build using the upstream nixpkgs pin also passed
all 74 Nextest tests (zero skipped) on h610. Its output was
`/nix/store/5kfkmwz15galdx8gxijccm4cj1vahzlb-maxops-0.3.0`.
The consuming repository follows its shared nixpkgs pin, so its final derivation
must still be evaluated and built. No system switch or live deployment acceptance
is implied by these build results.
