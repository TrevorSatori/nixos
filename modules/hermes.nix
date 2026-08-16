{ pkgs, config, lib, ... }:

let
  # Build an isolated Python environment with the CalDAV MCP dependencies
  caldavMcpServer = pkgs.python3.pkgs.buildPythonApplication {
    pname = "caldav-mcp";
    version = "unstable";
    src = pkgs.fetchFromGitHub {
      owner = "madbonez";
      repo = "caldav-mcp";
      rev = "main";
      hash = "sha256-Vq/Va9GSho3zFMBLiiA7iccv6ywS1OXZ93HWeQH676g=";
    };
    format = "pyproject";
    nativeBuildInputs = with pkgs.python3.pkgs; [ setuptools hatchling ];
    propagatedBuildInputs = with pkgs.python3.pkgs; [
      caldav
      fastmcp
      mcp
      pydantic
      icalendar
      python-dotenv
    ];
    doCheck = false;
  };

  # Wrapper script to enforce unbuffered stdio and prevent FastMCP hangs
  wrappedCaldavMcp = pkgs.writeShellScriptBin "mcp-caldav-runner" ''
    export PYTHONUNBUFFERED=1
    export FASTMCP_LOG_LEVEL=ERROR
    exec ${caldavMcpServer}/bin/mcp-caldav "$@"
  '';
in
{
  # Isolated system user
  users.users.hermes = {
    isSystemUser = true;
    group = "hermes";
    home = "/var/lib/hermes";
    createHome = true;
    description = "Hermes Agent Daemon User";
  };
  users.groups.hermes = {};

  services.hermes-agent = {
    enable = true;
    container.enable = false;
    addToSystemPackages = true;

    settings = {
      model = {
        provider = "openai-api";
        default = "gpt-4o";
      };

      logging = {
        level = "DEBUG";
      };

      agent = {
        reasoning_effort = false;
      };

      terminal.backend = "local";

      messaging = {
        matrix = {
          enable = true;
          require_mention = false;
        };
      };

      mcp_servers = {
        radicale_calendar = {
          command = "${wrappedCaldavMcp}/bin/mcp-caldav-runner";
          args = [ ];
        };
      };
    };

    environmentFiles = [ "/var/src/secrets/hermes-env" ];
  };

  systemd.services.hermes-agent.path = with pkgs; [ 
    python3
    nodejs 
    bash 
    cacert 
  ];

  # Systemd Isolation & Hard Cgroup Bounds (using mkForce to override upstream defaults)
  systemd.services.hermes-agent = {
    after = [ "network-online.target" "radicale.service" "matrix-tuwunel.service" ];
    wants = [ "network-online.target" ];

    serviceConfig = {
      User = lib.mkForce "hermes";
      Group = lib.mkForce "hermes";
      WorkingDirectory = lib.mkForce "/var/lib/hermes";
      StateDirectory = lib.mkForce "hermes";

      # Resource Guardrails
      CPUQuota = lib.mkForce "80%";
      MemoryMax = lib.mkForce "2G";
      MemoryHigh = lib.mkForce "1.5G";

      # Sandboxing
      ProtectHome = lib.mkForce true;
      ProtectSystem = lib.mkForce "strict";
      PrivateTmp = lib.mkForce true;
      ProtectKernelTunables = lib.mkForce true;
      ProtectControlGroups = lib.mkForce true;
      NoNewPrivileges = lib.mkForce true;

      ReadWritePaths = lib.mkForce [ 
        "/var/lib/hermes"
        "/etc/nixos"
      ];

      StandardOutput = lib.mkForce "journal";
      StandardError = lib.mkForce "journal";

      Restart = lib.mkForce "on-failure";
      RestartSec = lib.mkForce "10s";
    };
  };

  # State and secret permissions
  systemd.tmpfiles.rules = [
    "d /var/lib/hermes 0750 hermes hermes -"
    "z /var/src/secrets/hermes-env 0600 hermes hermes -"
  ];
}
