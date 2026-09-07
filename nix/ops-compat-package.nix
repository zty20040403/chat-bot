# Opt-in until upstream supports exact clean-workspace deployment sources.
{pkgs, package}:
package.overrideAttrs (old: {
  patches = (old.patches or []) ++ [./patches/ops-exact-source.patch];
  nativeCheckInputs = (old.nativeCheckInputs or []) ++ [pkgs.git];
  passthru = (old.passthru or {}) // {gaojiExactSource = true;};
})
