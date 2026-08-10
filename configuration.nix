{ pkgs, lib, config, ... }:

{
  # ---------------------------------------------------------------------------
  # Host Identity & Core Networking
  # ---------------------------------------------------------------------------
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  networking.hostName = "lo-pan";
  networking.networkmanager.enable = true;

  # Enable Flakes and modern CLI commands
  nix.settings.experimental-features = [ "nix-command" "flakes" ];
  nixpkgs.config.allowUnfree = true;

  # Timezone and Localization
  time.timeZone = "America/Chicago";
  i18n.defaultLocale = "en_US.UTF-8";

  # ---------------------------------------------------------------------------
  # Hardware Acceleration (Intel Celeron QuickSync / VAAPI)
  # ---------------------------------------------------------------------------
  hardware.graphics = {
    enable = true;
    extraPackages = with pkgs; [
      intel-media-driver  # Modern VAAPI driver for Intel Gen 9+
      intel-vaapi-driver   # Fallback i965 driver
      libvdpau-va-gl
    ];
  };

  # ---------------------------------------------------------------------------
  # Users and Permissions (Shared Media UID/GID 1800)
  # ---------------------------------------------------------------------------
  users.groups.media.gid = 1800;
  users.users.media = {
    isSystemUser = true;
    group = "media";
    uid = 1800;
  };

  # Primary Admin User
  users.users.satori = {
    isNormalUser = true;
    extraGroups = [ "wheel" "networkmanager" "video" "render" ]; # video/render for GPU
    shell = pkgs.zsh;

    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAYtBeGpCwTLk6myRFcUjRAgBan1v4kd9wmpHz0pg11 trevor@bloodofchrist"
    ];

    packages = with pkgs; [
      go 
      nodejs
      rustup
      neovim
      git
      tmux
      htop
      curl
      wget
      stow
      ripgrep
      fd
      starship
      pyenv                   
      fastfetch                
    ];
  };

  programs.zsh = {
    enable = true;
    autosuggestions.enable = true;
    syntaxHighlighting.enable = true;
  };

  # System-wide Base Packages
  environment.systemPackages = with pkgs; [
    neovim
    git
    networkmanager
    wireguard-tools
    restic
  ];

  # ---------------------------------------------------------------------------
  # Restic Automated Backups
  # ---------------------------------------------------------------------------
  # services.restic.backups.homelab = {
  #   repository = "/mnt/backups/restic"; # Change to S3/B2 or external drive as needed
  #   passwordFile = "/var/src/secrets/restic-password";
  #   initialize = true;
  #   paths = [
  #     "/var/lib"   # Automatically captures all app DBs (Jellyfin, Sonarr, Immich, etc.)
  #     "/etc/nixos" # Captures Nix configuration code
  #   ];
  #   timerConfig = {
  #     OnCalendar = "daily";
  #     Persistent = true;
  #   };
  #   pruneOpts = [
  #     "--keep-daily 7"
  #     "--keep-weekly 4"
  #     "--keep-monthly 12"
  #   ];
  # };

  # ---------------------------------------------------------------------------
  # Remote Access (SSH)
  # ---------------------------------------------------------------------------
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "no";
      PasswordAuthentication = true;
    };
  };

  # ---------------------------------------------------------------------------
  # Host Firewall
  # ---------------------------------------------------------------------------
  networking.firewall = {
    enable = true;
    allowedTCPPorts = [
    80    # Caddy 
    443   # Caddy
    1080  # FreshRSS
    2283  # Immich
    3000  # Homepage Dashboard
    3001  # Uptime Kuma
    7878  # Radarr
    8080  # qBittorrent
    8082  # Calibre
    8096  # Jellyfin
    8384  # Syncthing (Web GUI)
    8686  # Lidarr
    8989  # Sonarr
    9000  # Portainer
    9696  # Prowlarr
    13378 # Audiobookshelf
    25600 # Komga
  ];
    allowedUDPPorts = [ 51820 22000 21027 ];
  };

  system.stateVersion = "24.11";
}
