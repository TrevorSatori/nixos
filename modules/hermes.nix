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
      # If the sha256 fails on first build, replace with lib.fakeSha256 or the hash provided by nix
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
in
{
  services.hermes-agent = {
    enable = true;
    container.enable = false;
    addToSystemPackages = true;

    settings = {
      model = {
        provider = "openai-api";
        default = "gpt-4o";
      };

      agent = {
        reasoning_effort = false;
      };

      terminal.backend = "local";

      messaging = {
        matrix = {
          enable = true;
          require_mention = true;
        };
      };

      mcp_servers = {
        radicale_calendar = {
          command = "${caldavMcpServer}/bin/mcp-caldav";
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

  systemd.services.hermes-agent.serviceConfig = {
    ProtectSystem = lib.mkForce false;
    ProtectHome = lib.mkForce false;
    PrivateTmp = lib.mkForce false;
    ReadWritePaths = [ 
      "/etc/nixos"
      "/tmp"
    ];
  };

  users.users.hermes.extraGroups = [ "wheel" ];
  security.sudo.wheelNeedsPassword = false;
}
