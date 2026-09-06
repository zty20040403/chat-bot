{self}: {
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.services.kennethbot-cluster-worker;
  defaultPackage = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
in {
  options.services.kennethbot-cluster-worker = {
    enable = lib.mkEnableOption "an isolated Kennethbot compute and preview worker";
    package = lib.mkOption {
      type = lib.types.package;
      default = defaultPackage;
      defaultText = lib.literalExpression "inputs.qq-bot.packages.${pkgs.stdenv.hostPlatform.system}.default";
    };
    workerId = lib.mkOption {type = lib.types.str;};
    controlUrl = lib.mkOption {type = lib.types.str;};
    tokenFile = lib.mkOption {type = lib.types.str;};
    listenAddress = lib.mkOption {type = lib.types.str; default = "127.0.0.1";};
    port = lib.mkOption {type = lib.types.port; default = 8092;};
    publicBaseUrl = lib.mkOption {type = lib.types.str;};
    cpuMillis = lib.mkOption {type = lib.types.ints.positive; default = 2000;};
    memoryBytes = lib.mkOption {type = lib.types.ints.positive; default = 2147483648;};
    gpuSlots = lib.mkOption {type = lib.types.ints.unsigned; default = 0;};
    concurrency = lib.mkOption {type = lib.types.ints.between 1 16; default = 2;};
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = builtins.match "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$" cfg.workerId != null;
        message = "Kennethbot workerId is invalid";
      }
      {
        assertion = lib.hasPrefix "http://" cfg.controlUrl || lib.hasPrefix "https://" cfg.controlUrl;
        message = "Kennethbot worker controlUrl must be HTTP(S)";
      }
      {
        assertion = lib.hasPrefix "http://" cfg.publicBaseUrl || lib.hasPrefix "https://" cfg.publicBaseUrl;
        message = "Kennethbot worker publicBaseUrl must be HTTP(S)";
      }
    ];
    systemd.services.kennethbot-cluster-worker = {
      description = "Kennethbot isolated cluster worker";
      wantedBy = ["multi-user.target"];
      wants = ["network-online.target" "kennethbot-cluster-control.service"];
      after = ["network-online.target" "kennethbot-cluster-control.service"];
      path = [pkgs.poppler_utils pkgs.ffmpeg-headless];
      environment = {
        KW_WORKER_ID = cfg.workerId;
        KW_TOKEN_FILE = "%d/worker-token";
        KW_CONTROL_URL = cfg.controlUrl;
        KW_LISTEN_HOST = cfg.listenAddress;
        KW_LISTEN_PORT = toString cfg.port;
        KW_PUBLIC_BASE_URL = cfg.publicBaseUrl;
        KW_STATE_DIR = "/var/lib/kennethbot-cluster-worker";
        KW_CPU_MILLIS = toString cfg.cpuMillis;
        KW_MEMORY_BYTES = toString cfg.memoryBytes;
        KW_GPU_SLOTS = toString cfg.gpuSlots;
        KW_CONCURRENCY = toString cfg.concurrency;
        PYTHONUNBUFFERED = "1";
      };
      serviceConfig = {
        Type = "simple";
        DynamicUser = true;
        StateDirectory = "kennethbot-cluster-worker";
        WorkingDirectory = "${cfg.package}/share/qq-deepseek-bot";
        LoadCredential = "worker-token:${cfg.tokenFile}";
        ExecStart = "${cfg.package}/bin/kennethbot-cluster-worker";
        Restart = "on-failure";
        RestartSec = 5;
        UMask = "0077";
        CPUQuota = "${toString (builtins.div cfg.cpuMillis 10)}%";
        MemoryMax = cfg.memoryBytes;
        MemorySwapMax = 0;
        TasksMax = 256;
        LockPersonality = true;
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
      };
    };
  };
}
