{
  description = "Reproducible package and NixOS module for the QQ DeepSeek bot";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.nixpkgs.follows = "nixpkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
    };
  };

  outputs = inputs @ {
    self,
    nixpkgs,
    pyproject-nix,
    uv2nix,
    pyproject-build-systems,
    ...
  }: let
    inherit (nixpkgs) lib;
    supportedSystems = [
      "x86_64-linux"
      "aarch64-linux"
      "aarch64-darwin"
      "x86_64-darwin"
    ];
    forAllSystems = lib.genAttrs supportedSystems;
    project = builtins.fromTOML (builtins.readFile ./pyproject.toml);
    workspace = uv2nix.lib.workspace.loadWorkspace {workspaceRoot = ./.;};
    workspaceOverlay = workspace.mkPyprojectOverlay {
      sourcePreference = "wheel";
    };
    applicationSource = lib.fileset.toSource {
      root = ./.;
      fileset = lib.fileset.unions [
        ./bot.py
        ./alembic.ini
        ./pyproject.toml
        ./requirements.txt
        ./uv.lock
        ./README.md
        ./THIRD_PARTY_NOTICES.md
        (lib.fileset.fileFilter (file: file.hasExt "md" || file.hasExt "html") ./docs)
        (lib.fileset.fileFilter (file: file.hasExt "md") ./skills)
        (lib.fileset.fileFilter (
            file:
              file.hasExt "py"
              || file.hasExt "mako"
              || file.hasExt "md"
          )
          ./migrations)
        (lib.fileset.fileFilter (file: file.hasExt "py") ./tests)
        (lib.fileset.fileFilter (file: file.hasExt "js") ./tools)
        (lib.fileset.fileFilter (
            file:
              file.hasExt "py"
              || file.hasExt "swift"
              || file.hasExt "png"
              || file.hasExt "svg"
          )
          ./src)
      ];
    };

    mkPythonSet = system: let
      pkgs = nixpkgs.legacyPackages.${system};
      python = pkgs.python312;
      baseSet = pkgs.callPackage pyproject-nix.build.packages {inherit python;};
    in
      baseSet.overrideScope (
        lib.composeManyExtensions [
          pyproject-build-systems.overlays.wheel
          workspaceOverlay
        ]
      );

    mkPackage = system: let
      pkgs = nixpkgs.legacyPackages.${system};
      pythonSet = mkPythonSet system;
      virtualenv = pythonSet.mkVirtualEnv "qq-deepseek-bot-env" workspace.deps.default;
      adminUi = pkgs.buildNpmPackage {
        pname = "kennethbot-admin-ui";
        version = project.project.version;
        src = ./admin-ui;
        npmDepsHash = "sha256-OZcHLAk3j0OqfY7HCq/Lsxc4EyUAeHw8RLkDVgPtW7o=";
        npmBuildScript = "build";
        installPhase = ''
          runHook preInstall
          mkdir -p "$out/dist"
          cp -R dist/. "$out/dist/"
          runHook postInstall
        '';
      };
    in
      pkgs.stdenvNoCC.mkDerivation {
        pname = project.project.name;
        version = project.project.version;
        src = applicationSource;
        dontBuild = true;
        nativeBuildInputs = [pkgs.makeWrapper];

        installPhase = ''
          runHook preInstall

          mkdir -p "$out/bin" "$out/share/qq-deepseek-bot"
          cp -R . "$out/share/qq-deepseek-bot"
          mkdir -p "$out/share/qq-deepseek-bot/src/plugins/ai_chat/admin_ui_dist"
          cp -R ${adminUi}/dist/. \
            "$out/share/qq-deepseek-bot/src/plugins/ai_chat/admin_ui_dist/"
          makeWrapper ${virtualenv}/bin/python "$out/bin/qq-deepseek-bot" \
            --add-flags "$out/share/qq-deepseek-bot/bot.py" \
            --chdir "$out/share/qq-deepseek-bot" \
            --set PYTHONDONTWRITEBYTECODE 1 \
            --set PYTHONUNBUFFERED 1 \
            --run 'state_home="''${XDG_STATE_HOME:-''${HOME:-/tmp}/.local/state}"' \
            --run 'cache_home="''${XDG_CACHE_HOME:-''${HOME:-/tmp}/.cache}"' \
            --run 'export AI_STATE_DIR="''${AI_STATE_DIR:-$state_home/qq-deepseek-bot}"' \
            --run 'export AI_CACHE_DIR="''${AI_CACHE_DIR:-$cache_home/qq-deepseek-bot}"' \
            --run '${pkgs.coreutils}/bin/mkdir -p "$AI_STATE_DIR" "$AI_CACHE_DIR"'
          makeWrapper ${virtualenv}/bin/python "$out/bin/qq-deepseek-bot-db" \
            --add-flags "-m src.bot_storage.cli" \
            --chdir "$out/share/qq-deepseek-bot" \
            --set PYTHONDONTWRITEBYTECODE 1 \
            --set PYTHONUNBUFFERED 1

          runHook postInstall
        '';

        passthru = {inherit virtualenv;};
        meta = {
          description = project.project.description;
          homepage = "https://github.com/zty20040403/chat-bot";
          mainProgram = "qq-deepseek-bot";
          platforms = supportedSystems;
        };
      };
    mkSandboxImage = system: let
      pkgs = nixpkgs.legacyPackages.${system};
    in
      import ./nix/sandbox-image.nix {
        inherit pkgs lib;
        version = project.project.version;
      };
  in {
    packages = forAllSystems (system: let
      pkgs = nixpkgs.legacyPackages.${system};
    in
      {
        default = mkPackage system;
        qq-deepseek-bot = mkPackage system;
      }
      // lib.optionalAttrs pkgs.stdenv.isLinux {
        sandbox-image = mkSandboxImage system;
      });

    apps = forAllSystems (system: {
      default = {
        type = "app";
        program = lib.getExe self.packages.${system}.default;
      };
    });

    checks = forAllSystems (system: let
      pkgs = nixpkgs.legacyPackages.${system};
      package = self.packages.${system}.default;
      virtualenv = package.passthru.virtualenv;
    in {
      inherit package;
      imports = pkgs.runCommand "qq-deepseek-bot-import-check" {} ''
        cd ${package}/share/qq-deepseek-bot
        ${virtualenv}/bin/python -c 'import alembic, edge_tts, httpx, miniaudio, nonebot, openai, opentelemetry.sdk, playwright, prometheus_client, psycopg, psycopg_pool, pygments, pysilk, sqlalchemy'
        ${virtualenv}/bin/python -c 'import ast, pathlib; [ast.parse(path.read_text(encoding="utf-8"), filename=str(path)) for path in pathlib.Path("src").rglob("*.py")]'
        touch "$out"
      '';
    });

    devShells = forAllSystems (system: let
      pkgs = nixpkgs.legacyPackages.${system};
      pythonSet = mkPythonSet system;
      virtualenv = pythonSet.mkVirtualEnv "qq-deepseek-bot-dev-env" workspace.deps.default;
    in {
      default = pkgs.mkShell {
        packages = [virtualenv pkgs.uv pkgs.nodejs_22];
        env = {
          UV_NO_SYNC = "1";
          UV_PYTHON = pythonSet.python.interpreter;
          UV_PYTHON_DOWNLOADS = "never";
        };
      };
    });

    nixosModules = {
      default = import ./nix/module.nix {inherit self;};
      qq-deepseek-bot = self.nixosModules.default;
    };
  };
}
