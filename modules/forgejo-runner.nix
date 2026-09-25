{ config, lib, pkgs, ... }:

{
  # ---------------------------------------------------------------------------
  # Forgejo Actions runner — runs CI jobs directly on lo-pan
  #
  # Forgejo 12+ flow: the runner is created in the UI (Site Admin > Actions >
  # Runners), which hands out a UUID + that runner's own token. No `register`.
  #   UUID  → below (not secret)
  #   token → /var/src/secrets/forgejo-runner.token (raw token, root:root 600),
  #           passed in via systemd LoadCredential so the DynamicUser can read it
  #
  # The nixpkgs module still does the legacy register dance, so we keep it for
  # PATH/service plumbing and switch that part off.
  # ---------------------------------------------------------------------------

  services.forgejo.settings.actions.ENABLED = true;

  services.gitea-actions-runner = {
    package = pkgs.forgejo-runner;
    instances.lopan = {
      enable = true;
      name = "lo-pan";
      url = "https://git.lo-pan.com";
      # Module asserts token XOR tokenFile; unused since registration is off
      token = "unused";
      # Must contain a ":host" label or the module silently drops hostPackages
      # from PATH. Only used for that check (registration is disabled below).
      labels = [ "native:host" ];
      # Base toolbox every CI job gets. Repos needing other versions pin them
      # in their own flake.nix and run steps via `nix develop -c ...`.
      hostPackages = with pkgs; [
        # shell + basics
        bash coreutils findutils gawk gnugrep gnused gnutar gzip xz
        curl wget gitMinimal
        # per-repo toolchains (nix develop / nix shell)
        nix
        # languages: unversioned attrs = newest stable in our nixpkgs
        nodejs_latest   # also runs JS actions like actions/checkout
        python3
        go
        rustc cargo rustfmt clippy
        gcc gnumake pkg-config   # C toolchain; cargo needs a linker (cc)
      ];

      settings.server.connections.lopan = {
        url = "https://git.lo-pan.com/";
        uuid = "84511669-7823-4097-8519-c22c63849e78";
        token_url = "file:$CREDENTIALS_DIRECTORY/token";
        # "host" executes jobs directly on this box, no containers
        labels = [ "native:host" ];
      };
    };
  };

  # Static user instead of the module's DynamicUser. systemd mounts DynamicUser
  # state (/var/lib/private/...) noexec, so jobs couldn't run scripts or
  # binaries they built. A static user keeps state in /var/lib/gitea-runner.
  users.users.gitea-runner = {
    isSystemUser = true;
    group = "gitea-runner";
    home = "/var/lib/gitea-runner";
  };
  users.groups.gitea-runner = { };

  systemd.services.gitea-runner-lopan.serviceConfig = {
    DynamicUser = lib.mkForce false;
    ExecStartPre = lib.mkForce [ ];
    LoadCredential = "token:/var/src/secrets/forgejo-runner.token";
    # register script used to create the instance dir (HOME); do it here
    StateDirectory = lib.mkForce [ "gitea-runner" "gitea-runner/lopan" ];
  };
}
