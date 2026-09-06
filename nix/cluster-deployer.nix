{self}: {
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.services.kennethbot-cluster-deployer;
  defaultPackage = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
  targetType = lib.types.submodule {
    options = {
      hostId = lib.mkOption {type = lib.types.str;};
      flakeHost = lib.mkOption {type = lib.types.str;};
      sshTarget = lib.mkOption {type = lib.types.str;};
      verificationUnits = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [];
      };
      useRemoteSudo = lib.mkOption {
        type = lib.types.bool;
        default = false;
      };
      resourceVersion = lib.mkOption {
        type = lib.types.ints.positive;
        default = 1;
      };
    };
  };
  targetIds = map (target: target.hostId) cfg.targets;
in {
  options.services.kennethbot-cluster-deployer = {
    enable = lib.mkEnableOption "Kennethbot's fixed-contract Nix deployment executor";
    package = lib.mkOption {
      type = lib.types.package;
      default = defaultPackage;
      defaultText = lib.literalExpression "inputs.qq-bot.packages.${pkgs.stdenv.hostPlatform.system}.default";
    };
    deployerId = lib.mkOption {type = lib.types.str;};
    executorHostId = lib.mkOption {
      type = lib.types.str;
      default = config.networking.hostName;
      description = "Inventory host ID running this deployer service.";
    };
    allowSelfDeployment = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Permit this deployer to switch its own host. Unsafe by default.";
    };
    controlUrl = lib.mkOption {type = lib.types.str;};
    tokenFile = lib.mkOption {type = lib.types.str;};
    repositoryId = lib.mkOption {type = lib.types.str;};
    repositoryUrl = lib.mkOption {type = lib.types.str;};
    defaultBranch = lib.mkOption {type = lib.types.str; default = "main";};
    targets = lib.mkOption {
      type = lib.types.listOf targetType;
      default = [];
    };
    sshIdentityFile = lib.mkOption {type = lib.types.str;};
    sshKnownHostsFile = lib.mkOption {type = lib.types.str;};
    pollSeconds = lib.mkOption {
      type = lib.types.ints.between 1 60;
      default = 5;
    };
    commandTimeoutSeconds = lib.mkOption {
      type = lib.types.ints.between 60 7200;
      default = 1800;
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = builtins.match "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$" cfg.deployerId != null;
        message = "Kennethbot deployerId is invalid";
      }
      {
        assertion = builtins.match "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$" cfg.executorHostId != null;
        message = "Kennethbot executorHostId is invalid";
      }
      {
        assertion = builtins.match "^[a-z][a-z0-9_-]{0,63}$" cfg.repositoryId != null;
        message = "Kennethbot deployment repositoryId is invalid";
      }
      {
        assertion = lib.hasPrefix "http://" cfg.controlUrl || lib.hasPrefix "https://" cfg.controlUrl;
        message = "Kennethbot deployer controlUrl must use HTTP(S)";
      }
      {
        assertion = lib.hasPrefix "https://" cfg.repositoryUrl || lib.hasPrefix "ssh://" cfg.repositoryUrl || lib.hasPrefix "git@" cfg.repositoryUrl;
        message = "Kennethbot deployer repositoryUrl must use HTTPS or SSH";
      }
      {
        assertion = cfg.targets != [] && builtins.length targetIds == builtins.length (lib.unique targetIds);
        message = "Kennethbot deployer requires unique deployment targets";
      }
      {
        assertion = cfg.allowSelfDeployment || !(lib.elem cfg.executorHostId targetIds);
        message = "Kennethbot deployer cannot target its own host unless allowSelfDeployment is explicitly enabled";
      }
      {
        assertion = lib.all (target: builtins.match "^([A-Za-z0-9._-]+@)?[A-Za-z0-9][A-Za-z0-9.-]{0,199}$" target.sshTarget != null) cfg.targets;
        message = "Kennethbot deployer target sshTarget is invalid";
      }
      {
        assertion = lib.all (target: lib.all (unit: builtins.match "^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,119}\\.service$" unit != null) target.verificationUnits) cfg.targets;
        message = "Kennethbot deployer verification unit is invalid";
      }
    ];

    systemd.services.kennethbot-cluster-deployer = {
      description = "Kennethbot fixed-contract Nix deployer";
      wantedBy = ["multi-user.target"];
      wants = ["network-online.target" "kennethbot-cluster-control.service"];
      after = ["network-online.target" "kennethbot-cluster-control.service"];
      path = [pkgs.git pkgs.nix pkgs.openssh pkgs.coreutils pkgs.systemd];
      environment = {
        KD_DEPLOYER_ID = cfg.deployerId;
        KD_EXECUTOR_HOST_ID = cfg.executorHostId;
        KD_ALLOW_SELF_DEPLOYMENT = lib.boolToString cfg.allowSelfDeployment;
        KD_TOKEN_FILE = "%d/deployer-token";
        KD_CONTROL_URL = cfg.controlUrl;
        KD_STATE_DIR = "/var/lib/kennethbot-cluster-deployer";
        KD_REPOSITORY_ID = cfg.repositoryId;
        KD_REPOSITORY_URL = cfg.repositoryUrl;
        KD_DEFAULT_BRANCH = cfg.defaultBranch;
        KD_TARGETS_JSON = builtins.toJSON (map (target: {
          host_id = target.hostId;
          flake_host = target.flakeHost;
          ssh_target = target.sshTarget;
          verification_units = target.verificationUnits;
          use_remote_sudo = target.useRemoteSudo;
          resource_version = target.resourceVersion;
        }) cfg.targets);
        KD_SSH_IDENTITY_FILE = "%d/ssh-identity";
        KD_SSH_KNOWN_HOSTS_FILE = "%d/ssh-known-hosts";
        KD_POLL_SECONDS = toString cfg.pollSeconds;
        KD_COMMAND_TIMEOUT_SECONDS = toString cfg.commandTimeoutSeconds;
        PYTHONUNBUFFERED = "1";
      };
      serviceConfig = {
        Type = "simple";
        DynamicUser = true;
        StateDirectory = "kennethbot-cluster-deployer";
        WorkingDirectory = "${cfg.package}/share/qq-deepseek-bot";
        LoadCredential = [
          "deployer-token:${cfg.tokenFile}"
          "ssh-identity:${cfg.sshIdentityFile}"
          "ssh-known-hosts:${cfg.sshKnownHostsFile}"
        ];
        ExecStart = "${cfg.package}/bin/kennethbot-cluster-deployer";
        Restart = "on-failure";
        RestartSec = 5;
        UMask = "0077";
        NoNewPrivileges = true;
        PrivateDevices = true;
        PrivateTmp = true;
        ProtectClock = true;
        ProtectControlGroups = true;
        ProtectHome = true;
        ProtectKernelLogs = true;
        ProtectKernelModules = true;
        ProtectKernelTunables = true;
        ProtectSystem = "strict";
        RestrictAddressFamilies = ["AF_INET" "AF_INET6" "AF_UNIX"];
        RestrictSUIDSGID = true;
        LockPersonality = true;
        TasksMax = 512;
      };
    };
  };
}
