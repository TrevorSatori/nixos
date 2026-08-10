{ pkgs, config, ... }:

{
  services.hermes-agent = {
    enable = true;
    
    # Exposes 'hermes' command in shell for CLI access
    addToSystemPackages = true; 

    settings = {
      model = {
        provider = "openai";
        default = "gpt-4o";
      };
      
      # 'local' runs shell commands on host system.
      # Change to 'docker' if you want isolated sandboxing.
      terminal.backend = "local"; 

      messaging = {
        matrix = {
          enable = true;
          require_mention = true;
        };
      };
    };

    # Point to your dedicated secrets directory
    environmentFiles = [ "/var/src/secrets/hermes-env" ];
  };

  systemd.services.hermes-agent.serviceConfig = {
    ProtectSystem = "strict";
    ProtectHome = true;
    PrivateTmp = true;
  };
}
