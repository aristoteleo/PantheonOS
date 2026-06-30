# Deploying a fleet-relay

A relay is the data-plane **fallback**: when two Nodes can't hole-punch a direct
connection, they relay their Transfer through it. So a relay only makes sense on
a machine with a **public IP** and an **open UDP port**. Direct (hole-punched)
connections bypass it — the relay only carries traffic it has to.

## 1. Build for the target host

Pure Go, no CGO → one static binary:

```sh
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -o fleet-relay ./cmd/fleet-relay
scp fleet-relay relayhost:/usr/local/bin/
```

## 2. Open the UDP port

The relay listens on **UDP** (QUIC), default `--port 4250`. Allow inbound UDP:

```sh
ufw allow 4250/udp           # or the cloud provider's firewall / security group
```

## 3. Run it — stable identity + public announce (both required)

```sh
fleet-relay --port 4250 \
            --announce <PUBLIC_IP> \
            --identity /var/lib/pantheon-fleet/relay.key
```

It prints the multiaddr to advertise:

```
/ip4/<PUBLIC_IP>/udp/4250/quic-v1/p2p/<PEER_ID>
```

- `--identity` keeps `<PEER_ID>` **stable across restarts** (so the address nodes
  are configured with stays valid). Back this key file up.
- `--announce` makes the relay advertise its **public** address (a cloud VM's
  listen address is usually a private IP, which peers can't dial).

## 4. Run as a service (systemd)

Use the template unit in [`fleet-relay@.service`](fleet-relay@.service); the
instance name is the public IP:

```sh
cp fleet-relay@.service /etc/systemd/system/
systemctl enable --now fleet-relay@<PUBLIC_IP>
systemctl status fleet-relay@<PUBLIC_IP>      # the log line has the multiaddr
```

## 5. Wire it into the Fleet

Best: hand the relay multiaddr to the **Controller**, so every joining Node gets
it automatically in its join response:

```sh
fleet-controller --relays /ip4/<PUBLIC_IP>/udp/4250/quic-v1/p2p/<PEER_ID> …
```

Or give a Node the relay directly:

```sh
fleet up --relays /ip4/<PUBLIC_IP>/udp/4250/quic-v1/p2p/<PEER_ID> …
```

## Notes

- **Redundancy / latency:** run 2+ relays in different regions; pass all of them
  as a comma-separated `--relays`.
- **Relay limits (important):** libp2p's Circuit Relay v2 default caps each
  relayed connection at **128 KB / 2 min**, which silently truncates any real
  Transfer. `fleet-relay` therefore defaults to **unlimited** so the data plane
  works out of the box. For anti-abuse on a shared relay, opt into caps:

  ```sh
  fleet-relay --limit-mb 512 --limit-min 30 …   # 512 MB/dir, 30 min per connection
  ```

  `--limit-mb 0` / `--limit-min 0` (the defaults) mean unlimited.
- **Hole-punching beats most firewalls:** a relay is only the *fallback*. Two
  Nodes behind ordinary stateful firewalls / cone NATs will usually still get a
  **direct** (hole-punched) connection — the outbound packet each side sends
  during DCUtR opens the return path in the firewall's connection tracker. The
  relay carries bulk data only for the harder cases (symmetric NAT / CGNAT /
  strict egress filtering) where hole-punching genuinely fails.
- **Privacy:** the relay only sees ciphertext — libp2p Noise/TLS is end-to-end
  between the two Nodes.
