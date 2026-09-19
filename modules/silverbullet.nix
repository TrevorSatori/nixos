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
  archiveTasksExec = "${pkgs.python3}/bin/python3 ${../scripts/silverbullet/archive_tasks.py}";
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
    description = "Create today's blank daily-note skeleton at 00:00 local time";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "*-*-* 00:00:00";
      Persistent = true;   # if the machine was off at midnight, catch up on next boot
    };
  };

  # ---------------------------------------------------------------------------
  # Daily Note Backfill — refreshes today + trailing 7 days at noon so late
  # Garmin/ABS syncs land in the correct day's auto sections.
  # ---------------------------------------------------------------------------
  systemd.services.daily-note-writer-backfill = {
    description = "Refresh today + trailing 7 days of daily notes";
    after    = [ "network.target" "influxdb2.service" ];
    environment = dailyNoteEnv;
    serviceConfig = hardenedConfig // {
      Type            = "oneshot";
      ExecStart       = "${dailyNoteExec} --backfill 7";
      User            = "media";
      Group           = "media";
      ReadWritePaths  = [ spacePath ];
      EnvironmentFile = dailyNoteEnvFile;
    };
  };

  systemd.timers.daily-note-writer-backfill = {
    description = "Trigger 7-day backfill at 12:00 (noon) local time";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "*-*-* 12:00:00";
      Persistent = true;
    };
  };

  # ---------------------------------------------------------------------------
  # Task Archiver — moves completed tasks out of inbox.md into the daily note
  # for the date they were *completed*, then deletes them from the inbox.
  #
  # Runs at 00:05, five minutes after daily-note-writer creates the new day's
  # skeleton, so a task finished just before midnight still finds its target
  # note. Targeting the completion date (not "today") means a backlog drains
  # to the correct days rather than collapsing into one.
  #
  # Write-verify-then-delete: the line is appended, the target re-read to
  # confirm, and only then removed from the inbox. A crash leaves a duplicate,
  # never a hole. A missing target note is skipped and left in the inbox.
  #
  # Dry run:  sudo -u media python3 /etc/nixos/scripts/silverbullet/archive_tasks.py
  # ---------------------------------------------------------------------------
  systemd.services.archive-tasks = {
    description = "Archive completed SilverBullet tasks into their daily notes";
    after    = [ "daily-note-writer.service" ];
    serviceConfig = hardenedConfig // {
      Type           = "oneshot";
      ExecStart      = "${archiveTasksExec} --apply";
      User           = "media";
      Group          = "media";
      ReadWritePaths = [ spacePath ];
    };
  };

  systemd.timers.archive-tasks = {
    description = "Archive completed tasks at 00:05 local time";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "*-*-* 00:05:00";
      Persistent = true;   # catch up if the machine was off at midnight
    };
  };

  # Ensure base space directory exists with sticky permissions
  systemd.tmpfiles.rules = [
    "d ${spacePath} 2775 media media -"
  ];
}
