{ pkgs, lib, config, ... }:

let
  # Common Systemd Sandboxing + VPN Binding
  vpnServiceConfig = {
    NoNewPrivileges = lib.mkForce true;
    ProtectSystem = lib.mkForce "strict";
    ProtectHome = lib.mkForce true;
    ProtectKernelTunables = lib.mkForce true;
    ProtectControlGroups = lib.mkForce true;
    CapabilityBoundingSet = lib.mkForce [ "CAP_CHOWN" "CAP_SETUID" "CAP_SETGID" "CAP_FOWNER" ];
    
    # Network Namespace Injection
    NetworkNamespacePath = "/run/netns/mullvad";
    BindReadOnlyPaths = [ "/etc/netns/mullvad/resolv.conf:/etc/resolv.conf" ];
  };

  # Helper function to generate unit configs cleanly
  mkVpnService = extraPaths: {
    requires = [ "vpn-namespace.service" ];
    after = [ "vpn-namespace.service" ];
    serviceConfig = vpnServiceConfig // {
      ReadWritePaths = extraPaths;
    };
  };
in
{
  # 1. qBittorrent
  services.qbittorrent = { enable = true; user = "media"; group = "media"; };
  systemd.services.qbittorrent = {
    requires = [ "vpn-namespace.service" ];
    after = [ "vpn-namespace.service" ];
    serviceConfig = vpnServiceConfig // {
      StateDirectory = "qBittorrent";
      ReadWritePaths = [ "/var/lib/qBittorrent" "/data/downloads" "/data/media" ];
    };
  };

  # 2. Sonarr
  services.sonarr = { enable = true; user = "media"; group = "media"; };
  systemd.services.sonarr = {
    requires = [ "vpn-namespace.service" ];
    after = [ "vpn-namespace.service" ];
    serviceConfig = vpnServiceConfig // {
      StateDirectory = "sonarr";
      ReadWritePaths = [ "/var/lib/sonarr" "/data/downloads" "/data/media" ];
    };
  };

  # 3. Radarr
  services.radarr = { enable = true; user = "media"; group = "media"; };
  systemd.services.radarr = {
    requires = [ "vpn-namespace.service" ];
    after = [ "vpn-namespace.service" ];
    serviceConfig = vpnServiceConfig // {
      StateDirectory = "radarr";
      ReadWritePaths = [ "/var/lib/radarr" "/data/downloads" "/data/media" ];
    };
  };

  # 4. Lidarr
  services.lidarr = { enable = true; user = "media"; group = "media"; };
  systemd.services.lidarr = {
    requires = [ "vpn-namespace.service" ];
    after = [ "vpn-namespace.service" ];
    serviceConfig = vpnServiceConfig // {
      StateDirectory = "lidarr";
      WorkingDirectory = "/var/lib/lidarr";
      ReadWritePaths = [ "/var/lib/lidarr" "/tmp" "/data/downloads" "/data/media" ];
    };
  };

  # 5. Prowlarr
  services.prowlarr = { enable = true; };
  systemd.services.prowlarr = {
    requires = [ "vpn-namespace.service" ];
    after = [ "vpn-namespace.service" ];
    serviceConfig = vpnServiceConfig // {
      StateDirectory = "prowlarr";
      ReadWritePaths = [ "/var/lib/prowlarr" ];
    };
  };
  
  # 6. FlareSolverr (Configured for Chromium Systemd Sandboxing)
  systemd.services.flaresolverr = {
    description = "FlareSolverr proxy service";
    wantedBy = [ "multi-user.target" ];
    requires = [ "vpn-namespace.service" ];
    after = [ "vpn-namespace.service" ];
    path = with pkgs; [ chromium ];
    environment = {
      PORT = "8191";
      LOG_LEVEL = "info";
      HEADLESS = "true";
      HOME = "/var/lib/flaresolverr";
      # Tells Chromium to bypass internal user namespace sandboxing inside the systemd container
      EXTRA_FLAGS = "--no-sandbox --disable-dev-shm-usage --disable-gpu";
    };
    serviceConfig = vpnServiceConfig // {
      ExecStart = "${pkgs.flaresolverr}/bin/flaresolverr";
      User = "media";
      Group = "media";
      StateDirectory = "flaresolverr";
      WorkingDirectory = "/var/lib/flaresolverr";
      # Mount writable shared memory for Chromium
      TemporaryFileSystem = "/dev/shm:rw,mode=1777,size=2g";
      ReadWritePaths = [ "/var/lib/flaresolverr" "/tmp" ];
      Restart = "always";
    };
  };
}
