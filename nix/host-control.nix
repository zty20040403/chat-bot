{self}: {config, lib, pkgs, ...}: let
  cfg = config.services.gaoji-host-control;
  settings = (pkgs.formats.json {}).generate "gaoji-host-control.json" {
    host_id = cfg.hostId;
    shell = "${pkgs.bash}/bin/bash";
    systemctl = "${pkgs.systemd}/bin/systemctl";
    path = "/run/current-system/sw/bin:/run/wrappers/bin";
    default_cwd = "/";
    boot_id_file = "/proc/sys/kernel/random/boot_id";
    receipt_directory = "/var/lib/gaoji-host-control";
    job_state_root = "/var/lib/maxops-jobs";
    cgroup_file = "/proc/self/cgroup";
  };
  entry = pkgs.writeShellScriptBin "gaoji-host-control" ''
    if [ "$#" -ne 1 ]; then
      echo 'Expected one JSON request argument' >&2
      exit 64
    fi
    exec ${pkgs.python3}/bin/python3 -I ${cfg.package}/libexec/host_control.py \
      --request-json "$1" --config ${settings}
  '';
in {
  options.services.gaoji-host-control = {
    enable = lib.mkEnableOption "target-side checks for authorized gaoji host operations";
    hostId = lib.mkOption {
      type = lib.types.strMatching "[A-Za-z0-9][A-Za-z0-9_-]{0,63}";
      default = config.networking.hostName;
      description = "Exact host identity shared with the Ops catalog.";
    };
    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.host-control;
    };
  };
  config = lib.mkIf cfg.enable {
    environment.systemPackages = [entry];
    systemd.tmpfiles.rules = ["d /var/lib/gaoji-host-control 0700 root root -"];
  };
}
