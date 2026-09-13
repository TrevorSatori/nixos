{ pkgs, ... }:

{
  services.paperless = {
    enable = true;
    address = "127.0.0.1";
    port = 28981;

    # Standard NixOS state directory defaults
    dataDir = "/var/lib/paperless";
    mediaDir = "/var/lib/paperless/media";
    consumptionDir = "/var/lib/paperless/consume";
    consumptionDirIsPublic = true;

    # Admin password stored in your secrets folder
    passwordFile = "/var/src/secrets/paperless.env";

    settings = {
      PAPERLESS_URL = "https://paperless.lo-pan.com";
      PAPERLESS_OCR_LANGUAGE = "eng";

      # Reverse proxy headers for Caddy
      PAPERLESS_USE_X_FORWARD_HOST = true;
      PAPERLESS_USE_X_FORWARD_PORT = true;
      PAPERLESS_TRUSTED_PROXIES = "127.0.0.1";

      PAPERLESS_CONSUMER_ENABLE_BARCODES = true;
    };
  };

  # Paperless system user
  users.users.paperless.extraGroups = [ "media" ];
}
