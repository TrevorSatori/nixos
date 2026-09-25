{ config, pkgs, ... }:

{
  # ---------------------------------------------------------------------------
  # Forgejo — self-hosted Git forge at https://git.lo-pan.com
  # ---------------------------------------------------------------------------
  services.forgejo = {
    enable = true;

    # Postgres (already running for Immich). Peer auth over the local socket,
    # so there's no DB password to manage.
    database = {
      type = "postgres";
      createDatabase = true;
    };

    # Git LFS for big binaries
    lfs.enable = true;

    settings = {
      server = {
        DOMAIN = "git.lo-pan.com";
        ROOT_URL = "https://git.lo-pan.com/";
        # Bind locally so only Caddy reaches it (homepage owns :3000)
        HTTP_ADDR = "127.0.0.1";
        HTTP_PORT = 3002;

        # Git over SSH rides the system OpenSSH on :22 as the `forgejo` user
        #   git clone forgejo@git.lo-pan.com:satori/repo.git
        SSH_DOMAIN = "git.lo-pan.com";
        SSH_PORT = 22;
      };

      service = {
        # Private forge: no public signups. Admin gets created via CLI.
        DISABLE_REGISTRATION = true;
        REQUIRE_SIGNIN_VIEW = false;
      };

      session.COOKIE_SECURE = true;

      repository.DEFAULT_BRANCH = "main";

      # PRs squash-merge by default (applies to newly created repos)
      "repository.pull-request" = {
        DEFAULT_MERGE_STYLE = "squash";
        # Stuff the branch commit messages into the squash commit body
        POPULATE_SQUASH_COMMENT_WITH_COMMIT_MESSAGES = true;
      };

      # Internal secrets (SECRET_KEY, INTERNAL_TOKEN, JWT) are auto-generated
      # by the NixOS module into /var/lib/forgejo/custom/conf — never in store.
    };
  };
}
