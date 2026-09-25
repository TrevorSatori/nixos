{ ... }:
{
  imports = [
    ./arr_stack.nix
    ./backup.nix
    ./caddy.nix
    ./forgejo.nix
    ./forgejo-runner.nix
    ./hermes.nix
    ./homepage.nix
    ./matrix.nix
    ./media.nix
    ./networking-vpn.nix
    ./paperless.nix
    ./radicale.nix
    ./silverbullet.nix
    ./smb.nix
    ./storage.nix
    ./utilities.nix
    ./vaultwarden.nix
    ./vdirsyncer.nix

    # Activity / observability stack
    ./observability.nix
    ./activity-collectors.nix
  ];
}
