{pkgs, lib}: pkgs.stdenvNoCC.mkDerivation {
  pname = "gaoji-host-control";
  version = "1";
  src = ../src/host_control.py;
  dontUnpack = true;
  installPhase = ''
    mkdir -p $out/libexec
    cp $src $out/libexec/host_control.py
  '';
  meta = {
    description = "Target-side command preflight and durable execution receipts";
    license = lib.licenses.mit;
    platforms = lib.platforms.unix;
  };
}
