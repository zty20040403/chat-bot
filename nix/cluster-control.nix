{self}: {
  config,
  lib,
  pkgs,
  ...
}: let
  cfg = config.services.kennethbot-cluster-control;
  defaultPackage = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
  inventoryHostIds = map (host: host.host_id) cfg.inventory;
  inventoryHostType = lib.types.submodule {
    options = {
      host_id = lib.mkOption {
        type = lib.types.str;
        description = "Stable host identifier shared with the Nix registry and MaxOps.";
      };
      label = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "Human-readable host label.";
      };
      architecture = lib.mkOption {
        type = lib.types.str;
        default = "unknown";
        description = "Declared system architecture; this is inventory, not a live observation.";
      };
      site = lib.mkOption {
        type = lib.types.str;
        default = "unknown";
        description = "Declared host site or failure domain.";
      };
      maintainer = lib.mkOption {
        type = lib.types.str;
        default = "unknown";
        description = "Maintainer responsible for approving access to this host.";
      };
      permission_source = lib.mkOption {
        type = lib.types.str;
        default = "unconfirmed";
        description = "Configuration or owner decision that granted this host scope.";
      };
      roles = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [];
        description = "Declared roles used for display and later scheduling decisions.";
      };
      observe = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Allow Kennethbot to request read-only observations for this host.";
      };
      operate = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Declare future host-operation eligibility; it grants no P1 write capability.";
      };
      compute = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Declare future isolated-worker eligibility; it grants no P1 execution capability.";
      };
      readable_units = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [];
        description = "Exact systemd services Kennethbot may query on this host.";
      };
    };
  };
in {
  options.services.kennethbot-cluster-control = {
    enable = lib.mkEnableOption "Kennethbot's read-only cluster control service";

    package = lib.mkOption {
      type = lib.types.package;
      default = defaultPackage;
      defaultText = lib.literalExpression "inputs.qq-bot.packages.${pkgs.stdenv.hostPlatform.system}.default";
      description = "Kennethbot package containing the cluster-control executable.";
    };

    listenAddress = lib.mkOption {
      type = lib.types.str;
      default = "127.0.0.1";
      description = "Address for the internal control API; keep loopback unless a firewall and caller identity are configured.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 8091;
      description = "Port for the internal control API.";
    };

    openFirewall = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Open the control API port. This should remain false for a colocated bot.";
    };

    environmentFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Environment file containing the PostgreSQL DSN and pool settings.";
    };

    apiTokenFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Credential shared only with the bot-side internal API client.";
    };

    maxops = {
      enable = lib.mkEnableOption "the MaxOps read-only adapter";

      baseUrl = lib.mkOption {
        type = lib.types.str;
        default = "";
        example = "http://100.64.0.3:9721";
        description = "Fixed MaxOps hub URL; it cannot be supplied by a model tool call.";
      };

      tokenFile = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Dedicated MaxOps credential for Kennethbot.";
      };

      timeoutSeconds = lib.mkOption {
        type = lib.types.ints.between 1 30;
        default = 15;
        description = "Total budget for catalog validation and one MaxOps query.";
      };
    };

    cacheSeconds = lib.mkOption {
      type = lib.types.ints.between 1 300;
      default = 20;
      description = "Maximum age of a non-sensitive read-only query projection.";
    };

    inventory = lib.mkOption {
      type = lib.types.listOf inventoryHostType;
      default = [];
      example = [{host_id = "h610"; roles = ["control"]; observe = true;}];
      description = "Explicit host scope. Presence alone does not grant observation or operation.";
    };

    environment = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = {};
      description = "Additional non-secret environment variables.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.apiTokenFile != null;
        message = "services.kennethbot-cluster-control.apiTokenFile is required";
      }
      {
        assertion = cfg.environmentFile != null;
        message = "services.kennethbot-cluster-control.environmentFile is required for PostgreSQL";
      }
      {
        assertion = !cfg.maxops.enable || (cfg.maxops.baseUrl != "" && cfg.maxops.tokenFile != null);
        message = "MaxOps baseUrl and tokenFile are required when the adapter is enabled";
      }
      {
        assertion = cfg.listenAddress == "127.0.0.1" || cfg.listenAddress == "::1" || cfg.openFirewall;
        message = "A non-loopback cluster-control listener requires an explicit firewall decision";
      }
      {
        assertion = builtins.length inventoryHostIds == builtins.length (lib.unique inventoryHostIds);
        message = "Kennethbot cluster inventory contains duplicate host_id values";
      }
      {
        assertion = lib.all (host: builtins.match "^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$" host.host_id != null) cfg.inventory;
        message = "Kennethbot cluster inventory contains an invalid host_id";
      }
      {
        assertion = lib.all (host: lib.all (unit: builtins.match "^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,119}\\.service$" unit != null) host.readable_units) cfg.inventory;
        message = "Kennethbot cluster inventory contains an invalid readable systemd unit";
      }
    ];

    networking.firewall.allowedTCPPorts = lib.optionals cfg.openFirewall [cfg.port];

    systemd.services.kennethbot-cluster-control = {
      description = "Kennethbot read-only cluster control service";
      wantedBy = ["multi-user.target"];
      wants = ["network-online.target"];
      after = ["network-online.target"];
      environment = {
        KC_HOST = cfg.listenAddress;
        KC_PORT = toString cfg.port;
        KC_API_TOKEN_FILE = "%d/api-token";
        KC_MAXOPS_ENABLED = if cfg.maxops.enable then "true" else "false";
        KC_MAXOPS_BASE_URL = cfg.maxops.baseUrl;
        KC_MAXOPS_TOKEN_FILE = if cfg.maxops.enable then "%d/maxops-token" else "";
        KC_MAXOPS_TIMEOUT_SECONDS = toString cfg.maxops.timeoutSeconds;
        KC_CACHE_SECONDS = toString cfg.cacheSeconds;
        KC_INVENTORY_JSON = builtins.toJSON cfg.inventory;
        PYTHONUNBUFFERED = "1";
      } // cfg.environment;
      serviceConfig =
        {
          Type = "simple";
          DynamicUser = true;
          StateDirectory = "kennethbot-cluster-control";
          WorkingDirectory = "${cfg.package}/share/qq-deepseek-bot";
          LoadCredential =
            lib.optional (cfg.apiTokenFile != null) "api-token:${cfg.apiTokenFile}"
            ++ lib.optional (cfg.maxops.enable && cfg.maxops.tokenFile != null) "maxops-token:${cfg.maxops.tokenFile}";
          # The bot starts after this service, so the control plane owns the
          # idempotent schema upgrade and avoids a startup dependency cycle.
          ExecStartPre = "${cfg.package}/bin/qq-deepseek-bot-db upgrade";
          ExecStart = "${cfg.package}/bin/kennethbot-cluster-control";
          Restart = "on-failure";
          RestartSec = 5;
          UMask = "0077";

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
        }
        // lib.optionalAttrs (cfg.environmentFile != null) {
          EnvironmentFile = cfg.environmentFile;
        };
    };
  };
}
