{ config, pkgs, ... }:

{
  # 1. Native PostgreSQL Backup for Immich
  services.postgresqlBackup = {
    enable = true;
    databases = [ "immich" "forgejo" ];
    location = "/var/backup/postgresql";
  };

  # 2. Restic Configuration targeting Backblaze B2
  services.restic.backups.system-backup = {
    # All credentials, password, AND repository URL live here:
    environmentFile = "/var/src/secrets/restic-b2.env";

    # Schedule: Run daily at 02:30 AM
    timerConfig = {
      OnCalendar = "03:33:00";
      Persistent = true;
    };

    # Paths to back up
    paths = [
      "/etc/nixos"               # NixOS System configuration
      "/var/src/secrets"         # Secret files / .env files
      "/var/lib/vaultwarden"     # Vaultwarden config/data
      "/var/lib/radicale"
      "/var/lib/audiobookshelf"
      "/var/lib/forgejo"         # Forgejo repos, LFS, avatars, config
      "/var/backup/vaultwarden"  # Vaultwarden DB dumps
      "/data/media/immich"       # Immich original photos/videos
      "/data/media/photos"       # External libraries
      "/data/media/silverbullet" # SilverBullet PKM — daily notes, book/comic notes, configs
      "/var/backup/postgresql"   # Immich + Forgejo Postgres DB dumps
    ];

    # Skip disposable generated caches to save space/bandwidth
    exclude = [
      "/var/lib/immich/encoded-video"
      "/var/lib/immich/thumbs"
      "/var/lib/immich/upload/cache"
    ];

    # Retention policy in Backblaze
    pruneOpts = [
      "--keep-daily 7"
      "--keep-weekly 4"
      "--keep-monthly 12"
    ];

    # Automatically run 'restic init' on the B2 bucket if not initialized yet
    initialize = true;
  };
}
