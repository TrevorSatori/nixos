{ pkgs, ... }:

{
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
            Radicale = {
              icon = "radicale.png";
              href = "https://dav.lo-pan.com";
              description = "CalDAV & CardDAV Server";
              ping = "http://127.0.0.1:5232";
            };
          }
          {
            Vaultwarden = {
              icon = "vaultwarden.png";
              href = "https://vault.lo-pan.com";
              description = "Password Manager";
            };
          }
          {
            "Uptime Kuma" = {
              icon = "uptime-kuma.png";
              href = "http://lo-pan:3001";
              description = "Uptime & Status Monitoring";
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
