{
  description = "Homelab NixOS Flake Configuration";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    hermes-agent.url = "github:NousResearch/hermes-agent";
  };

  outputs = { self, nixpkgs, hermes-agent, ... }: {
    nixosConfigurations.lo-pan = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        hermes-agent.nixosModules.default
        ./hardware-configuration.nix
        ./configuration.nix
        ./modules/caddy.nix
        ./modules/arr_stack.nix
        ./modules/media.nix
        ./modules/utilities.nix
        ./modules/storage.nix
        ./modules/networking-vpn.nix
        ./modules/matrix.nix
        ./modules/homepage.nix
        # ./modules/hermes.nix
        ./modules/vaultwarden.nix
        ./modules/backup.nix
        ./modules/radicale.nix
        ./modules/vdirsyncer.nix
        ./modules/smb.nix
      ];
    };
  };
}
