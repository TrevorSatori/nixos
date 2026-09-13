{ pkgs, lib, config, ... }:

let
  hardenedConfig = {
    NoNewPrivileges = true;
    ProtectSystem = lib.mkForce "strict";
    ProtectHome = true;
    ProtectKernelTunables = true;
  };

  silverbulletPort = 3005;
  spacePath = "/data/media/silverbullet";
in
{
  # ---------------------------------------------------------------------------
  # SilverBullet Web Service
  # ---------------------------------------------------------------------------
  services.silverbullet = {
    enable = true;
    user = "media";
    group = "media";
    listenPort = silverbulletPort;
    listenAddress = "0.0.0.0";
    spaceDir = spacePath;
  };

  systemd.services.silverbullet.serviceConfig = hardenedConfig // {
    ReadWritePaths = [ spacePath ];
    EnvironmentFile = "/var/src/secrets/silverbullet.env";
  };

  # ---------------------------------------------------------------------------
  # ABS -> SilverBullet Poller Service
  # ---------------------------------------------------------------------------
  systemd.services.abs-to-silverbullet = {
    description = "Audiobookshelf to SilverBullet Poller";
    after = [ "network.target" "audiobookshelf.service" ];
    wantedBy = [ "multi-user.target" ];

    environment = {
      SILVERBULLET_SPACE = spacePath;
      ABS_URL = "http://127.0.0.1:13378";
      POLL_INTERVAL = "300";
    };

    serviceConfig = hardenedConfig // {
      ExecStart = "${pkgs.python3}/bin/python3 ${../scripts/silverbullet/abs.py}";
      Restart = "always";
      RestartSec = 5;
      User = "media";
      Group = "media";
      ReadWritePaths = [ spacePath ];
      EnvironmentFile = "/var/src/secrets/abs_poller.env";
    };
  };


  # ---------------------------------------------------------------------------
  # Komga -> SilverBullet Poller Service
  # ---------------------------------------------------------------------------
  systemd.services.komga-to-silverbullet = {
    description = "Komga to SilverBullet Poller";
    after = [ "network.target" "komga.service" ];
    wantedBy = [ "multi-user.target" ];

    environment = {
      SILVERBULLET_SPACE = spacePath;
      KOMGA_URL = "http://127.0.0.1:25600";
      POLL_INTERVAL = "300";
    };

    serviceConfig = hardenedConfig // {
      ExecStart = "${pkgs.python3}/bin/python3 ${../scripts/silverbullet/komga.py}";
      Restart = "always";
      RestartSec = 5;
      User = "media";
      Group = "media";
      ReadWritePaths = [ spacePath ];
      EnvironmentFile = "/var/src/secrets/komga_poller.env";
    };
  };

  # Ensure base space directory exists with sticky permissions
  systemd.tmpfiles.rules = [
    "d ${spacePath} 2775 media media -"
  ];
}
