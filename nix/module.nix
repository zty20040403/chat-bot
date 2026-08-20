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
  codesnapFonts = pkgs.runCommand "qq-bot-codesnap-fonts" {} ''
    mkdir -p "$out/share/fonts"
    cp ${pkgs.sarasa-gothic}/share/fonts/truetype/Sarasa-Regular.ttc \
      "$out/share/fonts/Sarasa-Regular.ttc"
  '';
  defaultWhisperModel = pkgs.fetchurl {
    url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin";
    hash = "sha256-YO1bw90U7qhWST0zQ0m0BXgt3K8AKNS130CINF+6Lv4=";
  };
  codesnapConfig = pkgs.writeText "qq-bot-codesnap.json" (builtins.toJSON {
    print_eggs = false;
    snapshot_config = {
      theme = cfg.codesnap.theme;
      fonts_folders = ["${codesnapFonts}/share/fonts"];
      window = {
        mac_window_bar = true;
        shadow = {
          radius = 16;
          color = "#00000040";
        };
        margin = {
          x = 42;
          y = 42;
        };
        border = {
          width = 1;
          color = "#ffffff24";
        };
        title_config = {
          color = "#d8dee9";
          font_family = cfg.codesnap.fontFamily;
        };
        radius = 8;
      };
      code_config = {
        font_family = cfg.codesnap.fontFamily;
        breadcrumbs = {
          enable = false;
          separator = "/";
          color = "#80848b";
          font_family = cfg.codesnap.fontFamily;
        };
      };
      watermark = {
        content = "";
        font_family = cfg.codesnap.fontFamily;
        color = "#ffffff";
      };
      background = "#15171c";
    };
  });
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

    database.migrateOnStart = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Run Alembic before starting the bot. When disabled, startup only checks
        that PostgreSQL is already at the revision required by this package.
      '';
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

    codesnap = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = "Render fenced code blocks with the CodeSnap CLI.";
      };

      package = lib.mkOption {
        type = lib.types.package;
        default = pkgs.codesnap;
        defaultText = lib.literalExpression "pkgs.codesnap";
        description = "CodeSnap CLI package used for code block images.";
      };

      fontFamily = lib.mkOption {
        type = lib.types.str;
        default = "Sarasa Mono SC";
        description = "Chinese-capable monospace family used for code snapshots.";
      };

      theme = lib.mkOption {
        type = lib.types.str;
        default = "candy";
        description = "Built-in CodeSnap syntax theme.";
      };
    };

    videoDeep = {
      enable = lib.mkEnableOption "temporary deep analysis of shared Bilibili videos";

      whisperPackage = lib.mkOption {
        type = lib.types.package;
        default = pkgs.whisper-cpp;
        defaultText = lib.literalExpression "pkgs.whisper-cpp";
        description = "whisper.cpp package used for local audio transcription.";
      };

      whisperModel = lib.mkOption {
        type = lib.types.package;
        default = defaultWhisperModel;
        description = "Fixed-output multilingual Whisper model file.";
      };

      frameCount = lib.mkOption {
        type = lib.types.ints.between 4 12;
        default = 8;
        description = "Number of evenly sampled video frames sent to the vision model.";
      };

      maxDownloadMB = lib.mkOption {
        type = lib.types.ints.positive;
        default = 500;
        description = "Maximum combined temporary video and audio download size.";
      };

      maxDurationMinutes = lib.mkOption {
        type = lib.types.ints.positive;
        default = 30;
        description = "Maximum video duration accepted by deep analysis.";
      };

      timeoutSeconds = lib.mkOption {
        type = lib.types.ints.positive;
        default = 600;
        description = "Timeout for media preparation and local transcription.";
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
        ++ lib.optionals cfg.browser.enable [cfg.browser.package]
        ++ lib.optionals cfg.codesnap.enable [cfg.codesnap.package]
        ++ lib.optionals cfg.videoDeep.enable [
          pkgs.ffmpeg-headless
          cfg.videoDeep.whisperPackage
        ];
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
        // lib.optionalAttrs cfg.codesnap.enable {
          AI_CODESNAP_ENABLED = "true";
          AI_CODESNAP_EXECUTABLE_PATH = lib.getExe cfg.codesnap.package;
          AI_CODESNAP_CONFIG_PATH = codesnapConfig;
          AI_CODESNAP_FONT_FAMILY = cfg.codesnap.fontFamily;
          AI_CODESNAP_THEME = cfg.codesnap.theme;
        }
        // lib.optionalAttrs (!cfg.codesnap.enable) {
          AI_CODESNAP_ENABLED = "false";
        }
        // lib.optionalAttrs cfg.videoDeep.enable {
          AI_VIDEO_DEEP_ENABLED = "true";
          AI_VIDEO_WHISPER_MODEL_PATH = toString cfg.videoDeep.whisperModel;
          AI_VIDEO_FRAME_COUNT = toString cfg.videoDeep.frameCount;
          AI_VIDEO_MAX_DOWNLOAD_MB = toString cfg.videoDeep.maxDownloadMB;
          AI_VIDEO_MAX_DURATION_MINUTES = toString cfg.videoDeep.maxDurationMinutes;
          AI_VIDEO_TIMEOUT_SECONDS = toString cfg.videoDeep.timeoutSeconds;
        }
        // lib.optionalAttrs (!cfg.videoDeep.enable) {
          AI_VIDEO_DEEP_ENABLED = "false";
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
          ExecStartPre = "${cfg.package}/bin/qq-deepseek-bot-db ${
            if cfg.database.migrateOnStart
            then "upgrade"
            else "check"
          }";
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
