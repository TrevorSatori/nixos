{ pkgs, ... }:

{

  services.freshrss = {
    enable = true;
    baseUrl = "http://lo-pan:1080";
    defaultUser = "satori";
    passwordFile = "/var/src/secrets/freshrss-pass";
    authType = "form";
    database.type = "sqlite";
    virtualHost = "lo-pan";
  };

  # 2. PHP-FPM Pool Configuration
  services.phpfpm.pools.freshrss = {
    phpOptions = ''
      date.timezone = "America/Chicago"
      session.save_path = "/var/lib/freshrss/data"
      upload_tmp_dir = "/var/lib/freshrss/data"
    '';
  };

  # Un-harden PHP-FPM so it can write config files in /var/lib/freshrss during the installer
  systemd.services.phpfpm-freshrss.serviceConfig = {
    ReadWritePaths = [ "/var/lib/freshrss" ];
    ProtectSystem = pkgs.lib.mkForce false;
  };

  # Declaratively ensure permissions and directories exist
  systemd.tmpfiles.rules = [
    # Ensure parent secrets directory allows user traversal without exposing contents
    "d /var/src/secrets 0711 root root -"
    
    # Secret file permissions (freshrss user can read it)
    "z /var/src/secrets/freshrss-pass 0600 freshrss freshrss -"

    # State directories
    "d /var/lib/freshrss 0775 freshrss freshrss -"
    "d /var/lib/freshrss/cache 0775 freshrss freshrss -"
    "d /var/lib/freshrss/users 0775 freshrss freshrss -"
    "d /var/lib/freshrss/favicons 0775 freshrss freshrss -"
    "d /var/lib/freshrss/data 0775 freshrss freshrss -"
    "d /var/lib/freshrss/tokens 0775 freshrss freshrss -"
  ];
  # Grant Nginx group membership for freshrss
  users.users.nginx.extraGroups = [ "freshrss" ];

  # Separate Nginx block to handle Port 1080
  services.nginx = {
    enable = true;
    virtualHosts."lo-pan" = {
      listen = [
        {
          addr = "0.0.0.0";
          port = 1080;
        }
      ];
    };
  };

  # Syncthing
  services.syncthing = {
    enable = true;
    user = "satori";
    dataDir = "/var/lib/syncthing";
    overrideDevices = false;
    overrideFolders = false;
    guiAddress = "0.0.0.0:8384";
  };

  services.uptime-kuma = {
    enable = true;
    settings = {
      HOST = "0.0.0.0";
      PORT = "3001";
    };
  };
}  
