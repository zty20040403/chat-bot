{self}: let
  hosts = ["h310" "h610" "tank"];
  evaluate = profile: self.inputs.nixpkgs.lib.nixosSystem {
    system = "x86_64-linux";
    modules = [
      (import ../../nix/cluster-control.nix {inherit self;})
      ({pkgs, ...}: {
        boot.isContainer = true;
        system.stateVersion = "26.05";
        services.gaoji-cluster-control = {
          enable = true;
          localHostId = "h610";
          package = pkgs.hello;
          apiTokenFile = "/run/test-api-token";
          environmentFile = "/run/test-database-env";
          inventory = map (host: {host_id = host; observe = true; operate = true;}) hosts;
          ops = {
            enable = true;
            baseUrl = "http://ops.invalid";
            tokenFile = "/run/test-read-token";
            management = {
              enable = true;
              tokenFile = "/run/test-management-token";
              inherit hosts;
              actors = ["admin:kenneth"];
            };
          };
          deployments.repositories = [{
            repositoryId = "nix-config";
            backend = "ops";
            url = "https://example.invalid/config.git";
            allowedChanges = ["gaoji"];
            targets = map (host: {
              hostId = host;
              flakeHost = host;
              opsRepository = "nix-config-${host}";
              opsProfile = profile host;
            }) hosts;
          }];
        };
      })
    ];
  };
  valid = (evaluate (host: "${host}-system")).config;
  invalid = (evaluate (_: "")).config;
in {
  assertionsPass = builtins.all (item: item.assertion) valid.assertions;
  failedAssertions = map (item: item.message) (builtins.filter (item: !item.assertion) valid.assertions);
  missingProfileRejected = builtins.any (item: !item.assertion) invalid.assertions;
  repositories = builtins.fromJSON valid.systemd.services.gaoji-cluster-control.environment.KC_DEPLOYMENT_REPOSITORIES_JSON;
}
