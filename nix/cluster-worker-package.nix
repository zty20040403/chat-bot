{pkgs, lib}: let
  python = pkgs.python312.withPackages (ps: [ps.fastapi ps.httpx ps.uvicorn]);
  source = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../src/__init__.py
      ../src/cluster_control/__init__.py
      ../src/cluster_control/job_kinds.py
      (lib.fileset.fileFilter (file: file.hasExt "py") ../src/cluster_worker)
    ];
  };
in pkgs.stdenvNoCC.mkDerivation {
  pname = "gaoji-cluster-worker";
  version = "2";
  src = source;
  dontBuild = true;
  nativeBuildInputs = [pkgs.makeWrapper];
  installPhase = ''
    runHook preInstall
    mkdir -p "$out/bin" "$out/share/gaoji"
    cp -R src "$out/share/gaoji/"
    makeWrapper ${python}/bin/python "$out/bin/gaoji-cluster-worker" \
      --add-flags "-m src.cluster_worker" \
      --chdir "$out/share/gaoji" \
      --set PYTHONDONTWRITEBYTECODE 1 \
      --set PYTHONUNBUFFERED 1
    runHook postInstall
  '';
  doInstallCheck = true;
  installCheckPhase = ''
    cd "$out/share/gaoji"
    ${python}/bin/python -c 'from src.cluster_worker.api import create_app; from src.cluster_worker.service import CAPABILITIES; assert len(CAPABILITIES) == 5'
  '';
  meta = {
    description = "Isolated gaoji worker without the Bot, models or admin frontend";
    license = lib.licenses.mit;
    mainProgram = "gaoji-cluster-worker";
  };
}
