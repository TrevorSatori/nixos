{ pkgs, config, lib, ... }:

{
  services.hermes-agent = {
    enable = true;
    
    container.enable = true;

    # Exposes 'hermes' command in shell for CLI access
    addToSystemPackages = true; 

    settings = {
      model = {
        provider = "openai-api";
        default = "gpt-4o";
      };

      agent = {
        reasoning_effort = false;
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
    ProtectSystem = lib.mkForce "full";
    ProtectHome = lib.mkForce false;
    PrivateTmp = lib.mkForce true;
  };
}
