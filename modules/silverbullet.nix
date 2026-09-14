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

  dailyNoteEnv = {
    SILVERBULLET_SPACE       = spacePath;
    INFLUX_URL               = "http://127.0.0.1:8086";
    INFLUX_ORG               = "home";
    INFLUX_ACTIVITY_BUCKET   = "activity";
    INFLUX_BIOMETRICS_BUCKET = "biometrics";
    TZ                       = "America/Chicago";
  };

  dailyNoteExec = "${pkgs.python3}/bin/python3 ${../scripts/silverbullet/daily_note.py}";
  dailyNoteEnvFile = "/var/src/secrets/silverbullet-influx.env";
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

  # ---------------------------------------------------------------------------
  # Daily Note Writer — one-shot service invoked by timer at 12:00 (noon) daily.
  # Populates yesterday's Journal/YYYY/MM/YYYY-MM-DD.md with activity + health
  # summary blocks from InfluxDB, and creates today's blank skeleton.
  # Idempotent — only content between <!-- BEGIN AUTO --> markers is rewritten.
  # Manual per-date regen:  systemctl start daily-note-writer@2026-09-08.service
  # ---------------------------------------------------------------------------
  systemd.services.daily-note-writer = {
    description = "Write yesterday's SilverBullet daily note + today's skeleton";
    after    = [ "network.target" "influxdb2.service" ];
    environment = dailyNoteEnv;
    serviceConfig = hardenedConfig // {
      Type            = "oneshot";
      ExecStart       = dailyNoteExec;
      User            = "media";
      Group           = "media";
      ReadWritePaths  = [ spacePath ];
      EnvironmentFile = dailyNoteEnvFile;
    };
  };

  systemd.services."daily-note-writer@" = {
    description = "Regenerate SilverBullet daily note for %i";
    after    = [ "network.target" "influxdb2.service" ];
    environment = dailyNoteEnv;
    serviceConfig = hardenedConfig // {
      Type            = "oneshot";
      ExecStart       = "${dailyNoteExec} %i";
      User            = "media";
      Group           = "media";
      ReadWritePaths  = [ spacePath ];
      EnvironmentFile = dailyNoteEnvFile;
    };
  };

  systemd.timers.daily-note-writer = {
    description = "Trigger daily-note-writer at 12:00 (noon) local time";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "*-*-* 12:00:00";
      Persistent = true;   # if the machine was off at noon, catch up on next boot
    };
  };

  # Ensure base space directory exists with sticky permissions
  systemd.tmpfiles.rules = [
    "d ${spacePath} 2775 media media -"
  ];
}
