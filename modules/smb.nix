{ config, pkgs, ... }:

{
  # 1. Create a dedicated system user for SMB access
  users.users.smb-media = {
    isSystemUser = true;
    group = "smb-media";
    description = "Samba Media Service User";
  };
  users.groups.smb-media = {};

  # 2. Configure Samba
  services.samba = {
    enable = true;
    openFirewall = true; # Opens SMB ports (139 & 445) in the NixOS firewall

    settings = {
      global = {
        "workgroup" = "WORKGROUP";
        "server string" = "lo-pan media server";
        "netbios name" = "lo-pan";
        "security" = "user";

        # Disable guest/anonymous logins entirely
        "map to guest" = "never";

        # Network Restrictions: Allow 192.168.1.x and local loopback only
        "hosts allow" = "192.168.1. 127.0.0.1 localhost";
        "hosts deny" = "0.0.0.0/0";
      };

      # The Read-Only Share
      "Media" = {
        "path" = "/data/media";
        "browseable" = "yes";
        "read only" = "yes";           # Strictly blocks edits, uploads, and deletes
        "guest ok" = "no";             # Requires login
        "valid users" = "smb-media";   # Locked to the smb-media account
      };
    };
  };

  # 3. Enable WS-Discovery (so Windows Explorer sees lo-pan automatically)
  services.samba-wsdd = {
    enable = true;
    openFirewall = true;
  };

  # 4. Enable Avahi/mDNS (so macOS Finder sees lo-pan automatically)
  services.avahi = {
    enable = true;
    nssmdns4 = true;
    openFirewall = true;
  };
}
