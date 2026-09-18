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
    extraGroups = [ "wheel" "networkmanager" "media" "video" "render" ]; # video/render for GPU
    shell = pkgs.zsh;

    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFAYtBeGpCwTLk6myRFcUjRAgBan1v4kd9wmpHz0pg11 trevor@bloodofchrist"
    ];

    packages = with pkgs; [
      go 
      nodejs
      rustup
      unzip
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
  # Remote Access (SSH)
  # ---------------------------------------------------------------------------
  services.openssh = {
    enable = true;
    settings = {
      PermitRootLogin = "no";
      PasswordAuthentication = true;
    };
    extraConfig = ''
      ClientAliveInterval 10
      ClientAliveCountMax 3
      TCPKeepAlive yes
    '';
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
    3005  # SilverBullet
    3001  # Uptime Kuma
    7878  # Radarr
    8000  # Apprise Microservice
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

  # ──────────────────────────────────────────────────────────────────────────
  # Fonts. EB Garamond is used for SilverBullet `story` pages (book
  # typography — see configs/story_style in the vault). The vault keeps its
  # own copy under fonts/ because the browser fetches it over HTTP from
  # SilverBullet; installing it here only makes it available to the host.
  # The tmpfiles rules below copy the 12pt cut into the space on activation
  # so the two never drift.
  # ──────────────────────────────────────────────────────────────────────────
  fonts.packages = with pkgs; [
    eb-garamond
  ];

  systemd.tmpfiles.rules = [
    "d /data/media/silverbullet/fonts 2771 media media -"
    "C+ /data/media/silverbullet/fonts/EBGaramond12-Regular.ttf 0664 media media - ${pkgs.eb-garamond}/share/fonts/truetype/EBGaramond12-Regular.ttf"
    "C+ /data/media/silverbullet/fonts/EBGaramond12-Italic.ttf  0664 media media - ${pkgs.eb-garamond}/share/fonts/truetype/EBGaramond12-Italic.ttf"
  ];

  # ──────────────────────────────────────────────────────────────────────────
  # Automatic updates. Bumps every flake input (nixpkgs, hermes-agent, ...),
  # rebuilds, and commits the new flake.lock to /etc/nixos so each upgrade is
  # a tracked, revertable commit.
  #
  # allowReboot = false: kernel/systemd updates stage for the next manual
  # reboot rather than dropping a running server mid-week. Check pending with
  #   systemctl status nixos-upgrade.service
  #   journalctl -u nixos-upgrade.service
  # Roll back a bad upgrade with: nixos-rebuild switch --rollback
  # ──────────────────────────────────────────────────────────────────────────
  system.autoUpgrade = {
    enable = true;
    flake  = "/etc/nixos#lo-pan";
    # No --update-input, so all inputs move together.
    # -L streams build logs into the journal for debugging failures.
    flags  = [ "--commit-lock-file" "-L" ];
    dates  = "weekly";                # Monday 00:00
    randomizedDelaySec = "45min";
    allowReboot = false;
  };

  # Weekly upgrades churn the store — without collection it grows without
  # bound. 30 days keeps a month of generations to roll back to.
  nix.gc = {
    automatic = true;
    dates     = "weekly";
    options   = "--delete-older-than 30d";
  };

  # Hard-link identical files across store paths. Meaningful savings once
  # you're keeping many generations of the same packages.
  nix.optimise = {
    automatic = true;
    dates     = [ "weekly" ];
  };

  system.stateVersion = "24.11";
}
