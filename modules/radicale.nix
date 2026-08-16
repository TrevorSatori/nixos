{ config, lib, pkgs, ... }:

{
  services.radicale = {
    enable = true;
    settings = {
      auth = {
        type = "htpasswd";
        htpasswd_filename = "/var/src/secrets/radicale.htpasswd";
        htpasswd_encryption = "bcrypt";
      };
      storage = {
        filesystem_folder = "/var/lib/radicale/collections";
      };
      logging = {
        level = "info";
      };
    };
  };
}
