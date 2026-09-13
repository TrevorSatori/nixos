{ pkgs, lib, config, ... }:

let
  influxPort  = 8086;
  grafanaPort = 3010;
in {
  # ──────────────────────────────────────────────────────────────────────────
  # InfluxDB 2.x — time-series storage for all activity / biometric data
  # ──────────────────────────────────────────────────────────────────────────
  services.influxdb2.enable = true;

  # ──────────────────────────────────────────────────────────────────────────
  # Grafana — dashboards and visualization
  # Admin credentials + tokens injected via /var/src/secrets/monitoring.env
  # ──────────────────────────────────────────────────────────────────────────
  services.grafana = {
    enable = true;
    settings = {
      server = {
        http_port = grafanaPort;
        http_addr = "127.0.0.1";
        domain    = "grafana.lo-pan.com";
        root_url  = "https://grafana.lo-pan.com";
      };
      security = {
        admin_user     = "satori";
        admin_password = "$__env{GRAFANA_ADMIN_PASSWORD}";
        secret_key     = "$__env{GRAFANA_SECRET_KEY}";
      };
    };
    provision = {
      enable = true;
      datasources.settings.datasources = [{
        name = "InfluxDB";
        type = "influxdb";
        url  = "http://localhost:${toString influxPort}";
        jsonData = {
          version       = "Flux";
          organization  = "home";
          defaultBucket = "activity";
        };
        secureJsonData.token = "$__env{GRAFANA_INFLUX_TOKEN}";
      }];
    };
  };
  systemd.services.grafana.serviceConfig.EnvironmentFile =
    "/var/src/secrets/monitoring.env";

  networking.firewall.allowedTCPPorts = [ grafanaPort ];
}
