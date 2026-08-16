{ pkgs, ... }:

{
  # Install vdirsyncer system-wide
  environment.systemPackages = with pkgs; [
    vdirsyncer
  ];

  # Systemd service to run the sync
  systemd.user.services.vdirsyncer = {
    description = "Bidirectional Google Calendar <-> Radicale Sync";
    path = [ pkgs.vdirsyncer ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.vdirsyncer}/bin/vdirsyncer sync";
    };
  };

  # Systemd timer to trigger the service every 15 minutes
  systemd.user.timers.vdirsyncer = {
    description = "Run vdirsyncer every 15 minutes";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2m";
      OnUnitActiveSec = "15m";
      Persistent = true;
    };
  };
}
