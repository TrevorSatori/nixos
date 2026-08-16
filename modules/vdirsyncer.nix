{ pkgs, ... }:

{
  # 1. Dedicated System User & Group
  users.users.vdirsyncer = {
    isSystemUser = true;
    group = "vdirsyncer";
    description = "vdirsyncer CalDAV/CardDAV sync daemon";
    home = "/var/lib/vdirsyncer";
    createHome = true;
  };
  users.groups.vdirsyncer = {};

  # 2. Package Availability
  environment.systemPackages = with pkgs; [
    vdirsyncer
  ];

  # 3. Create State Directories and Secret Permissions Declaratively
  systemd.tmpfiles.rules = [
    "d /var/lib/vdirsyncer 0750 vdirsyncer vdirsyncer -"
    "d /var/lib/vdirsyncer/status 0750 vdirsyncer vdirsyncer -"
    "z /var/src/secrets/vdirsyncer.conf 0600 vdirsyncer vdirsyncer -"
    "z /var/src/secrets/vdirsyncer.env 0600 vdirsyncer vdirsyncer -"
  ];

  # 4. Sandboxed Systemd System Service
  systemd.services.vdirsyncer = {
    description = "vdirsyncer CalDAV Sync Daemon";
    after = [ "network-online.target" "radicale.service" ];
    wants = [ "network-online.target" ];

    serviceConfig = {
      Type = "oneshot";
      User = "vdirsyncer";
      Group = "vdirsyncer";
      WorkingDirectory = "/var/lib/vdirsyncer";
      StateDirectory = "vdirsyncer";

      # Load secret tokens or passwords if used in config
      EnvironmentFile = [ "-/var/src/secrets/vdirsyncer.env" ];

      # Explicit configuration path
      ExecStart = "${pkgs.vdirsyncer}/bin/vdirsyncer -c /var/src/secrets/vdirsyncer.conf sync";
      TimeoutStartSec = "4m";

      # Kernel Hardening & Isolation
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true; # Completely hides /home/satori
      ProtectKernelTunables = true;
      ProtectControlGroups = true;
      PrivateTmp = true;
      ReadWritePaths = [ "/var/lib/vdirsyncer" ];

      StandardOutput = "journal";
      StandardError = "journal";
    };
  };

  # 5. System Timer (Syncs every 15 minutes)
  systemd.timers.vdirsyncer = {
    description = "Run vdirsyncer every 15 minutes";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "3m";
      OnUnitActiveSec = "10m";
      Persistent = true;
    };
  };
}
