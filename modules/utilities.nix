{ pkgs, ... }:

{

  services.freshrss = {
    enable = true;
    baseUrl = "http://lo-pan:1080";
    defaultUser = "satori";
    passwordFile = "/var/src/secrets/freshrss-pass";
    authType = "form";
    database.type = "sqlite";
    virtualHost = "lo-pan";
  };

  # 2. PHP-FPM Pool Configuration
  services.phpfpm.pools.freshrss = {
    phpOptions = ''
      date.timezone = "America/Chicago"
      session.save_path = "/var/lib/freshrss/data"
      upload_tmp_dir = "/var/lib/freshrss/data"
    '';
  };

  # Un-harden PHP-FPM so it can write config files in /var/lib/freshrss during the installer
  systemd.services.phpfpm-freshrss.serviceConfig = {
    ReadWritePaths = [ "/var/lib/freshrss" ];
    ProtectSystem = pkgs.lib.mkForce false;
  };

  # Declaratively ensure permissions and directories exist
  systemd.tmpfiles.rules = [
    # Ensure parent secrets directory allows user traversal without exposing contents
    "d /var/src/secrets 0711 root root -"
    
    # Secret file permissions (freshrss user can read it)
    "z /var/src/secrets/freshrss-pass 0600 freshrss freshrss -"

    # State directories
    "d /var/lib/freshrss 0775 freshrss freshrss -"
    "d /var/lib/freshrss/cache 0775 freshrss freshrss -"
    "d /var/lib/freshrss/users 0775 freshrss freshrss -"
    "d /var/lib/freshrss/favicons 0775 freshrss freshrss -"
    "d /var/lib/freshrss/data 0775 freshrss freshrss -"
    "d /var/lib/freshrss/tokens 0775 freshrss freshrss -"
  ];
  # Grant Nginx group membership for freshrss
  users.users.nginx.extraGroups = [ "freshrss" ];

  # Separate Nginx block to handle Port 1080
  services.nginx = {
    enable = true;
    virtualHosts."lo-pan" = {
      listen = [
        {
          addr = "0.0.0.0";
          port = 1080;
        }
      ];
    };
  };

  # Syncthing
  services.syncthing = {
    enable = true;
    user = "satori";
    dataDir = "/var/lib/syncthing";
    overrideDevices = false;
    overrideFolders = false;
    guiAddress = "0.0.0.0:8384";
  };

  services.uptime-kuma = {
    enable = true;
    settings = {
      HOST = "0.0.0.0";
      PORT = "3001";
    };
  };
  
  # Homepage
  services.homepage-dashboard = {
    enable = true;
    listenPort = 3000;
    
    allowedHosts = "lo-pan:3000,homepage.lo-pan.com";
    # -------------------------------------------------------------------------
    # settings.yaml
    # -------------------------------------------------------------------------
    settings = {
      title = "Lo-Pan's Dashboard";
      theme = "dark";
      color = "slate";
      background = {
        image = "https://images.unsplash.com/photo-1502790671504-542ad42d5189?auto=format&fit=crop&w=2560&q=80";
        blur = "sm";
        saturate = 50;
        brightness = 50;
        opacity = 75;
      };
      providers = {
        openweathermap = "openweathermapapikey";
        weatherapi = "weatherapiapikey";
      };
    };

    # -------------------------------------------------------------------------
    # widgets.yaml
    # -------------------------------------------------------------------------
    widgets = [
      {
        resources = {
          cpu = true;
          memory = true;
          disk = "/";
        };
      }
      {
        search = {
          provider = "duckduckgo";
          target = "_blank";
        };
      }
    ];

    # -------------------------------------------------------------------------
    # bookmarks.yaml
    # -------------------------------------------------------------------------
    bookmarks = [
      {
        "AI & Research" = [
          { Gemini = [{ icon = "si-googlegemini"; href = "https://gemini.google.com/"; }]; }
          { Grok = [{ icon = "si-x"; href = "https://grok.com/"; }]; }
          { ChatGPT = [{ icon = "si-openai"; href = "https://chatgpt.com/"; }]; }
          { Claude = [{ icon = "si-anthropic"; href = "https://claude.ai/"; }]; }
          { "Brave Search" = [{ icon = "si-brave"; href = "https://search.brave.com/"; }]; }
          { Yandex = [{ icon = "si-yandex"; href = "https://yandex.com/"; }]; }
        ];
      }
      {
        "SaaS Infrastructure" = [
          { Cloudflare = [{ icon = "si-cloudflare"; href = "https://dash.cloudflare.com/"; }]; }
          { DigitalOcean = [{ icon = "si-digitalocean"; href = "https://cloud.digitalocean.com/"; }]; }
          { Firebase = [{ icon = "si-firebase"; href = "https://console.firebase.google.com/"; }]; }
          { GitLab = [{ icon = "si-gitlab"; href = "https://gitlab.com/"; }]; }
          { GitHub = [{ icon = "si-github"; href = "https://github.com/"; }]; }
        ];
      }
      {
        Entertainment = [
          { YouTube = [{ icon = "si-youtube"; href = "https://youtube.com/"; }]; }
          { "X (Twitter)" = [{ icon = "si-x"; href = "https://x.com/"; }]; }
        ];
      }
    ];

    # -------------------------------------------------------------------------
    # services.yaml
    # -------------------------------------------------------------------------
    services = [
      {
        "Media & Books" = [
          {
            FreshRSS = {
              icon = "freshrss.png";
              href = "http://lo-pan:1080";
              description = "RSS Feed Aggregator";
            };
          }
          {
            Immich = {
              icon = "immich.png";
              href = "http://lo-pan:2283";
              description = "Photo & Video Management";
            };
          }
          {
            Jellyfin = {
              icon = "jellyfin.png";
              href = "http://lo-pan:8096";
              description = "Media Streaming";
            };
          }
          {
            Audiobookshelf = {
              icon = "audiobookshelf.png";
              href = "http://lo-pan:13378";
              description = "Audiobooks & Podcasts";
            };
          }
          {
            Komga = {
              icon = "komga.png";
              href = "http://lo-pan:25600";
              description = "Comics & Manga";
            };
          }
          {
            Calibre = {
              icon = "calibre.png";
              href = "http://lo-pan:8082";
              description = "E-Book Library";
            };
          }
        ];
      }
      {
        "Downloads & Automation" = [
          {
            Sonarr = {
              icon = "sonarr.png";
              href = "http://lo-pan:8989";
              description = "TV Series Management";
            };
          }
          {
            Radarr = {
              icon = "radarr.png";
              href = "http://lo-pan:7878";
              description = "Movie Management";
            };
          }
          {
            Lidarr = {
              icon = "lidarr.png";
              href = "http://lo-pan:8686";
              description = "Music Management";
            };
          }
          {
            Prowlarr = {
              icon = "prowlarr.png";
              href = "http://lo-pan:9696";
              description = "Indexer Manager";
            };
          }
          {
            qBittorrent = {
              icon = "qbittorrent.png";
              href = "http://lo-pan:8080";
              description = "Torrent Client";
            };
          }
        ];
      }
      {
        "Management & Tools" = [
          {
            "Uptime Kuma" = {
              icon = "uptime-kuma.png";
              href = "http://lo-pan:3001";
              description = "Uptime & Status Monitoring";
            };
          }
          {
            Matrix = {
              icon = "matrix.png";
              href = "http://lo-pan:8082";
              description = "Private Messaging & AI Gateway";
            };
          }
          {
            Syncthing = {
              icon = "syncthing.png";
              href = "http://lo-pan:8384";
              description = "Continuous File Sync";
            };
          }
        ];
      }
    ];
  };
}
