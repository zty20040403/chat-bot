{ config, lib, pkgs, ... }:
let
  cfg = config.services.kennethbot-qwen-control;
in {
  options.services.kennethbot-qwen-control = {
    enable = lib.mkEnableOption "restricted Kennethbot WSL Qwen controller";
    listenAddress = lib.mkOption { type = lib.types.str; default = "127.0.0.1"; };
    port = lib.mkOption { type = lib.types.port; default = 8001; };
    allowedPeers = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [];
      description = "Explicit source IP allowlist, normally the h610 Tailscale IP.";
    };
    tokenFile = lib.mkOption {
      type = lib.types.str;
      description = "Root-owned token file outside the Nix store; at least 32 random characters.";
    };
    nvidiaSmiPath = lib.mkOption {
      type = lib.types.str;
      default = "/usr/lib/wsl/lib/nvidia-smi";
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [
      { assertion = cfg.allowedPeers != []; message = "Qwen control requires an explicit peer allowlist."; }
      { assertion = !(lib.hasPrefix "/nix/store/" cfg.tokenFile); message = "Qwen control token must not be stored in the Nix store."; }
    ];
    users.groups.qwen-control = {};
    users.users.qwen-control = { isSystemUser = true; group = "qwen-control"; };
    security.polkit.enable = true;
    security.polkit.extraConfig = ''
      polkit.addRule(function(action, subject) {
        if (subject.user === "qwen-control" &&
            action.id === "org.freedesktop.systemd1.manage-units" &&
            action.lookup("unit") === "podman-qwen38.service" &&
            (action.lookup("verb") === "start" || action.lookup("verb") === "stop")) {
          return polkit.Result.YES;
        }
      });
    '';
    systemd.services.kennethbot-qwen-control = {
      description = "Restricted Qwen lifecycle API (never starts Qwen on boot)";
      wantedBy = [ "multi-user.target" ];
      after = [ "network-online.target" "tailscaled.service" ];
      wants = [ "network-online.target" ];
      environment.LD_LIBRARY_PATH = "/usr/lib/wsl/lib";
      serviceConfig = {
        User = "qwen-control";
        Group = "qwen-control";
        ExecStart = lib.concatStringsSep " " ([
          "${pkgs.python3}/bin/python" "${../tools/qwen_control.py}"
          "--host" (lib.escapeShellArg cfg.listenAddress)
          "--port" (toString cfg.port)
          "--token-file" "%d/token"
          "--database" "/var/lib/kennethbot-qwen-control/requests.sqlite3"
          "--systemctl" "${pkgs.systemd}/bin/systemctl"
          "--nvidia-smi" (lib.escapeShellArg cfg.nvidiaSmiPath)
        ] ++ lib.concatMap (peer: [ "--allow-peer" (lib.escapeShellArg peer) ]) cfg.allowedPeers);
        LoadCredential = [ "token:${cfg.tokenFile}" ];
        StateDirectory = "kennethbot-qwen-control";
        StateDirectoryMode = "0700";
        Restart = "on-failure";
        RestartSec = 5;
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictSUIDSGID = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
        CapabilityBoundingSet = "";
        UMask = "0077";
      };
    };
  };
}
