# bifrost-server
The server Bifrost connects to: accounts with Discord sign-in, custom rooms, casual and ranked queues, the P2P relay,
friends, clans, tournaments, stats and leaderboards, and a save kept per account. Python 3.11+, standard library only.
This repo has no game code, art or files.

## Run

```bat
python -m bh_server
```

Run it from this folder. The game port is 7910 and the sign-in page is on 7911. Players add your address in Bifrost's
Options > SERVERS, and the Bifrost app's Server tab can run a copy of this server on a player's own PC.

## Setting up a server, step by step

These steps are for a Linux VPS (Ubuntu or Debian). Windows works the same way; the differences are noted.

1. **Install Python and Git.**

   ```sh
   sudo apt update
   sudo apt install python3 git
   python3 --version
   ```

   The version has to be 3.11 or newer. On Windows, install Python from python.org and Git from git-scm.com, and use
   `python` wherever these steps say `python3`.

2. **Download the server.**

   ```sh
   git clone https://github.com/Saorax/bifrost-server.git
   cd bifrost-server
   ```

   Run every command below from this folder.

3. **Make your settings file.**

   ```sh
   cp config.local.example.json config.local.json
   ```

   Open `config.local.json` and change `server_name` to your server's name. Leave the rest for now. (On Windows,
   copy the file in Explorer.)

4. **Open the ports.** The game needs 7910 TCP, 7910 UDP (the P2P relay) and 7911 TCP (mods, replays, tournaments
   and the sign-in page). If you use ufw:

   ```sh
   sudo ufw allow 7910/tcp
   sudo ufw allow 7910/udp
   sudo ufw allow 7911/tcp
   ```

   If your provider has its own firewall in its control panel (Hetzner and OVH do), open them there too. If you
   point a domain at the server through Cloudflare, set that DNS record to DNS only (grey cloud), because Cloudflare's
   proxy blocks these ports.

5. **Start it.**

   ```sh
   python3 -m bh_server
   ```

   The log should say `game connection on 0.0.0.0:7910`. To check the ports are open, open
   `http://<your address>:7911/info` from another network, like your phone off Wi-Fi. It should show your
   server's name.

6. **Join it.** In Bifrost, go to Options > SERVERS and add your address (an IP or domain; add `:port` only if
   you changed 7910). Play a match. That's the address you give players.

At this point the server works and everyone plays as a guest. The rest is optional:

7. **Discord sign-in**, so players keep levels, stats, friends and clans. See Discord sign-in below.
8. **Game tables**, for missions, achievements, the battle pass and timed events. See Game tables below.
9. **Run it as a service** so it comes back after a crash or reboot (below).

If you change the ports, keep `web_port` one above `game_port`: the game expects the tournament list there.

### Running it as a service

On Linux you can use systemd so it comes back after a crash or reboot. Put this in
`/etc/systemd/system/bifrost-server.service`, changing the user and folder:

```ini
[Unit]
Description=Bifrost server
After=network-online.target
Wants=network-online.target

[Service]
User=bifrost
WorkingDirectory=/opt/bifrost-server
ExecStart=/usr/bin/python3 -m bh_server
Restart=on-failure
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
```

Then `sudo systemctl enable --now bifrost-server`. `KillSignal=SIGINT` lets it warn players before it stops. The log
is in `data/server-log.txt`. You don't get the admin console this way, so use `tmux` or `screen` if you want it. On
Windows, just leave it running in a console window.

### Updating

Stop the server, `git pull`, start it again. `config.local.json` and `data/` aren't touched.

## Settings

`config.json` holds the shared defaults. This server's own settings go in `config.local.json` (not in Git), which
overrides it. 

| Setting | What it does |
| --- | --- |
| `server_name` | the name players see for this server |
| `game_port`, `web_port`, `game_host`, `web_host` | where it listens (7910 and 7911, every address) |
| `public_url` | the sign-in page's address, as players' browsers reach it |
| `behind_proxy` | `true` if a proxy answers `public_url` on the same port number as `web_port` |
| `tls_cert`, `tls_key` | a real certificate (full chain) and its key, to make the web port https; reloaded when they change |
| `tls` | `true` makes the web port https with the server's own certificate (browsers don't trust it) |
| `discord_client_id`, `discord_client_secret` | your Discord application, for sign-in |
| `admin_discord_ids` | the Discord accounts allowed to use admin commands |
| `mod_reviewer_discord_ids` | the Discord accounts that review shared mods |
| `game_data` | the folder holding the game's `Game/` and `Init/` tables (see below) |
| `relay` | `inprocess` (the built-in P2P relay) or `native` with `relay_native` set to the relay program |
| `turn` | `off` (default) or `cloudflare`: relayed matches can go through Cloudflare's TURN servers (see below) |
| `turn_key_id`, `turn_api_token` | your Cloudflare TURN key's ID and API token, for `turn` |
| `turn_ttl` | how long each TURN login lasts, in seconds (default 7200, at most 172800) |
| `cups` | `true` turns on Cups (double elimination brackets of 8) |
| `ask_region` | `true` opens the game's region picker at login for players who have no region yet |
| `client_theme` | the menu theme the server gives every game (main menu look and buttons, login bonus frame, menu music): a theme's `ClientThemeName` from the game's `ClientThemeTypes` table (`Halloween`, `Anniversary`, `Shrek`, ...) or its id, or `auto` (default), which sends theme 0, so the game shows its Default theme. Only live themes count (not `NotLive`); the list comes from your game tables (see below), or the 11.00.49 list without them. The console's `theme` command changes it for everyone until the server restarts, and players can still pick their own in Bifrost's Options |

