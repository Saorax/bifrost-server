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

## Settings

`config.json` holds the shared defaults. This server's own settings go in `config.local.json` (not in Git), which
overrides it. 

| Setting | What it does |
| --- | --- |
| `server_name` | the name players see for this server |
| `game_port`, `web_port`, `game_host`, `web_host` | where it listens (7910 and 7911, every address) |
| `public_url` | the sign-in page's address as players reach it |
| `discord_client_id`, `discord_client_secret` | your Discord application, for sign-in |
| `admin_discord_ids` | the Discord accounts allowed to use admin commands |
| `mod_reviewer_discord_ids` | the Discord accounts that review shared mods |
| `game_data` | the folder holding the game's `Game/` and `Init/` tables (see below) |
| `relay` | `inprocess` (the built-in P2P relay) or `native` with `relay_native` set to the relay program |
| `cups` | `true` turns on Cups (double elimination brackets of 8) |

Environment variables override both files: `BH_DISCORD_CLIENT_SECRET`, `BH_PUBLIC_URL`, `BH_GAME_DATA`, and
`BH_RELAY_SECRET` for the native relay.

## Discord sign-in

1. Make an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Under OAuth2, add the redirect `<public_url>/callback`, for example `https://play.example.com:7911/callback`.
3. Put its client ID in `discord_client_id` and its client secret in `config.local.json`, never in `config.json`.

Without a secret the server still runs, and players join as guests.

## Game tables

Daily missions, achievements, the battle pass, timed events, tournament map pools and default skins read the game's
own XML tables. Those are Ubisoft's data, so they are never in this repo. Put the folder holding `Game/` and `Init/`,
taken from your own Brawlhalla install, at `data/game/`, or point `game_data` at it. Without them the server still
runs, and those features have nothing to offer.

## Data

Everything the server keeps goes in `data/` (not in Git): `accounts.sqlite`, the log, backups, replays, shared mods
and bug reports. Back up that folder.

## Console

The server prints its admin commands when it starts (`say`, `who`, `kick`, `ban`, `mods`, `backup`, ...) and again
for any command it doesn't know.

## License

MIT, see [LICENSE](LICENSE).
