{ pkgs, ... }:

let
  hostSubnet = "192.168.1.0/24";
in
{
  # Enable IP forwarding on host so kernel forwards packets across the veth cable
  boot.kernel.sysctl."net.ipv4.ip_forward" = 1;

  # Open Web UI ports in NixOS Firewall for your home network
  # networking.firewall.allowedTCPPorts = [ 7878 8989 8686 9696 8080 ];

  systemd.services.vpn-namespace = {
    description = "Isolated Mullvad VPN Network Namespace with Local Routing";
    before = [ "network.target" ];
    wantedBy = [ "multi-user.target" ];
    path = with pkgs; [ iproute2 wireguard-tools iptables gnugrep gawk ];
    script = ''
      # 1. Parse assigned IP from secret config
      MULLVAD_IP=$(grep -i '^Address' /var/src/secrets/mullvad-wg.conf | awk -F'=' '{print $2}' | tr -d ' ' | cut -d',' -f1)

      # 2. Create netns and enable loopback
      ip netns add mullvad || true
      ip netns exec mullvad ip link set dev lo up

      # 3. Create veth pair (Host <-> VPN NetNS) for local Web UI access
      ip link add veth-host type veth peer name veth-mullvad
      ip link set veth-mullvad netns mullvad

      # Configure Host side of veth
      ip addr add 10.200.1.1/24 dev veth-host || true
      ip link set dev veth-host up

      # Configure NetNS side of veth
      ip netns exec mullvad ip addr add 10.200.1.2/24 dev veth-mullvad
      ip netns exec mullvad ip link set dev veth-mullvad up

      # 4. Route host LAN traffic back through veth-mullvad to host
      ip netns exec mullvad ip route add ${hostSubnet} via 10.200.1.1 dev veth-mullvad

      # 5. Create WireGuard interface inside namespace
      ip link add wg-mullvad type wireguard
      ip link set wg-mullvad netns mullvad

      # 6. Strip non-WG parameters and setconf
      grep -vE '^(Address|DNS)' /var/src/secrets/mullvad-wg.conf > /tmp/wg-mullvad-stripped.conf
      ip netns exec mullvad wg setconf wg-mullvad /tmp/wg-mullvad-stripped.conf
      rm -f /tmp/wg-mullvad-stripped.conf

      # 7. Assign VPN IP and set VPN as default internet gateway
      ip netns exec mullvad ip address add "$MULLVAD_IP" dev wg-mullvad
      ip netns exec mullvad ip link set dev wg-mullvad up
      ip netns exec mullvad ip route add default dev wg-mullvad

      # 8. Setup isolated static DNS for Mullvad namespace
      mkdir -p /etc/netns/mullvad
      echo "nameserver 10.64.0.1" > /etc/netns/mullvad/resolv.conf
      echo "nameserver 1.1.1.1" >> /etc/netns/mullvad/resolv.conf

      # 9. Forward Host Ports -> Mullvad Namespace (Docker "ports:" equivalent)
      iptables -t nat -A PREROUTING -p tcp -m multiport --dports 7878,8989,8686,9696,8080 -j DNAT --to-destination 10.200.1.2
      iptables -A FORWARD -p tcp -d 10.200.1.2 -m multiport --dports 7878,8989,8686,9696,8080 -j ACCEPT
    '';
    postStop = ''
      iptables -t nat -D PREROUTING -p tcp -m multiport --dports 7878,8989,8686,9696,8080 -j DNAT --to-destination 10.200.1.2 || true
      ip link del veth-host || true
      ip netns del mullvad || true
      rm -rf /etc/netns/mullvad
    '';
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
  };
}

# { pkgs, ... }:
#
# {
  # ---------------------------------------------------------------------------
  # 1. Point-to-Point DigitalOcean Remote WireGuard Tunnel
  # ---------------------------------------------------------------------------
  # networking.wireguard.interfaces.wg-remote = {
  #   ips = [ "10.8.0.2/32" "fdcc:ad94:bacf:61a4::cafe:2/128" ];
  #   privateKeyFile = "/var/src/secrets/wg-remote-private.key";
  #
  #   peers = [{
  #     publicKey = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
  #     allowedIPs = [ "10.8.0.0/24" ];
  #     endpoint = "vpn.lo-pan.com:51820";
  #     persistentKeepalive = 25;
  #   }];
  # };

  # ---------------------------------------------------------------------------
  # 2. Maximum Isolation Mullvad VPN Network Namespace (Gluetun Replacement)
  # ---------------------------------------------------------------------------