Environment variables override both files: `BH_DISCORD_CLIENT_SECRET`, `BH_PUBLIC_URL`, `BH_GAME_DATA`,
`BH_RELAY_SECRET` for the native relay, and `BH_TURN_KEY_ID` and `BH_TURN_API_TOKEN` for TURN.

## Cloudflare TURN (optional)

Players who aren't friends play through a relay so they never see each other's IP address. The built-in relay runs on
this server, so two players far from it get a high ping. With `"turn": "cloudflare"`, the server gives each player a
short-lived login for Cloudflare's TURN servers. Each player relays through the Cloudflare location nearest them, and
the players still only see Cloudflare's addresses. Players or networks that can't reach Cloudflare, and older Bifrost
builds, keep using this server's relay.

1. Create a TURN key in the Cloudflare dashboard (Realtime TURN). Copy its key ID and API token.
2. Put them in `config.local.json` (`turn_key_id`, `turn_api_token`) or in `BH_TURN_KEY_ID` and `BH_TURN_API_TOKEN`,
   set `"turn": "cloudflare"`, and restart. The log says `turn: Cloudflare TURN on`.

Cloudflare charges for the traffic it sends to players after a free amount each month; see Cloudflare's TURN pricing.
Cloudflare sees the IP addresses of the players it relays, as this server does for its own relay.

## Discord sign-in

Discord is optional. Without it, everyone plays as a guest: custom rooms, the queues and matches all work, and with
an up-to-date Bifrost each game keeps its own guest name and settings. Guests don't get anything tied to an account:
levels and stats, friends, clans, tournaments, uploading mods, or the save kept on the server. A whitelist also needs
Discord.

To turn it on:

1. Make an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Put its client ID and secret in `config.local.json` as `discord_client_id` and `discord_client_secret`. Use your
   own app's ID: the one in `config.json` is the Bifrost project's app and won't work with your secret. Never put the
   secret in `config.json`.
3. Set `public_url` to the address players' browsers use for your sign-in page (see below).
4. In the app's OAuth2 > Redirects, add `<public_url>/callback`. The server prints the exact URL when it starts.
5. Put your own Discord ID in `admin_discord_ids` so you can use the admin commands in game.
6. Restart the server. The log shows the redirect it expects, or `DISCORD SIGN-IN IS OFF` and why.

Once someone signs in, their account has one player, and they get that same player on any PC they sign in from.

Signing in happens in the browser: Discord sends the player back to `<public_url>/callback`, and that page asks for
the 4-digit code shown in the game. Port 7911 speaks plain http unless you give it a certificate, so pick one of
these:

- Plain http, like `http://play.example.com:7911`. Fine for an IP address or a normal domain, but not for `.app`,
  `.dev`, `.page` and a few other domains, which browsers always open over https. Those get `ERR_SSL_PROTOCOL_ERROR`.
- A certificate on the server itself, so port 7911 speaks https. With Let's Encrypt, stop anything using port 80,
  open it, and run (changing the domain and the server's folder and user):

  ```sh
  sudo certbot certonly --standalone -d play.example.com     --deploy-hook 'install -o bifrost -m 600 "$RENEWED_LINEAGE"/fullchain.pem "$RENEWED_LINEAGE"/privkey.pem /opt/bifrost-server/data/'
  ```

  The hook copies the certificate into the server's `data/` folder, where the server can read it, now and after
  every renewal. Then set:

  ```json
  "tls_cert": "data/fullchain.pem",
  "tls_key": "data/privkey.pem",
  "public_url": "https://play.example.com:7911"
  ```

  The server picks up renewed files within a minute, without a restart. Keep port 80 open so certbot can renew.
- A reverse proxy with a real certificate on port 443. With Caddy, this is the whole
  config:

  ```
  play.example.com {
      reverse_proxy 127.0.0.1:7911
  }
  ```

  Set `public_url` to `https://play.example.com` with no port, and open ports 80 and 443. Keep 7910 and 7911 open,
  since the game still connects to them directly.
- A Cloudflare Tunnel, if your domain is on Cloudflare and you'd rather not open ports. Run `cloudflared` on the
  server with its own hostname, like `login.example.com`, pointing at `http://localhost:7911`, and set `public_url`
  to `https://login.example.com`. Players still use the DNS-only name as the server address.

Don't use `https://` with port 7911 (`https://play.example.com:7911`) without `tls_cert` and `tls_key`. Without them
that port isn't https, so the browser fails with `ERR_SSL_PROTOCOL_ERROR`. The server catches this when it starts: the
log says `DISCORD SIGN-IN IS OFF`, and players who try to log in are told the server is set up wrong.

## Game tables

Daily missions, achievements, the battle pass, timed events, tournament map pools, default skins and the menu themes read the game's
own XML tables. Those are Ubisoft's data, so they are never in this repo. They're inside `Game.swz` and `Init.swz` in
your Brawlhalla install. Extract those two archives (this repo has no extractor), and put the resulting `Game/` and
`Init/` folders in `data/game/`, or point `game_data` at the folder that holds them. Without them the server still
runs, and those features have nothing to offer.

## Data

Everything the server keeps goes in `data/` (not in Git): `accounts.sqlite`, the log, backups, replays, shared mods
and bug reports. Back up that folder.

## Console

The server prints its admin commands when it starts (`say`, `who`, `kick`, `ban`, `mods`, `backup`, ...) and again
for any command it doesn't know.

## License

MIT, see [LICENSE](LICENSE).
