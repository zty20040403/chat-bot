{self}: {
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.services.qq-deepseek-bot;
  serviceName = "qq-deepseek-bot";
  statePath = "/var/lib/${cfg.stateDirectory}";
  cachePath = "/var/cache/${cfg.cacheDirectory}";
  defaultPackage = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
  boolString = value:
    if value
    then "true"
    else "false";
  napcatServiceName = "docker-${cfg.napcat.containerName}";
in {
  options.services.qq-deepseek-bot = {
    enable = lib.mkEnableOption "the DeepSeek-powered QQ bot";

    package = lib.mkOption {
      type = lib.types.package;
      default = defaultPackage;
      defaultText = lib.literalExpression "inputs.qq-bot.packages.${pkgs.stdenv.hostPlatform.system}.default";
      description = "Packaged bot application to run.";
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "/run/secrets/qq-deepseek-bot.env";
      description = ''
        Runtime environment file containing API keys and bot settings. Keep this
        file outside the Nix store; do not pass a Nix path containing secrets.
      '';
    };

    environment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = {};
      example = {AI_ENABLED_GROUPS = "123456789";};
      description = "Non-secret environment variables for the bot service.";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = serviceName;
      description = "User account that runs the bot.";
    };

    group = lib.mkOption {
      type = lib.types.str;
      default = serviceName;
      description = "Primary group of the bot process.";
    };

    stateDirectory = lib.mkOption {
      type = lib.types.str;
      default = serviceName;
      description = "Name of the systemd-managed directory under /var/lib.";
    };

    cacheDirectory = lib.mkOption {
      type = lib.types.str;
      default = serviceName;
      description = "Name of the systemd-managed directory under /var/cache.";
    };

    host = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Address used by the OneBot reverse WebSocket server.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8080;
      description = "Port used by the OneBot reverse WebSocket server.";
    };

    openFirewall = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Open the configured TCP port in the NixOS firewall.";
    };

    runtimePackages = lib.mkOption {
      type = lib.types.listOf lib.types.package;
      default = [];
      description = "Additional executables exposed to bot tools through PATH.";
    };

    sandbox.enable = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Allow the bot to create Docker-backed execution sandboxes.";
    };

    browser = {
      enable = lib.mkEnableOption "the bot's persistent Playwright browser and rich rendering";

      package = lib.mkOption {
        type = lib.types.package;
        default = pkgs.chromium;
        defaultText = lib.literalExpression "pkgs.chromium";
        description = "Chromium-compatible browser executable used by Playwright.";
      };
    };

    napcat = {
      enable = lib.mkEnableOption "a dedicated NapCat container for this bot";

      account = lib.mkOption {
        type = lib.types.str;
        example = "123456789";
        description = "QQ account logged in by the dedicated NapCat container.";
      };

      image = lib.mkOption {
        type = lib.types.str;
        default = "mlikiowa/napcat-docker:latest";
        description = "NapCat container image; pin a version or digest in production.";
      };

      containerName = lib.mkOption {
        type = lib.types.str;
        default = "napcat-chat-bot";
        description = "OCI container name for the dedicated NapCat instance.";
      };

      dataDirectory = lib.mkOption {
        type = lib.types.str;
        default = "/var/lib/napcat-chat-bot";
        description = "Persistent NapCat data directory on the host.";
      };

      environmentFiles = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [];
        description = "Runtime environment files passed to the NapCat container.";
      };

      environment = lib.mkOption {
        type = lib.types.attrsOf lib.types.str;
        default = {};
        description = "Additional non-secret environment variables for NapCat.";
      };

      reverseWebsocketUrl = lib.mkOption {
        type = lib.types.str;
        default = "ws://host.docker.internal:${toString cfg.port}/onebot/v11/ws";
        description = "OneBot reverse WebSocket URL used by NapCat.";
      };

      webuiAddress = lib.mkOption {
        type = lib.types.str;
        default = "127.0.0.1";
        description = "Host address used for the NapCat WebUI port mapping.";
      };

      webuiPort = lib.mkOption {
        type = lib.types.port;
        default = 6100;
        description = "Host port used for the dedicated NapCat WebUI.";
      };
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = builtins.match "^[A-Za-z0-9_.-]+$" cfg.stateDirectory != null;
        message = "services.qq-deepseek-bot.stateDirectory must be a directory name, not a path";
      }
      {
        assertion = builtins.match "^[A-Za-z0-9_.-]+$" cfg.cacheDirectory != null;
        message = "services.qq-deepseek-bot.cacheDirectory must be a directory name, not a path";
      }
    ];

    users.groups.${serviceName} = lib.mkIf (cfg.group == serviceName) {};
    users.users.${serviceName} = lib.mkIf (cfg.user == serviceName) {
      isSystemUser = true;
      group = cfg.group;
      home = statePath;
    };

    networking.firewall.allowedTCPPorts = lib.optionals cfg.openFirewall [cfg.port];

    virtualisation.docker.enable = lib.mkDefault (cfg.sandbox.enable || cfg.napcat.enable);

    systemd.services.${serviceName} = {
      description = "DeepSeek QQ bot";
      wantedBy = ["multi-user.target"];
      wants = ["network-online.target"];
      after = ["network-online.target"];
      path =
        cfg.runtimePackages
        ++ lib.optionals cfg.sandbox.enable [pkgs.docker]
        ++ lib.optionals cfg.browser.enable [cfg.browser.package];
      environment =
        {
          HOME = statePath;
          AI_STATE_DIR = "${statePath}/state";
          AI_CACHE_DIR = cachePath;
          AI_SANDBOX_ENABLED = boolString cfg.sandbox.enable;
          HOST = cfg.host;
          PORT = toString cfg.port;
          PYTHONUNBUFFERED = "1";
        }
        // lib.optionalAttrs cfg.browser.enable {
          AI_BROWSER_ENABLED = "true";
          AI_BROWSER_EXECUTABLE_PATH = lib.getExe cfg.browser.package;
        }
        // cfg.environment;

      serviceConfig =
        {
          Type = "simple";
          User = cfg.user;
          Group = cfg.group;
          SupplementaryGroups = lib.optional cfg.sandbox.enable "docker";
          StateDirectory = cfg.stateDirectory;
          CacheDirectory = cfg.cacheDirectory;
          WorkingDirectory = "${cfg.package}/share/qq-deepseek-bot";
          ExecStart = lib.getExe cfg.package;
          Restart = "on-failure";
          RestartSec = 5;
          UMask = "0077";

          NoNewPrivileges = true;
          PrivateTmp = true;
          ProtectControlGroups = true;
          ProtectHome = "read-only";
          ProtectKernelModules = true;
          ProtectKernelTunables = true;
          ProtectSystem = "strict";
          RestrictSUIDSGID = true;
        }
        // lib.optionalAttrs (cfg.environmentFile != null) {
          EnvironmentFile = cfg.environmentFile;
        };
    };

    virtualisation.oci-containers = lib.mkIf cfg.napcat.enable {
      backend = "docker";
      containers.${cfg.napcat.containerName} = {
        image = cfg.napcat.image;
        autoStart = true;
        environment =
          {
            ACCOUNT = cfg.napcat.account;
            WSR_ENABLE = "true";
            WS_URLS = builtins.toJSON [cfg.napcat.reverseWebsocketUrl];
          }
          // cfg.napcat.environment;
        environmentFiles = cfg.napcat.environmentFiles;
        ports = [
          "${cfg.napcat.webuiAddress}:${toString cfg.napcat.webuiPort}:6099"
        ];
        volumes = [
          "${cfg.napcat.dataDirectory}/QQ:/app/.config/QQ"
          "${cfg.napcat.dataDirectory}/config:/app/napcat/config"
          "${cfg.napcat.dataDirectory}/outbox:/data/outbox"
        ];
        extraOptions = ["--add-host=host.docker.internal:host-gateway"];
      };
    };

    systemd.services.${napcatServiceName} = lib.mkIf cfg.napcat.enable {
      wants = ["${serviceName}.service"];
      after = ["${serviceName}.service"];
    };

    systemd.tmpfiles.rules = lib.optionals cfg.napcat.enable [
      "d ${cfg.napcat.dataDirectory} 0700 root root -"
      "d ${cfg.napcat.dataDirectory}/QQ 0700 root root -"
      "d ${cfg.napcat.dataDirectory}/config 0700 root root -"
      "d ${cfg.napcat.dataDirectory}/outbox 0700 root root -"
    ];
  };
}
