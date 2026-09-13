{ pkgs, lib, config, ... }:

let
  influxUrl    = "http://127.0.0.1:8086";
  influxOrg    = "home";
  influxBucket = "activity";
  envFile      = "/var/src/secrets/monitoring.env";
in {
  # ──────────────────────────────────────────────────────────────────────────
  # Activity collectors — one systemd unit per data source. Each writes to
  # the shared "activity" bucket in InfluxDB using the schema described in
  # scripts/activity/*.py. Add a new source by copying an existing block.
  # ──────────────────────────────────────────────────────────────────────────

  # ABS — polls the listening-sessions API every 10 min.
  systemd.services.abs-to-influx = {
    description = "ABS listening sessions → InfluxDB";
    after    = [ "network.target" "influxdb2.service" "audiobookshelf.service" ];
    wantedBy = [ "multi-user.target" ];
    environment = {
      ABS_URL       = "http://127.0.0.1:13378";
      INFLUX_URL    = influxUrl;
      INFLUX_ORG    = influxOrg;
      INFLUX_BUCKET = influxBucket;
      POLL_INTERVAL = "600";
    };
    serviceConfig = {
      ExecStart       = "${pkgs.python3}/bin/python3 ${../scripts/activity/abs.py}";
      Restart         = "always";
      RestartSec      = 10;
      StateDirectory  = "abs-to-influx";
      EnvironmentFile = envFile;
    };
  };

  # Jellyfin — HTTP receiver for the Webhook plugin.
  # Configure in Jellyfin: Dashboard → Webhook → Add → http://127.0.0.1:9096/webhook
  # Events: PlaybackStart, PlaybackProgress, PlaybackStop
  systemd.services.jellyfin-webhook = {
    description = "Jellyfin webhook → InfluxDB receiver";
    after    = [ "network.target" "influxdb2.service" "jellyfin.service" ];
    wantedBy = [ "multi-user.target" ];
    environment = {
      INFLUX_URL    = influxUrl;
      INFLUX_ORG    = influxOrg;
      INFLUX_BUCKET = influxBucket;
      WEBHOOK_PORT  = "9096";
    };
    serviceConfig = {
      ExecStart       = "${pkgs.python3}/bin/python3 ${../scripts/activity/jellyfin_webhook.py}";
      Restart         = "always";
      RestartSec      = 5;
      StateDirectory  = "jellyfin-webhook";
      EnvironmentFile = envFile;
    };
  };
}
