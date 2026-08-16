{ pkgs, ... }:

{
  systemd.tmpfiles.rules = [
    "z /var/src/secrets/matrix-token 0600 tuwunel tuwunel -"
  ];

  systemd.services.tuwunel.serviceConfig = {
    EnvironmentFile = [ "/var/src/secrets/matrix-token" ];
  };

  services.matrix-tuwunel = {
    enable = true;
    settings = {
      global = {
        server_name = "lo-pan";
        port = [ 6167 ];
        address = [ "0.0.0.0" ];
        allow_registration = true;
        registration_token = "\${TUWUNEL_REGISTRATION_TOKEN}";
        allow_federation = false;
      };
    };
  };

  networking.firewall.allowedTCPPorts = [ 6167 ];
}
