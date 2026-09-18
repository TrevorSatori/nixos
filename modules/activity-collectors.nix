{ pkgs, lib, config, ... }:

let
  influxUrl    = "http://127.0.0.1:8086";
  influxOrg    = "home";
  influxBucket = "activity";
  envFile      = "/var/src/secrets/monitoring.env";

  # Python environment for the Garmin poller. Uses nixpkgs' pinned
  # garminconnect (pinned by flake.lock) so upstream can't ship arbitrary
  # code without a deliberate nixpkgs bump.
  #
  # curl-cffi's test suite transitively pulls litestar → fastapi → scipy,
  # and a scipy test currently fails on unstable. Skip curl-cffi's tests
  # to sidestep the broken transitive dep.
  garminPython = pkgs.python312.withPackages (ps: [
    (ps.garminconnect.override {
      curl-cffi = ps.curl-cffi.overridePythonAttrs (_: { doCheck = false; });
    })
  ]);
in {
  # ──────────────────────────────────────────────────────────────────────────
  # Activity collectors — one systemd unit per data source. Each writes to
  # the shared "activity" bucket in InfluxDB using the schema described in
  # scripts/activity/*.py. Add a new source by copying an existing block.
  # ──────────────────────────────────────────────────────────────────────────

  # ABS — polls the listening-sessions API hourly. Sessions are only written
  # once closed, so a shorter interval buys nothing but API chatter.
  systemd.services.abs-to-influx = {
    description = "ABS listening sessions → InfluxDB";
    after    = [ "network.target" "influxdb2.service" "audiobookshelf.service" ];
    wantedBy = [ "multi-user.target" ];
    environment = {
      ABS_URL       = "http://127.0.0.1:13378";
      INFLUX_URL    = influxUrl;
      INFLUX_ORG    = influxOrg;
      INFLUX_BUCKET = influxBucket;
      POLL_INTERVAL = "3600";
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

  # Garmin — polls Garmin Connect every 10 min for activities (→ "activity"
  # bucket) and heart-rate samples (→ "biometrics" bucket). Uses cached OAuth
  # tokens under /var/lib/garmin-to-influx/token/ to avoid re-login.
  #
  # DISABLED by default — flip `wantedBy` to enable once garmin.env is populated
  # and (if MFA is on) an initial interactive login has been performed.
  systemd.services.garmin-to-influx = {
    description = "Garmin Connect → InfluxDB";
    after    = [ "network.target" "influxdb2.service" ];
    wantedBy = [ ];  # start manually; enable in unit once creds are set
    environment = {
      INFLUX_URL              = influxUrl;
      INFLUX_ORG              = influxOrg;
      INFLUX_ACTIVITY_BUCKET  = influxBucket;
      INFLUX_BIOMETRICS_BUCKET = "biometrics";
      POLL_INTERVAL           = "600";
      GARMIN_DEVICE           = "Forerunner 970";
      GARMIN_FIT_DIR          = "/data/archive/garmin";
    };
    serviceConfig = {
      ExecStart       = "${garminPython}/bin/python3 ${../scripts/activity/garmin.py}";
      Restart         = "always";
      RestartSec      = 30;
      StateDirectory  = "garmin-to-influx";
      EnvironmentFile = [ envFile "/var/src/secrets/garmin.env" ];
      # Raw .fit files are archived here alongside the InfluxDB metrics.
      ReadWritePaths  = [ "/data/archive/garmin" ];
    };
  };

  # Garmin .fit archive — created up front so the collector never has to
  # mkdir into /data as a non-root user.
  systemd.tmpfiles.rules = [
    "d /data/archive        0755 root root -"
    "d /data/archive/garmin 0755 root root -"
  ];
}
