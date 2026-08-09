{ config, lib, pkgs, ... }:

{
  # -------------------------------------------------------------------------
  # PORKCHOP EXPRESS (Btrfs RAID1)
  # -------------------------------------------------------------------------
  
  # Media - Subvolume @media
  fileSystems."/data/media" = {
    device = "/dev/disk/by-uuid/9527c6af-757d-41bf-b6af-d453e34afaac";
    fsType = "btrfs";
    options = [ "subvol=@media" "compress=zstd:3" "autodefrag" "noatime" "space_cache=v2" "nofail" ];
  };

  # Downloads - Subvolume @downloads (No CoW compatible, no compression)
  fileSystems."/data/downloads" = {
    device = "/dev/disk/by-uuid/9527c6af-757d-41bf-b6af-d453e34afaac";
    fsType = "btrfs";
    options = [ "subvol=@downloads" "noatime" "space_cache=v2" "nofail" ];
  };

  # Snapshots - Subvolume @snapshots
  fileSystems."/data/snapshots" = {
    device = "/dev/disk/by-uuid/9527c6af-757d-41bf-b6af-d453e34afaac";
    fsType = "btrfs";
    options = [ "subvol=@snapshots" "noatime" "space_cache=v2" "nofail" ];
  };

  # Production - Subvolume @production
  fileSystems."/data/production" = {
    device = "/dev/disk/by-uuid/9527c6af-757d-41bf-b6af-d453e34afaac";
    fsType = "btrfs";
    options = [ "subvol=@production" "compress=zstd:3" "autodefrag" "noatime" "space_cache=v2" "nofail" ];
  };

  # Archive - Subvolume @archive
  fileSystems."/data/archive" = {
    device = "/dev/disk/by-uuid/9527c6af-757d-41bf-b6af-d453e34afaac";
    fsType = "btrfs";
    options = [ "subvol=@archive" "compress=zstd:3" "noatime" "space_cache=v2" "nofail" ];
  };

  # Secrets - Subvolume @secrets
  fileSystems."/data/secrets" = {
    device = "/dev/disk/by-uuid/9527c6af-757d-41bf-b6af-d453e34afaac";
    fsType = "btrfs";
    options = [ "subvol=@secrets" "noatime" "space_cache=v2" "nofail" ];
  };

  # -------------------------------------------------------------------------
  # Mog (Btrfs Pool)
  # -------------------------------------------------------------------------
  fileSystems."/mnt/mog" = {
    device = "/dev/disk/by-uuid/dbf96d86-43ba-412d-b3b9-154090d3ec11";
    fsType = "btrfs";
    options = [ "compress=zstd:3" "autodefrag" "noatime" "space_cache=v2" "nofail" ];
  };
}
