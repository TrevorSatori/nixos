{ pkgs, config, lib, ... }:

{
  services.vaultwarden = {
    enable = true;

    # Pointing to your .env secret file
    environmentFile = "/var/src/secrets/vaultwarden.env";

    config = {
      # Public URL for bitwarden extensions and apps
      DOMAIN = "https://vault.lo-pan.com";

      # Bind locally so Caddy can reverse proxy
      ROCKET_ADDRESS = "127.0.0.1";
      ROCKET_PORT = 8222;

      # WebSocket settings for live sync across devices
      WEBSOCKET_ENABLED = true;
      WEBSOCKET_ADDRESS = "127.0.0.1";
      WEBSOCKET_PORT = 3012;

      # Signups enabled initially so you can create your account.
      # (Change to false after creating your user to lock down access)
      SIGNUPS_ALLOWED = true;
    };
  };
}
