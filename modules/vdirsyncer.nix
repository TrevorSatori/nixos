{ pkgs, ... }:

let
  # Declarative vdirsyncer config. Secrets in /var/src/secrets/vdirsyncer.env
  # (RADICALE_PERSONAL_USERNAME, RADICALE_PERSONAL_PASSWORD).
  vdirsyncerConf = pkgs.writeText "vdirsyncer.conf" ''
    [general]
    status_path = "/var/lib/vdirsyncer/status/"

    [pair contacts]
    a = "contacts_local"
    b = "contacts_remote"
    collections = ["from a", "from b"]
    conflict_resolution = "b wins"
    metadata = ["displayname"]

    [storage contacts_local]
    type = "filesystem"
    path = "/var/lib/vdirsyncer/contacts/"
    fileext = ".vcf"

    [storage contacts_remote]
    type = "carddav"
    url = "http://localhost:5232/"
    username.fetch = ["shell", "echo -n $RADICALE_PERSONAL_USERNAME"]
    password.fetch = ["shell", "echo -n $RADICALE_PERSONAL_PASSWORD"]
  '';

  # khard reads contacts from the vdir vdirsyncer writes.
  khardConf = pkgs.writeText "khard.conf" ''
    [addressbooks]
    [[personal]]
    path = /var/lib/vdirsyncer/contacts/0b625adb-9be5-9de3-90ee-a399ad34b0e4/

    [general]
    debug = no
    default_action = list

    [contact table]
    display = first_name
    show_nicknames = yes
    sort = last_name

    [vcard]
    preferred_version = 3.0
  '';
in
{
  # 1. Dedicated System User & Group
  users.users.vdirsyncer = {
    isSystemUser = true;
    group = "vdirsyncer";
    description = "vdirsyncer CalDAV/CardDAV sync daemon";
    home = "/var/lib/vdirsyncer";
    homeMode = "0750";  # let group traverse
    createHome = true;
  };
  users.groups.vdirsyncer = {};

  # Let hermes read synced contact vcards for khard queries.
  users.users.hermes.extraGroups = [ "vdirsyncer" ];

  # 2. Package Availability
  environment.systemPackages = with pkgs; [
    vdirsyncer
    khard
  ];

  # Point khard at the declarative config globally.
  environment.sessionVariables = {
    KHARD_CONFIG = "/var/src/secrets/khard.conf";
  };

  # 3. Create State Directories and Secret Permissions Declaratively
  systemd.tmpfiles.rules = [
    "d /var/lib/vdirsyncer 0750 vdirsyncer vdirsyncer -"
    "d /var/lib/vdirsyncer/status 0750 vdirsyncer vdirsyncer -"
    "d /var/lib/vdirsyncer/contacts 0755 vdirsyncer vdirsyncer -"
    "z /var/src/secrets/vdirsyncer.env 0600 vdirsyncer vdirsyncer -"
    "L+ /var/src/secrets/vdirsyncer.conf - - - - ${vdirsyncerConf}"
    "L+ /var/src/secrets/khard.conf     - - - - ${khardConf}"
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
      ExecStart = "${pkgs.vdirsyncer}/bin/vdirsyncer -c ${vdirsyncerConf} sync";
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
