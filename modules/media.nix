{ pkgs, lib, config, ... }:

let
  hardenedConfig = {
    NoNewPrivileges = true;
    ProtectSystem = lib.mkForce "strict";
    ProtectHome = true;
    ProtectKernelTunables = true;
  };
in
{
  # ---------------------------------------------------------------------------
  # Jellyfin (Media Streaming)
  # ---------------------------------------------------------------------------
  services.jellyfin = {
    enable = true;
    user = "media";
    group = "media";
  };
  # Grant Jellyfin user access to GPU nodes for QuickSync / VAAPI
  users.users.media.extraGroups = [ "video" "render" ];

  systemd.services.jellyfin.serviceConfig = hardenedConfig // {
    ReadWritePaths = [ "/var/lib/jellyfin" "/var/cache/jellyfin" "/data/media" ];
    DeviceAllow = [ "/dev/dri/renderD128 rwm" "/dev/dri/card0 rwm" ];
  };
  # ---------------------------------------------------------------------------
  # Immich (Photos & Video Backup)
  # ---------------------------------------------------------------------------
  services.immich = {
    enable = true;
    port = 2283;
    host = "0.0.0.0";
    mediaLocation = "/data/media/immich";

    accelerationDevices = [ "/dev/dri/renderD128" ];
  };

  users.users.immich.extraGroups = [ "video" "render" "media" ];

  systemd.services.immich-server.serviceConfig = {
    NoNewPrivileges = true;
    ProtectSystem = "strict";
    ProtectHome = true;
    ReadWritePaths = [
      "/var/lib/immich"
      "/data/media/immich"
      "/data/media/photos"
    ];
  };

  systemd.tmpfiles.rules = [
    "d /data/media 0775 media media -"
    "d /data/media/immich 0775 immich media -"
    "d /var/lib/immich 0775 immich media -"
  ];
  # ---------------------------------------------------------------------------
  # Audiobookshelf (Audiobooks & Podcasts)
  # ---------------------------------------------------------------------------
  services.audiobookshelf = {
    enable = true;
    user = "media";
    group = "media";
    host = "0.0.0.0";
    port = 13378;
  };
  systemd.services.audiobookshelf.serviceConfig = hardenedConfig // {
    ReadWritePaths = [ "/var/lib/audiobookshelf" "/data/media/audiobooks" ];
  };

  # ---------------------------------------------------------------------------
  # Komga (Comics & Manga)
  # ---------------------------------------------------------------------------

  # Komga Configuration
  services.komga = {
    enable = true;
    user = "media";
    group = "media";
    settings = {
      server = {
        port = 25600;
        address = "0.0.0.0";
      };
    };
  };

  # Extend systemd service config WITHOUT nuking ExecStart
  systemd.services.komga.serviceConfig = {
    ReadWritePaths = [ 
      "/var/lib/komga" 
      "/data/media/comics" 
      "/data/media/manga" 
    ];
  };

  # ---------------------------------------------------------------------------
  # Calibre Server (E-books)
  # ---------------------------------------------------------------------------
  services.calibre-server = {
    enable = true;
    user = "media";
    group = "media";
    port = 8082;
    libraries = [ "/data/media/books" ];
  };
}
