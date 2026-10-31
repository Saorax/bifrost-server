# Packets

Every network packet the Brawlhalla 11.00 client and Bifrost's server send each other: what it is, how it travels,
its fields in wire order, what it needs and what it gives. The game's own packets come first, by number. Bifrost's
own packets (11001 and up, which the official game doesn't have) are at the very bottom.

The game's names are obfuscated, so every packet has a descriptive name given here, followed by the game's two
names for it: the decompiler's (`var_9595`) and the original obfuscated one (`_-P6Z`). Where Bifrost's server
handles a packet, its constant name is shown too (`protocol.LOGIN_OK` means `LOGIN_OK` in `bh_server/protocol.py`).

## How packets travel

The game keeps up to four connections:

| Connection | Transport | Used for |
| --- | --- | --- |
| Lobby | TCP (`GameCore.serverConnection`) | login, menus, rooms, queues, friends, clans, stats, store, everything outside a match |
| Game server | TCP (`gameServerConnection`) | joining a match on a match server, and the match event reports (10402, 10410–10423) |
| Match | UDP (`udpConnection`) | the match itself: inputs, sync and time |
| Spectate | TCP (`GameCore.var_10051`) | watching a match: opened to the host and port that 2420 gives; carries 10100–10107 and 10200–10202 |

Bifrost's server is the lobby server. Bifrost's matches run P2P between the players (or through the server's relay)
with Bifrost's own datagrams, so its server doesn't speak the official match server packets (103xx, 104xx).

### TCP frame (big-endian)

| Field | Size | Notes |
| --- | --- | --- |
| type | u16 | the packet number; bit 15 (`0x8000`) is set when an extra u32 follows |
| extra | u32, optional | **extra u32 A** for types 1701–1999, 2001–2299 and 2401–2999; **extra u32 B** for types below 500 and 10202, 10400, 10405 (the 11.00 client always writes 0 there). Never for 16, 178, 2463 or 2467 |
| length | u16 | payload length in bytes (at most 65535) |
| payload | length bytes | the bit-packed fields below |

### UDP datagram

From the client:

| Field | Size | Notes |
| --- | --- | --- |
| prefix | u8 | always 0 |
| player id | u32 | the sender's id in the match |
| session | u16 | the match session id the match setup gave (10310 / 10306) |
| reliable | u8 | 0 for a single message, 1 for a reliable bundle |
| type | u16 | the packet number (single message) |
| length | u16 | payload length in bytes |
| payload | length bytes | the bit-packed fields |

A reliable bundle (reliable = 1) carries, after the reliable byte, a u16 first sequence number and a u16 count, then
each message as u16 type, u16 length and payload. The client acknowledges reliable messages with type 10 (a `small`
holding the highest sequence number it has). Datagrams from the server start with u8 0 and the reliable byte, then the
same layout without the player id and session. 10316 carries the same kind of bundle over TCP.

Bifrost's P2P matches use their own datagrams between the players instead (see the bottom of this file).

### Field encoding

The payload is a bit stream, written most significant bit first, with no padding between fields. The field types
used in every table below:

| Type | On the wire |
| --- | --- |
| `bool` | 1 bit |
| `byte` | 8 bits |
| `uint` | a 4-bit prefix `k`, then `2(k+1)` bits: an unsigned number of up to 32 bits, taking only the bits it needs |
| `small` | a 3-bit prefix `k`, then `2(k+1)` bits: up to 16 bits |
| `u64` | a 5-bit prefix `k`, then `2(k+1)` bits: up to 64 bits |
| `f32` | 32 bits, IEEE float |
| `string` | 16 bits of byte length, then that many UTF-8 bytes |
| `raw n` | n bytes, not aligned to a byte boundary |

Lists are either a count (`uint`) followed by that many items, or a `bool` "another item follows" before each item
and a `false` after the last. Each entry says which.

### Numbering

The game numbers its packets with a counter in groups, so the numbers jump between ranges. Roughly:

| Range | What's in it |
| --- | --- |
| 16–20 | handshake and the first login |
| 30–196 | client → lobby server requests |
| 500, 1700, 2000, 2300 | single markers between groups |
| 2400–2553 | lobby server → client replies and pushes |
| 2700–2959 | further server → client groups (clans, friends, events, ...) |
| 3000 | a marker |
| 10100–10423 | match servers and match traffic |
| 12000–12003 | login challenge |
| 12100–12101 | keep-alive |
| 11001–11099 | **Bifrost only** (bottom of this file) |

## Connecting and logging in (Bifrost's server)

1. The client opens the lobby TCP connection and sends **178** (protocol hello: the string "Brawlhalla client to
   server protocol 1.0") and **30** (its version).
2. The server answers 30 with **12000** (the login challenge string).
3. The client sends **20** (login: names, the Steam ticket, platform, version). Bifrost ignores the Steam ticket.
   A Bifrost client signed in to the server first sends **11001** (its account token) so the login lands on the
   right account.
4. The server answers with **2431** (login accepted: the player, level, settings, queues, ratings), then **2528**
   (feature flags) and whatever else the player needs at login (perks, friends, clans, missions, battle pass,
   achievements, notices).
5. Both sides send **12100** (keep-alive) while connected.

The rest of this file is the reference, one entry per packet.

## Game packets

### 16 · Server message popup
`var_9595` / `_-P6Z` · server → client · TCP lobby · no extra u32 (the frame code leaves it out for this type) · Bifrost: `game.SERVER_MESSAGE`

The server shows the player a message. GameCore.method_3707 shows the text in a popup. A few texts get special handling. "Incorrect Version, Please Update" (with or without a full stop) opens the version screen and disconnects. "Server is restarting, please wait!" sets a flag that keeps that popup showing. The cross-progression link keys are shown as a popup with extra flags.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | text | string | message or localization key to show |
| 2 | disconnect | bool | true: drop the lobby connection after showing it |

**Needs:** an open lobby connection.
**Gives:** a popup; may disconnect the client.
**Bifrost's server:** `bh_server/game.py` `restart_packet` sends it with "Server is restarting, please wait!" and false when the server announces a restart. Sign-out notices use 2465 instead.

### 17 · Unknown (string to lobby, dead sender)
`var_2186` / `_-N5V` · client → server · TCP lobby · extra u32 B · Bifrost: none

GameCore.method_388 builds this packet with one string, but nothing in 11.00 calls that method.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | text | string | (unclear) |

**Needs:** nothing calls it in 11.00.
**Gives:** nothing known.
**Bifrost's server:** not handled.

### 18 · Back from game server (unclear)
`var_5067` / `_-J50` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent on the next frame after the client gets packet 10311 (handled by LinkUpdater.method_8750). That handler stores a string from 10311 and raises a flag. GameCore.method_3202 then sends that string with the player's own id to the lobby server. If the lobby connection is down, the client starts its reconnect path instead.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the player's own id |
| 2 | text | string | the string from 10311. The 2434/2466 match-start packets keep a game-server string in the same variable (unclear) |

**Needs:** packet 10311 received.
**Gives:** (unclear) tells the lobby server what the game server reported.
**Bifrost's server:** not handled.

### 19 · Unused
`PKTTYPE_UNUSED_19` / `PKTTYPE_UNUSED_19` · not sent or handled by the 11.00 client or Bifrost's server

### 20 · Login
`var_15185` / `_-V3R` · client → server · TCP lobby · extra u32 B · Bifrost: `protocol.LOGIN`

Sent by GameCore.method_4700 once the lobby connection is up and the 12000 challenge has arrived, with either Steam login data or saved credentials. On Steam the client first gets an encrypted app ticket (GameCore.method_3007). Then the password proof is left empty.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | account name | string | e-mail or account name for a credential login. Empty or unset on Steam |
| 2 | password proof | string | each hex digit of the stored password hash XORed with the matching digit of the 12000 challenge, in lowercase hex. Empty on Steam logins |
| 3 | (unused) | string | never assigned in 11.00, always empty |
| 4 | persona name | string | Steam display name |
| 5 | ticket length | uint | number of bytes that follow |
| 5.1 | ticket byte | byte | Steam encrypted app ticket, repeated field-5 times (the client drops one byte from its end first) |
| 6 | persona id | string | Steam id |
| 7 | platform | byte | 2 on Mac, 1 otherwise |
| 8 | constant | uint | always 36. Bifrost logs it as "version" |
| 9 | has manifest | bool | true once the client got its file-manifest packet |
| 10 | checksum | string | SHA-256 of "11000" followed by the manifest file hashes. Empty if there is no manifest |

**Needs:** 178 Hello and 30 Version sent, then 12000 challenge received.
**Gives:** the server answers with 2431 Login accepted.
**Bifrost's server:** `bh_server/protocol.py` `Session.on_packet` → `read_login`. It ignores the ticket and the hash, and makes a player key from the persona id (a guest key if there is none). It sends 2431 (`login_ok`), then 2528 feature flags, optionally 11093, 11013 notices and 11090 perks. Then it runs every module's after-login step (picks, stats, reports, social, clans, missions, battle pass, rejoin, ...) and may send 11010.

### 30 · Version
`var_1831` / `_-RI` · client → server · TCP lobby · extra u32 B · Bifrost: `protocol.VERSION`

GameCore.onServerConnected sends this right after the 178 Hello string.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | build | uint | 0x4190AB00 (1100000000) ORed with a 16-bit running checksum of the game SWF's bytes |
| 2 | platform | uint | platform id (GameCore field, 1 by default) |

**Needs:** TCP connect and 178 Hello.
**Gives:** the server sends the 12000 login challenge.
**Bifrost's server:** `bh_server/protocol.py` `Session.on_packet` treats a build of 1100000000 or more as an 11.00 client and anything lower as 10.11, and replies with 12000 (CHALLENGE).

### 31 · Ready for battle (queue)
`var_664` / `_-L9` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.READY_QUEUE`

Sent from a queue lobby when the player presses start (Class_113.method_8101, reached from method_8287 in lobby mode 1). It is not sent again while already searching. The client also remembers the playlist as the last one played.

No payload.

**Needs:** in a queue lobby (after 33 Create with a playlist) and connected.
**Gives:** matchmaking search starts.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle`. It is accepted when the sender is host, the party size fits the playlist and everyone has locked in. It replies 2472 to the party with accepted true/false and runs the matchmaker. Gameplay mods refuse it with 11014. After a matched game it can instead start a rematch (`back_from_match`).

### 32 · Cancel queue
`var_12169` / `_-p12` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.CANCEL_QUEUE`

Sent when the player leaves the matchmaking wait (Class_113.method_6929 from the waiting-queue screen, Class_574, or GameCore). It is skipped for rematches.

No payload.

**Needs:** searching after 31.
**Gives:** search stops. The party stays together and is un-readied.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` → `rooms.cancel_search`, sends 2428 (false) and 2445 to the party. Outside a queue it sends 2412 to the sender. It does nothing during a rematch.

### 33 · Create room / enter queue
`var_5994` / `_-Y3S` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.CREATE`

LinkUpdater.method_963 asks the server for a new lobby. For a queue the client sends a playlist. For a custom room it sends a custom game type: the "Create room" menu (Class_503), the custom-game options (Class_504) and the queue picker (Class_113.method_4496, Class_574).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | flags | uint | 14 (6\|8) for queues and private rooms, 15 for a public custom room (bit 1 = public) |
| 2 | playlist id | uint | queue playlist, 0 for a custom room |
| 3 | custom type id | uint | custom game type, 0 for a queue |
| 4 | (unclear) | uint | 0 from the menus. Bifrost logs it as a region |
| 5 | (unclear) | bool | true from every menu caller |
| 6 | (unclear) | bool | caller-supplied, false from the custom-room menu |

**Needs:** logged in (2431). Bifrost also needs its own 11003 room defaults before it creates a custom room.
**Gives:** a new room, which the server sends as 2445.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle`. Flags 14 with a known playlist make a queue room and send 2445. Flags 14/15 with playlist 0 make a custom room from the 11003 defaults and send 2445. Anything else gets 2446. `social.lobby_packet` then brings the leader's party along.

### 34 · Invite player
`var_14606` / `_-J19` · client → server · TCP lobby · extra u32 B · Bifrost: `social.INVITE`

Invites a friend to the sender's room, from the friends list (Class_208) or another social-hub list (Class_210).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | who to invite |

**Needs:** in a room.
**Gives:** the target gets an invite.
**Bifrost's server:** `bh_server/social.py` `_dispatch`. A host's invite sends 2439 to the target and 2440 to the room. A non-host's invite goes to the host as a suggestion (2441). Failures send 2404 with a reason code.

### 35 · Accept invite
`var_2757` / `_-Rf` · client → server · TCP lobby · extra u32 B · Bifrost: `social.INVITE_ACCEPT`

Accepts a pending invite. The invite popup (Class_436 "Yes" button) sends only field 1. The social-hub path (LinkUpdater.method_2616) also sends field 2.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | invite id | uint | id from the 2439 invite |
| 2 | playlist | bool + uint | signed number (sign bit, then size): the invite's playlist id, or -1. Only sent by LinkUpdater.method_2616 |

**Needs:** a 2439 invite received.
**Gives:** the player moves into the inviter's room.
**Bifrost's server:** `bh_server/social.py` `_accept`. It moves the player into the room (2445) and sends 2442 to the room. A friend-request invite is accepted as a friendship instead. A missing invite or a full room gets 2404 (plus a notice).

### 36 · Decline invite
`var_12780` / `_-d0` · client → server · TCP lobby · extra u32 B · Bifrost: `social.INVITE_DECLINE`

Declines a pending invite from the invite popup's close button or the social hub.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | invite id | uint | id from the 2439 invite |

**Needs:** a 2439 invite received.
**Gives:** the invite is dropped.
**Bifrost's server:** `bh_server/social.py` `_dispatch` deletes the invite and sends 2443 to the inviting room. For a friend request it declines the friendship.

### 37 · Room settings
`var_4119` / `_-m10` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.SETTINGS`

LinkUpdater.method_893 is sent by the host of an online lobby when they confirm the lobby settings screen (Class_451).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | playlist id | uint | 0 for a custom room |
| 2 | custom type id | uint | custom game type |
| 3 | (unclear) | small | only if field 1 is 0. The client defaults it to 4 |
| 4 | game rules | 15 × uint | only if field 1 is 0. The Class_174 rule values in fixed order. The first is a flags word (bit 1 = teams), and match duration is one of the rest |
| 5 | flags | uint | room flags, bit 1 = public |
| 6 | region | byte | server region id |
| 7 | description | string | room description |
| 8 | map selection mode | small | index of the level-select mode (random, voting, striking, ...) |
| 9 | (unclear) | bool | room-state flag, reset on most mode changes |
| 10 | handicaps | bool | handicaps allowed |

**Needs:** host of an online room.
**Gives:** new settings for everyone in the room.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` → `read_settings`. It is host only. For a custom room it stores the settings and sends 2448 to the room. In a tournament room it refuses and echoes 2448 back.

### 38 · Join room
`var_10704` / `_-46X` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.JOIN`

LinkUpdater.method_7545 joins a room by number. It is sent from the public games list, the room-code screen and the "room full" prompt.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | room number | uint | room to join |
| 2 | from list | bool | true from the public games list. False from the room-code screen and the room-full prompt. Bifrost reads it as "ask before spectating if full" |
| 3 | (unclear) | bool | true from every menu caller |

**Needs:** logged in.
**Gives:** the player joins the room, or is offered spectating.
**Bifrost's server:** `bh_server/social.py` `lobby_packet` refuses rooms that banned the player (2446). Otherwise `bh_server/lobby.py` `join_room` sends 2445 to the joiner, 2449 to the others and 2451 ratings. A full custom room gets 2447 (offer to spectate), or a direct spectate if field 2 is false. Other failures get 2446.

### 39 · Kick player
`var_8717` / `_-S6N` · client → server · TCP lobby · extra u32 B · Bifrost: `social.KICK`

The host kicks a player from the lobby screen or the social hub's lobby list.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | who to kick |
| 2 | sub-slot | uint | that player's local slot (for couch guests) |

**Needs:** host of the room.
**Gives:** the player is removed.
**Bifrost's server:** `bh_server/social.py` `_kick` (reads only field 1). It sends 2453 to the kicked player, or 2444 to the room if they are offline.

### 40 · Ban player from room
`var_6227` / `_-W31` · client → server · TCP lobby · extra u32 B · Bifrost: `social.BAN`

Like 39, but the player also can't rejoin this room.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | who to ban |

**Needs:** host of the room.
**Gives:** the player is removed and barred from the room.
**Bifrost's server:** `bh_server/social.py` `_kick` with ban, which records the ban for the room number and sends 2453. Later 38 joins are refused.

### 41 · Lock in loadout
`var_5712` / `_-G2M` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.LOADOUT`

Sent from character select (Class_543) when a player or the host's bot locks in, in online lobby modes. The client does not send it when the rules ask for more than 5 legends.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | is bot | bool | locking in a bot |
| 2 | slot | uint | bot index if field 1, else the player's local sub-slot |
| 3 | colour | uint | colour scheme id |
| 4 | (unclear) | bool | caller flag |
| 5 | sidekick | uint | sidekick (SpawnBot) id |
| 6 | (unclear) | uint | loadout value. Bifrost keeps fields 5-7 as "extra" |
| 7 | emitter | uint | emitter-group cosmetic id (unclear) |
| 8 | legend count | uint | number of legends that follow (at most 5) |
| 8.1 | random | bool | random pick |
| 8.2 | 16-bit id | bool | true when the legend id fits in 16 bits |
| 8.3 | legend id | uint | low 16 bits of the legend id. Add 65536 when 8.2 is false |
| 8.4 | skin | uint | costume id |
| 8.5 | (unclear) | uint | Bifrost: p8 |
| 8.6 | weapon skins | uint | weapon skin value |

**Needs:** in an online room (2445).
**Gives:** the player shows as ready with that loadout.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` → `read_loadout`. It sends 2437 to the room. It refuses legends that are switched off (2458) and handles bot lock-ins (`bot_loadout`) and rematches. Outside a room it sends 2456.

### 42 · Change team
`var_2647` / `_-64m` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.TEAM`

Sent from the party screen (Class_510) when a team is changed in an online custom room with teams on.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | is bot | bool | changing a bot's team |
| 2 | bot slot | uint | only if field 1 is true: bot index |
| 3 | player id | uint | only if field 1 is false |
| 4 | sub-slot | uint | only if field 1 is false: local slot |
| 5 | team | uint | new team (0, 1 or 2) |

**Needs:** online custom room with teams on. The host may move anyone, and others only themselves.
**Gives:** the new team for everyone.
**Bifrost's server:** `bh_server/lobby.py` `change_team` sends 2437 to the others.

### 43 · Un-ready
`var_14790` / `_-T6b` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.UNREADY`

Undoes a lock-in from character select (Class_543) or the lobby state (Class_113).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | is bot | bool | un-readying a bot |
| 2 | slot | uint | bot index if field 1, else local sub-slot |
| 3 | (unclear) | bool | true from character select, false from Class_113 |

**Needs:** locked in (41).
**Gives:** not ready.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` sets ready to 0 and sends 2437 to the room. It ignores the fields.

### 44 · Add bot or local guest
`var_3833` / `_-94L` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.ADD_BOT`

The party screen sends it with true to add a bot (LinkUpdater.method_6439). Spectator/lobby input sends it with false to add a couch guest (method_7985).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | is bot | bool | true: bot, false: local guest |
| 2 | value | uint | bot level if field 1 is true, else the guest's input device id |

**Needs:** in an online room (host for bots).
**Gives:** a new bot or guest slot.
**Bifrost's server:** `bh_server/lobby.py` `manage_party` adds a bot and sends 2449. It ignores guests.

### 45 · Remove bot or local guest
`var_1919` / `_-565` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.REMOVE_BOT`

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | is bot | bool | true: remove a bot |
| 2 | guest slot | uint | only if field 1 is false: which local guest leaves |

**Needs:** a bot or guest in the room.
**Gives:** the slot is removed.
**Bifrost's server:** `bh_server/lobby.py` `manage_party` removes the last bot and sends 2444 (bot form). It ignores guests.

### 46 · Set bot level
`var_8815` / `_-V4i` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.BOT_LEVEL`

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | slot | uint | room slot of the bot (players first, then bots) |
| 2 | level | uint | bot difficulty |

**Needs:** host with a bot in the room.
**Gives:** the bot's new level.
**Bifrost's server:** `bh_server/lobby.py` `manage_party` sends 2461 to the room.

### 47 · Opened settings from character select
`var_12346` / `_-q5f` · client → server · TCP lobby · extra u32 B · Bifrost: `reports.OPENED_SETTINGS`

Character select (Class_543.method_8534) sends this when the player opens the settings screen while in an online lobby (mode 1 or 4).

No payload.

**Needs:** in an online lobby.
**Gives:** nothing to the client.
**Bifrost's server:** `bh_server/reports.py` `Desk.handle` logs it only.

### 48 · Unused
`var_6866` / `_-y5t` · not sent or handled by the 11.00 client or Bifrost's server

### 49 · Arcade run result
`var_4578` / `_-G5E` · client → server · TCP lobby · extra u32 B · Bifrost: `stats.ARCADE_RESULT`

Class_201.method_1044 reports a finished arcade run. If the client is offline, the result is queued (Class_52) and sent after it reconnects.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | mode | uint | arcade mode id |
| 2 | legend | uint | legend id |
| 3 | score | uint | total score |
| 4 | placement | uint | Bifrost's name for this field |
| 5 | rounds | 3 × group | always three rounds, no count |
| 5.1 | kills | uint | |
| 5.2 | lives left | uint | |
| 5.3 | match time | uint | |
| 5.4 | kill points | uint | kills × 5000 |
| 5.5 | life points | uint | lives × 10000 |
| 5.6 | time points | uint | (3000 − time/100) × 20, floor 0 |

**Needs:** logged in.
**Gives:** nothing to the client.
**Bifrost's server:** `bh_server/stats.py` `Desk.handle` stores the run as an arcade record for signed-in accounts.

### 50 · Unknown record (dead sender)
`var_14772` / `_-ei` · client → server · TCP lobby · extra u32 B · Bifrost: none

Class_201.method_4703 updates a local best-value table (it keeps the lower third value) and sends the three values. Nothing calls it in 11.00.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | key 1 | uint | (unclear) |
| 2 | key 2 | uint | (unclear) |
| 3 | value | uint | (unclear) lower is better |

**Needs:** nothing calls it in 11.00.
**Gives:** nothing known.
**Bifrost's server:** not handled.

### 51 · Buy store item
`var_8638` / `_-45W` · client → server · TCP lobby · extra u32 B · Bifrost: none

LinkUpdater.method_6947 buys a store item. GameCore.method_2809, method_6317, method_5531 and method_2704 send it for different payment kinds, and ClientManager also sends it.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | item id | uint | store item id |
| 2 | payment kind | uint | 1 = the item's main price (sale price if on sale), 3 and 4 = two other currencies the client checks against its balances, or the item's own special currency id |
| 3 | variant | uint | item variant (for a "PeekableColor" item: the chosen colour) |
| 4 | on sale | bool | the item is marked on sale |

**Needs:** connected, and the client thinks the player can afford it.
**Gives:** a purchase on the official server.
**Bifrost's server:** not handled.

### 52 · Refund store item
`var_7862` / `_-H44` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent when the player confirms the refund prompt (Class_532, "a_ScreenRefundPrompt").

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | item id | uint | store item id |
| 2 | variant | uint | item variant |

**Needs:** refund list from 95.
**Gives:** a refund on the official server.
**Bifrost's server:** not handled.

### 53 · Rejoin match
`var_6771` / `_-c6Q` · client → server · TCP lobby · extra u32 B · Bifrost: `rejoin.REJOIN`

The "Rejoin" button on the rejoin prompt (Class_535). The method calls sendToServer twice, so the packet goes out twice.

No payload.

**Needs:** the 2477 rejoin prompt.
**Gives:** the player goes back into the match they dropped from.
**Bifrost's server:** `bh_server/rejoin.py` `handle`. It finds the held seat and either rebuilds the player's seat or spectates the room (2445, a late ticket 2420). It ignores the second copy and sends 11013 if the match ended.

### 54 · Decline rejoin
`var_6739` / `_-W7` · client → server · TCP lobby · extra u32 B · Bifrost: `rejoin.DECLINE`

The "Decline" button on the rejoin prompt.

No payload.

**Needs:** the 2477 rejoin prompt.
**Gives:** the seat is given up.
**Bifrost's server:** `bh_server/rejoin.py` `handle` forgets the held seat.

### 55 · Start match (custom room)
`var_7053` / `_-Z4w` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.START`

Class_113.method_8287 sends it once when the host starts an online custom room (lobby mode 4).

No payload.

**Needs:** host of an online custom room, players locked in.
**Gives:** level select or the match starts.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` → `rooms.start`. It opens level select (2460) when the map mode needs it, otherwise `send_start` (11005 + 2401). Content-mod mismatches get 11014.

### 56 · Level select closed
`var_4639` / `_-C5v` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.LEVEL_DONE`

ScreenLevelSelect.method_1172 sends it when the level select closes in an online custom room (timer done or closed).

No payload.

**Needs:** level select open (2460).
**Gives:** the vote is wrapped up.
**Bifrost's server:** `bh_server/lobby.py` `level_select`. For the host it finishes an "all but X" pick, otherwise it returns to the lobby (`level_select_back`).

### 57 · Level vote / strike
`var_2503` / `_-43x` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.LEVEL_VOTE`

Sent from ScreenLevelSelect when a player votes for or strikes a level.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | slot | uint | voter's local sub-slot |
| 2 | level id | uint | level voted or struck |
| 3 | all but | bool | "all but this" vote |

**Needs:** level select open.
**Gives:** the vote is shown to everyone.
**Bifrost's server:** `bh_server/lobby.py` `level_select` → `mappick.vote`. It sends 2459 to the room and finishes the pick (2424) when it is complete.

### 58 · Done striking
`var_13342` / `_-h44` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.LEVEL_STRIKE_DONE`

ScreenLevelSelect.method_5545 sends it in the striking map modes when a player has finished striking, in online lobbies.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | slot | uint | the player's local sub-slot |

**Needs:** a striking level select open.
**Gives:** that player's turn is done.
**Bifrost's server:** `bh_server/lobby.py` `level_select` → `mappick.done`. It sends 2425 and finishes the pick when complete.

### 59 · Toggle sidekick
`var_3002` / `_-56J` · client → server · TCP lobby · extra u32 B · Bifrost: `picks.SETS` (sidekick)

Class_744.method_1280 adds a sidekick (SpawnBot) to, or removes it from, the player's selected set. The inventory, battle-pass and store screens call it. Types 60, 61, 63 and 65 work the same way.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | item id | uint | sidekick id |
| 2 | only this | bool | clear the set and select just this item |
| 3 | selected | bool | item is in the set after the change |

**Needs:** owning the item (or removing it).
**Gives:** the player's sidekick set.
**Bifrost's server:** `bh_server/picks.py` `apply` stores the set in the player's profile picks.

### 60 · Toggle KO effect
`var_14505` / `_-q1I` · client → server · TCP lobby · extra u32 B · Bifrost: `picks.SETS` (koEffect)

Same as 59 for KO effects (Class_674).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | item id | uint | KO effect id |
| 2 | only this | bool | clear the set and select just this item |
| 3 | selected | bool | item is in the set after the change |

**Needs:** owning the item (or removing it).
**Gives:** the player's KO effect set.
**Bifrost's server:** `bh_server/picks.py` `apply` stores it.

### 61 · Toggle player theme
`var_7973` / `_-z4q` · client → server · TCP lobby · extra u32 B · Bifrost: `picks.SETS` (theme)

Same as 59 for player themes (Class_305).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | item id | uint | player theme id |
| 2 | only this | bool | clear the set and select just this item |
| 3 | selected | bool | item is in the set after the change |

**Needs:** owning the item (or removing it).
**Gives:** the player's theme set.
**Bifrost's server:** `bh_server/picks.py` `apply` stores it.

### 62 · Set emote slot
`var_6554` / `_-6M` · client → server · TCP lobby · extra u32 B · Bifrost: `picks.TAUNT_SLOT`

Class_744.method_7374 puts a taunt in one of the 8 emote slots, or clears it with 0.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | slot | uint | 0-7 |
| 2 | taunt id | uint | taunt, 0 = empty |

**Needs:** owning the taunt.
**Gives:** the emote wheel slot.
**Bifrost's server:** `bh_server/picks.py` `apply` stores it.

### 63 · Toggle taunt in set
`var_14931` / `_-N5D` · client → server · TCP lobby · extra u32 B · Bifrost: `picks.SETS` (victory)

Same as 59 for a set of taunts (Class_653). Bifrost's picks module calls this set "victory".

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | item id | uint | taunt id |
| 2 | only this | bool | clear the set and select just this item |
| 3 | selected | bool | item is in the set after the change |

**Needs:** owning the item (or removing it).
**Gives:** the player's taunt set.
**Bifrost's server:** `bh_server/picks.py` `apply` stores it.

### 64 · Set avatar
`var_12234` / `_-d65` · client → server · TCP lobby · extra u32 B · Bifrost: `picks.AVATAR`, `clans.AVATAR`

Class_744.method_5974 sets the player's avatar, or a clan's emblem when field 2 is true. For the player's own avatar, the client also updates the lobby slots.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | avatar id | uint | avatar |
| 2 | for clan | bool | true: set the clan emblem instead |

**Needs:** owning the avatar. For a clan: officer or leader.
**Gives:** new avatar or clan emblem.
**Bifrost's server:** `bh_server/picks.py` `apply` stores the player's avatar. `bh_server/clans.py` `_emblem` stores the clan emblem and sends 2498 to the clan.

### 65 · Toggle podium
`var_13143` / `_-y3T` · client → server · TCP lobby · extra u32 B · Bifrost: `picks.SETS` (podium)

Same as 59 for podiums (Class_310).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | item id | uint | podium id |
| 2 | only this | bool | clear the set and select just this item |
| 3 | selected | bool | item is in the set after the change |

**Needs:** owning the item (or removing it).
**Gives:** the player's podium set.
**Bifrost's server:** `bh_server/picks.py` `apply` stores it.

### 66 · Set title
`var_14375` / `_-o52` · client → server · TCP lobby · extra u32 B · Bifrost: `picks.TITLE`

Class_744.method_2910 sets the title shown under the player's name. 0 clears it.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | title id | small | title (moniker) id, 0 = none |

**Needs:** owning the title.
**Gives:** the new title.
**Bifrost's server:** `bh_server/picks.py` `apply` stores it.

### 67 · Open chest
`var_7067` / `_-03H` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent from the chest carousel (Class_408, "a_ScreenBoxCarousel") when the player opens a chest. It starts the opening animation.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | chest id | uint | ChanceBoxID |
| 2 | clock | uint | a seconds clock the client keeps advancing (GameCore field). Purpose unclear |

**Needs:** connected and owning a chest.
**Gives:** chest contents from the official server.
**Bifrost's server:** not handled.

### 68 · Buy Mammoth Coins (Steam)
`var_12145` / `_-HN` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent from the coin purchase screen (Class_412, "a_ScreenBuyIdols") and Class_21. It needs the Steam overlay ("Please Enable The Steam Overlay" otherwise).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | item id | uint | coin pack item id |

**Needs:** connected, Steam overlay on.
**Gives:** a Steam purchase started by the official server.
**Bifrost's server:** not handled.

### 69 · Steam purchase answer
`var_4725` / `_-53c` · client → server · TCP lobby · extra u32 B · Bifrost: none

GameCore.method_5859 sends it when Steam reports the result of an in-game purchase (Steam response kind 2) and passes on the order.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | order id | string | Steam order id |
| 2 | authorized | bool | the player approved the purchase |

**Needs:** a purchase started by 68.
**Gives:** the official server can finish the order.
**Bifrost's server:** not handled.

### 70 · Steam overlay state
`var_9787` / `_-e5r` · client → server · TCP lobby · extra u32 B · Bifrost: `rejoin.OVERLAY`

GameCore.method_7404 sends it at most once, after 5 s of run time, if connected and its "sent" flag is not set yet. GameCore.onServerConnected also sets that flag, so when it is sent is unclear. Right after, the client reads saved crash logs and sends them as 87.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | overlay on | bool | Steam overlay is enabled |

**Needs:** connected.
**Gives:** nothing to the client.
**Bifrost's server:** `bh_server/rejoin.py` `handle` uses it as a trigger to send a pending 2477 rejoin prompt 2 s later. It does not consume the packet.

### 71 · Unused
`PKTTYPE_UNUSED_71` / `PKTTYPE_UNUSED_71` · not sent or handled by the 11.00 client or Bifrost's server

### 72 · Unknown room request (deferred action 13)
`var_4354` / `_-tM` · client → server · TCP lobby · extra u32 B · Bifrost: none

LinkUpdater.method_1286 is only called from the after-reconnect action queue (Class_52, action 13). Nothing in 11.00 queues that action, so the packet is not sent in practice.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | number | uint | queued value, probably a room number (unclear) |
| 2 | (unclear) | bool | always true |
| 3 | (unclear) | bool | caller flag |

**Needs:** never queued in 11.00.
**Gives:** unknown.
**Bifrost's server:** not handled. (The `swfcheck` constant with this number is a SWF tag, not this packet.)

### 73 · Redeem code
`var_13534` / `_-WA` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent from the redeem-code screen (Class_531) with the typed code.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | code | string | the code (empty if the box still shows its placeholder) |

**Needs:** connected ("Error_LOST_CONNECTION_DURING_SELECT" otherwise).
**Gives:** the official server redeems it.
**Bifrost's server:** not handled.

### 74 · Unused
`var_15540` / `_-C5M` · not sent or handled by the 11.00 client or Bifrost's server

### 75 · Unused
`var_8087` / `_-M1F` · not sent or handled by the 11.00 client or Bifrost's server

### 76 · Channel chat
`var_7619` / `_-bP` · client → server · TCP lobby · extra u32 B · Bifrost: `social.CHANNEL_CHAT`

Sent from the chat box (Class_414) when the player types into a chat channel rather than the party chat.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | channel id | uint | the channel |
| 2 | text | string | message |

**Needs:** being in a channel ("You are not in any channels." otherwise).
**Gives:** the line goes to the channel.
**Bifrost's server:** `bh_server/social.py` `_dispatch` → `_general_say` for channel 1 (General). It joins the channel if needed (2417/2416) and sends 2418 to the members. Other channels are ignored.

### 77 · Party chat
`var_2895` / `_-K2Z` · client → server · TCP lobby · extra u32 B · Bifrost: `social.PARTY_CHAT`

A chat line to the current lobby, from the chat box.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | sender's own id |
| 2 | sub-slot | byte | sender's local slot |
| 3 | text | string | message |

**Needs:** in a room.
**Gives:** the line shows for everyone in the room.
**Bifrost's server:** `bh_server/social.py` `_chat` sends 2413 to the room. Lines starting with "!" or "/" run server chat commands. Server-muted players are dropped.

### 78 · Typing indicator
`var_1192` / `_-N0` · client → server · TCP lobby · extra u32 B · Bifrost: `social.TYPING`

The chat box sends true when the player starts typing and false when they stop.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | typing | bool | is typing |

**Needs:** in a room.
**Gives:** the typing mark for others.
**Bifrost's server:** `bh_server/social.py` `_typing` sends 2414 to the room, skipping players who muted the sender.

### 79 · Leave lobby (spectator)
`var_8954` / `_-4z` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.LEAVE_LOBBY`

Sent when a lobby spectator (lobby role 1) leaves. It is also sent with 81 when a public room's host goes idle for 5 minutes.

No payload.

**Needs:** in a room.
**Gives:** the player is out of the room.
**Bifrost's server:** `bh_server/lobby.py` `leave(notify_self=True)` sends 2412 to the sender and updates the room. `social.lobby_packet` may drop the player from their party.

### 80 · Spectator wants to play
`var_12916` / `_-T2n` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.TO_PLAYER`

Class_113.method_1303 sends it when a lobby spectator presses join and the room has a free slot ("Error_GAME_FULL" otherwise).

No payload.

**Needs:** spectating an online lobby.
**Gives:** the spectator takes a player slot.
**Bifrost's server:** `bh_server/lobby.py` `spectator_plays` moves them to the players and sends 2449 to the room.

### 81 · Leave lobby (player)
`var_13069` / `_-d1A` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.LEAVE_SLOT`

Sent when a player in a slot (lobby role 2) leaves the room, or when the client finds itself missing from the room's slots. If the send fails the client goes back on its own.

No payload.

**Needs:** in a room as a player.
**Gives:** the player is out of the room.
**Bifrost's server:** `bh_server/lobby.py` `leave(notify_self=True)`, same as 79.

### 82 · Make leader
`var_3508` / `_-R2B` · client → server · TCP lobby · extra u32 B · Bifrost: `social.MAKE_LEADER`

The host hands the room to another player.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | new host |

**Needs:** host of the room.
**Gives:** a new host.
**Bifrost's server:** `bh_server/social.py` `_make_leader` sends 2455 to the room and moves the party leadership.

### 83 · Spectate friend
`var_2884` / `_-O5L` · client → server · TCP lobby · extra u32 B · Bifrost: `social.SPECTATE_FRIEND`

Sent from the friends list (Class_208). On some builds the friend's id is also copied to the clipboard.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the friend |
| 2 | (unclear) | bool | always true |

**Needs:** the friend is online in a room.
**Gives:** the player spectates the friend's room.
**Bifrost's server:** `bh_server/social.py` `_to_friend`. It spectates the friend's custom room (2445, and a late ticket if the room has started). Otherwise it sends 2404, or 2403 if the room is private.

### 84 · Join friend
`var_3641` / `_-F4o` · client → server · TCP lobby · extra u32 B · Bifrost: `social.JOIN_FRIEND`

Sent from the friends list. The client may show another screen instead in some states.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the friend |
| 2 | (unclear) | bool | always true |

**Needs:** the friend is online in a room.
**Gives:** the player joins the friend's room.
**Bifrost's server:** `bh_server/social.py` `_to_friend` moves the player into the room and joins the friend's party. It sends 2404/2403 on failure.

### 85 · Leave match
`var_5705` / `_-o9` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.LEAVE_MATCH`

Sent from the rewards/scoreboard screen (Class_541) when the player leaves after an online match, and by GameCore.method_8665 when returning to the menu from one.

No payload.

**Needs:** an online match or its end screen.
**Gives:** the player is out of the match's room.
**Bifrost's server:** `bh_server/lobby.py` `leave_match`. It marks "no rematch" (2542), leaves a matched room (2429, 2474 to the others) and always replies 2402.

### 86 · Unused
`PKTTYPE_UNUSED_85` / `PKTTYPE_UNUSED_85` · not sent or handled by the 11.00 client or Bifrost's server

### 87 · Client error report
`var_12143` / `_-c3G` · client → server · TCP lobby · extra u32 B · Bifrost: `reports.CRASH`

GameCore.method_7211 sends error text. On an uncaught error while connected it sends the text live. Saved error logs from earlier runs (the file cdsnt.dat) are sent after 70 with field 2 false. The same text is never sent twice in a row.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | text | string | error text with version and platform prefix |
| 2 | live | bool | true: happened now, false: saved earlier |

**Needs:** connected.
**Gives:** nothing to the client.
**Bifrost's server:** `bh_server/reports.py` `Desk.handle` logs it and keeps up to 30 per session in `client_reports`.

### 88 · Login (credential variant)
`var_2077` / `_-RA` · client → server · TCP lobby · extra u32 B · Bifrost: `protocol.LOGIN_ALT`

Same layout as 20. GameCore.method_4700 picks this type instead of 20 for a credential login with Steam not running. That login is set up by GameCore.method_5015, which nothing calls in 11.00, so the client always sends 20.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1-10 | (as 20) | | same fields as 20 |

**Needs:** as 20.
**Gives:** as 20.
**Bifrost's server:** `bh_server/protocol.py` treats it exactly like 20.

### 89 · Report player
`var_5556` / `_-lx` · client → server · TCP lobby · extra u32 B · Bifrost: `reports.REPORT_PLAYER`

Sent from the report popup (Class_537) with the chosen reason.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | reporter | uint | sender's own id |
| 2 | reported | uint | reported player id |
| 3 | reason | string | the reason's localized text |

**Needs:** logged in.
**Gives:** nothing to the client.
**Bifrost's server:** `bh_server/reports.py` `Desk.handle` logs it and keeps it in `client_reports` (it always records the sender as the reporter).

### 90 · Mute player
`var_6165` / `_-m3n` · client → server · TCP lobby · extra u32 B · Bifrost: `reports.MUTE`

Sent from the social hub lobby list (Class_209), the scoreboard (Class_542), and Bifrost's own player menu (PlayerMenu).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | sender's own id |
| 2 | target | uint | player to mute or unmute |

**Needs:** logged in.
**Gives:** the mute state.
**Bifrost's server:** `bh_server/reports.py` `Desk.mute` toggles a stored mute and replies 2470 (1 muted, 2 unmuted).

### 91 · Platform identity record
`var_4694` / `_-C2f` · client → server · TCP lobby · extra u32 B · Bifrost: `reports.IDENTITY`

LinkUpdater.method_6613 builds it. GameCore sends it when a Steam request of kind 3 or 4 answers "1". The only caller fills in just the Steam id and leaves the rest empty.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (unclear) | uint | 1 from the only caller |
| 2 | (unclear) | uint | 0 |
| 3 | persona id | string | Steam id |
| 4 | (unclear) | bool + uint | signed number (sign bit, then size), 0 |
| 5 | (unclear) | string | empty |
| 6 | (unclear) | string | empty |
| 7 | (unclear) | string | empty |
| 8 | (unclear) | string | empty |
| 9 | count | uint | number of entries, 0 from the only caller |
| 9.1-9.5 | (unclear) | 5 × string | per entry |

**Needs:** a successful Steam request.
**Gives:** nothing to the client.
**Bifrost's server:** `bh_server/reports.py` `Desk.handle` logs its size only.

### 92 · Unused
`var_831` / `_-E6Q` · not sent or handled by the 11.00 client or Bifrost's server

### 93 · Demote to spectator
`var_13013` / `_-G4S` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.DEMOTE`

The host moves a player out of their slot into the spectators, from character select.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the player |
| 2 | sub-slot | uint | that player's local slot |

**Needs:** host of the room.
**Gives:** the player becomes a spectator.
**Bifrost's server:** `bh_server/lobby.py` `demote` sends 2409 to the target and 2444 + 2411 to the others. It refuses demoting the host or a sub-slot other than 0.

### 94 · List public rooms
`var_13690` / `_-F1C` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.LIST_PUBLIC`

Sent when the public games list opens (from the custom-game menu, Class_504) and on refresh, at most every 20 s (Class_445).

No payload.

**Needs:** logged in.
**Gives:** the list of public rooms.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` replies 2480 (`public_rooms_packet`).

### 95 · Ask for refundable items
`var_5793` / `_-E6w` · client → server · TCP lobby · extra u32 B · Bifrost: none

Class_744.method_8703 sends it when the refunds screen (Class_533) opens, at most every 10 s. It is not sent during a cross-progression lock.

No payload.

**Needs:** connected.
**Gives:** the refund list from the official server.
**Bifrost's server:** not handled.

### 96 · Set region
`var_11269` / `_-o2G` · client → server · TCP lobby · extra u32 B · Bifrost: `playerprefs.SET_REGION`

Sent from the region screens (Class_417 "a_ScreenConfigureRegions", Class_645 "a_ScreenRegionSettings"). Class_417 may first re-enter the current queue.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | region | uint | chosen region index |

**Needs:** logged in.
**Gives:** the stored region.
**Bifrost's server:** `bh_server/playerprefs.py` `handle` stores it and replies 2482 with region and servers.

### 97 · Set allowed servers
`var_13965` / `_-UD` · client → server · TCP lobby · extra u32 B · Bifrost: `playerprefs.SET_SERVERS`

Sent when the edit-servers prompt (Class_618) closes. It carries the ticked servers as a bitmask.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | servers | uint | bit n set = server region n allowed |

**Needs:** logged in.
**Gives:** the stored server mask.
**Bifrost's server:** `bh_server/playerprefs.py` `handle` stores it and replies 2482.

### 98 · Unused
`var_7560` / `_-j2V` · not sent or handled by the 11.00 client or Bifrost's server

### 99 · Unknown map id (dead sender)
`var_7192` / `_-e6c` · client → server · TCP lobby · extra u32 B · Bifrost: none

Class_594.method_2461 sends the MapID of the current entry in a rotating list (Class_593). Nothing calls it in 11.00.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | map id | uint | MapID of the current entry |

**Needs:** nothing calls it in 11.00.
**Gives:** nothing known.
**Bifrost's server:** not handled.

### 100 · Create clan
`var_2102` / `_-Xh` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.CREATE`

Sent when the player confirms a new clan name, either from the clan-name screen (Class_438.method_2127, after it trims spaces and rejects bad names locally) or from the "UI_Guilds_ConfirmCreate" cost popup (Class_514.method_5740). The server creates the clan with the sender as leader.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | payment | byte | currency the creation cost is paid in: the cost popup sends the currency it shows, the name screen sends 0 (unclear) |
| 2 | name | string | the new clan's name |

**Needs:** logged in, not already in a clan.
**Gives:** the sender leads a new clan and gets the clan state; on failure, an error code.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: skips the byte, collapses spaces, requires Discord sign-in (else a 11013 popup and 2486), refuses if already in a clan, the name is not 2–20 allowed characters, or the name is taken (2486 with a code); on success creates the clan and sends 2487 clan state to all of the creator's sessions.

### 101 · Disband clan
`var_533` / `_-s5V` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.DISBAND`

Sent by the clan leader to delete the whole clan (LinkUpdater.method_350).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | clan id | uint | the player's own clan |

**Needs:** the sender leads a clan.
**Gives:** the clan is gone; every online member is told they left.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: leader only (else 2485 with a code), deletes the clan and sends 2490 "left" to each online member.

### 102 · Invite player to clan
`var_2479` / `_-p2q` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.INVITE`

Sent when a clan member invites another player to the clan (LinkUpdater.method_1686).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the player being invited |

**Needs:** the sender is in a clan.
**Gives:** the invitee gets a clan invite they can answer with 103.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: the target must be online, Discord-signed-in, in no clan, and the clan below capacity (else 2485 with a code); remembers the invite and sends 2491 (clan id, clan name, inviter id, inviter name) to the invitee.

### 103 · Answer clan invite
`var_15441` / `_-v49` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.INVITE_ANSWER`

Sent when the player accepts or declines a clan invite popup (LinkUpdater.method_8975).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | clan id | uint | the inviting clan |
| 2 | inviter id | uint | the player who sent the invite (likely) |
| 3 | accept | bool | true to join, false to decline |

**Needs:** an invite from 102 is pending.
**Gives:** on accept, the player joins the clan.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: drops the pending invite; on accept (and still not in a clan, clan not full) adds the member, sends 2487 clan state to the joiner and 2492 "joined" to the other online members; a missing invite gives 2485.

### 104 · Kick clan member
`var_2127` / `_-P34` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.KICK`

Sent when an officer or leader removes a member (LinkUpdater.method_8355).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the member to remove |

**Needs:** the sender outranks the target.
**Gives:** the target leaves the clan.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: officer or leader only and only on a lower rank; removes the member and sends 2490 (kicked flag set, clan name, kicker name) to the target and the clan.

### 105 · Promote clan member
`var_15286` / `_-G1F` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.PROMOTE`

Raises a member one rank (LinkUpdater.method_127).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the member to promote |

**Needs:** the sender is officer or leader and stays above the new rank.
**Gives:** the member's new rank, shown to the clan.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: rank checks, stores the rank, broadcasts 2489 (who, target, name, old rank, new rank).

### 106 · Demote clan member
`var_382` / `_-XW` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.DEMOTE`

Lowers a member one rank (LinkUpdater.method_2886).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the member to demote |

**Needs:** the sender outranks the target.
**Gives:** the member's new rank, shown to the clan.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: same checks as 105, broadcasts 2489.

### 107 · Make clan leader
`var_13313` / `_-66q` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.MAKE_LEADER`

The leader hands leadership to another member (LinkUpdater.method_3387).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the new leader |

**Needs:** the sender is the leader.
**Gives:** the target leads; the old leader becomes an officer.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: leader only; swaps ranks and broadcasts two 2489 rank changes.

### 108 · Leave clan
`var_7475` / `_-236` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.LEAVE`

Sent when the player quits their clan (LinkUpdater.method_5785).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | clan id | uint | the player's own clan |

**Needs:** in a clan; a leader must hand over leadership first unless alone.
**Gives:** the player is out of the clan.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: a leader with other members gets a failure code; otherwise broadcasts 2490, removes the member, and deletes the clan if it is now empty.

### 109 · Set clan notice
`var_5545` / `_-xo` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.NOTICE_SET`

Sent when an officer edits the clan's notice text (LinkUpdater.method_1439).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | clan id | uint | the player's own clan |
| 2 | text | string | the new notice |

**Needs:** officer or leader.
**Gives:** all online members see the new notice.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: officers only; cuts the text to its limit, stores it and broadcasts 2493 (clan id, text).

### 110 · Clan chat message
`var_13872` / `_-B3o` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.CHAT`

A line typed in clan chat or officer chat (LinkUpdater.method_7664).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | clan id | uint | the player's own clan |
| 2 | text | string | the message |
| 3 | officers only | bool | true for officer chat |

**Needs:** in a clan; officer rank for officer chat.
**Gives:** the line appears for clanmates (or officers).
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: checks rank and mute, broadcasts 2494 (sender id, text, officers flag) and keeps it in the clan chat history.

### 111 · Spectate clanmate
`var_1514` / `_-a6C` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.SPECTATE`

Asks to watch a clanmate's match from the clan list (LinkUpdater.method_5858).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the clanmate |

**Needs:** logged in.
**Gives:** the same result as spectating a friend.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: rewrites it as social packet 83 (spectate friend) and hands it to `social.py`.

### 112 · Join clanmate
`var_13097` / `_-K1Z` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.JOIN`

Asks to join a clanmate's room from the clan list (LinkUpdater.method_7644).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the clanmate |

**Needs:** logged in.
**Gives:** the same result as joining a friend.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: rewrites it as social packet 84 (join friend, with a false flag added) for `social.py`.

### 113 · Party invite clanmate
`var_1050` / `_-G3u` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.PARTY_INVITE`

Invites a clanmate to the player's party from the clan list (LinkUpdater.method_317).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the clanmate |

**Needs:** logged in.
**Gives:** the same result as a party invite.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: rewrites it as social packet 34 (invite) for `social.py`.

### 114 · Set clan recruiting
`var_13948` / `_-S5S` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.RECRUITING`

The recruitment post screen's toggle (Class_461.method_2550); it sends the opposite of the clan's current recruiting flag.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | recruiting | bool | true to list the clan as recruiting |

**Needs:** officer or leader.
**Gives:** the clan shows in (or leaves) the recruitment list.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: officers only; stores it and broadcasts 2501 (byte 1 or 0).

### 115 · Set clan tags
`var_7988` / `_-v3e` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.TAGS`

Sends the clan's chosen tags (Class_68.method_6240).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | count | uint | number of tags |
| 1.1 | tag | uint | a tag id, repeated count times |

**Needs:** officer or leader.
**Gives:** the clan's tags change.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: reads at most 20 tags, stores them as a comma list and broadcasts 2499 (that list as a string).

### 116 · Set clan description
`var_913` / `_-Y6t` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.DESCRIPTION`

Sends the clan's edited description (Class_68.method_5317).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | text | string | the description |

**Needs:** officer or leader.
**Gives:** the clan's description changes.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: officers only; stores it (cut to its limit) and broadcasts 2500.

### 117 · Apply to clan
`var_11881` / `_-2p` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.APPLY`

Sent from the recruitment screen to apply to a clan or withdraw an application (Class_68.method_73, called by Class_638.method_1835); not sent for clan id 0.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | clan id | uint | the clan |
| 2 | apply | bool | true to apply, false to withdraw |
| 3 | count | uint | number of entries that follow |
| 3.1 | value | uint | an id picked on the recruitment screen, repeated count times (unclear) |

**Needs:** not in a clan.
**Gives:** an application the clan's officers can answer with 118.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: skips the list; refuses members of a clan (2485) and players without Discord sign-in (popup); stores or removes the application and sends 2502 to the applicant and the clan's officers.

### 118 · Answer clan applicant
`var_9258` / `_-C1I` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.APPLICANT_ANSWER`

An officer accepts or rejects an application on the applicants screen (Class_622.method_8105).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | clan id | uint | the officer's own clan |
| 2 | player id | uint | the applicant |
| 3 | accept | bool | true to let them in |

**Needs:** officer or leader; a pending application from 117.
**Gives:** the application closes; on accept the applicant joins.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: officers only; removes the application, sends 2502 to the officers and the applicant, and on accept adds the member (2487 to them, 2492 to the others).

### 119 · Ask clan recruitment list
`var_14195` / `_-W3` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.RECRUIT_LIST`

Sent when the clan recruitment screen opens (Class_638.OnInitDisplay), at most once a minute.

No payload.

**Needs:** logged in.
**Gives:** the list of recruiting clans.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: sends 2503 with every recruiting clan (id, name, leader, members, capacity, emblem, tier, xp, created time, tags).

### 120 · Ask clan battle log
`var_9164` / `_-74X` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.BATTLE_LOG`

Sent when the clan Battle Log screen opens (Class_624.OnInitDisplay), at most once every 5 minutes.

No payload.

**Needs:** in a clan.
**Gives:** the clan's recent battles.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: sends 2505 (an empty list when not in a clan).

### 121 · Ask clan leaderboard
`var_8746` / `_-n4N` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.LEADERBOARD`

Sent by the clan leaderboard screen for one board (Class_631.method_6661); each board is fetched at most once an hour.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | board | uint | which board, 1 to 4 |

**Needs:** logged in.
**Gives:** that board's rows.
**Bifrost's server:** `bh_server/clans.py` `Clans._send_board`: ranks clans by last week, this week, xp or elo and sends up to 100 rows in 2511–2515, or 2516 when there are none.

### 122 · Ask clan member info
`var_14424` / `_-45h` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.PLAYER_INFO`

Sent when the player opens a clan member's entry (Class_68.method_5833, from Class_630 and Class_634).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the member |

**Needs:** logged in.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: reads the id and does nothing.

### 123 · Refresh clan missions
`var_8042` / `_-s2N` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.MISSIONS_REFRESH`

Sent by clan screens (Class_68.method_5822 from Class_629 and Class_640), at most once a minute.

No payload.

**Needs:** in a clan.
**Gives:** the clan's weekly duel state.
**Bifrost's server:** `bh_server/clans.py` `Clans.send_duel`: sends 2518 with this week's clan duel.

### 124 · Ask clan missions
`var_2779` / `_-rG` · client → server · TCP lobby · extra u32 B · Bifrost: `clans.MISSIONS`

Sent from the clan missions screen (Class_635.method_1656, set as a popup callback in LinkUpdater); the method's argument is not written.

No payload.

**Needs:** in a clan.
**Gives:** the clan mission list.
**Bifrost's server:** `bh_server/clans.py` `Clans._dispatch`: sends 2504 with an empty list (no clan missions).

### 125 · Local activity status
`var_3750` / `_-A2r` · client → server · TCP lobby · extra u32 B · Bifrost: `social.LOCAL_STATUS`

Menus send this when the player enters an activity the server can't see (LinkUpdater.method_1071): seen values are 8, 9 and 10 from Class_113 and 2 from Class_543, all with detail 0.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | status | uint | presence code (Bifrost: 2 online, 8 training, 9 couch play, 10 local tournament) |
| 2 | detail | uint | extra detail, 0 in every sender |

**Needs:** logged in.
**Gives:** friends see this presence.
**Bifrost's server:** `bh_server/social.py` `Social._dispatch`: keeps (status, detail) when status is training, couch or tournament, else online; used for presence shown to friends.

### 126 · Pick border
`var_11345` / `_-h3J` · client → server · TCP lobby · extra u32 B · Bifrost: `picks.SETS` ("frame")

Sent when the player turns a border (avatar frame) on or off in the loadout, inventory or store screens, or when the client drops one it no longer owns (Class_744.method_1280 via method_1585, with this type passed in).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | item | uint | border id |
| 2 | exclusive | bool | true: this becomes the only one picked |
| 3 | selected | bool | true to add it, false to remove it |

**Needs:** logged in.
**Gives:** the border pick is saved for the account.
**Bifrost's server:** `bh_server/picks.py` `apply`: sets or clears the item's bit in the "frame" set and saves the picks; they come back in the loadout and inventory packets after login.

### 127 · Unused
`var_13115` / `_-C6s` · not sent or handled by the 11.00 client or Bifrost's server

### 128 · Twitch overlay show/hide
`var_6614` / `_-H6Y` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent by the chat box commands TWITCHSHOW and TWITCHHIDE (Class_414.method_4006); with no linked Twitch account the client prints "No twitch account found." instead.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | show | bool | true for TWITCHSHOW, false for TWITCHHIDE |

**Needs:** a linked Twitch account.
**Gives:** nothing visible in the client.
**Bifrost's server:** not handled.

### 129 · Achievement unlocked
`var_10542` / `_-02k` · client → server · TCP lobby · extra u32 B · Bifrost: `achievements.ACK`

Sent after the client triggers a Steam achievement (Class_4.method_5140 → LinkUpdater.method_5701), unless the achievement is already marked done.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | achievement id | small | the achievement |

**Needs:** connected; usually follows a 2802 unlock from the server.
**Gives:** the server knows the unlock reached the client.
**Bifrost's server:** `bh_server/achievements.py` `handle`: marks the achievement acknowledged and logs it.

### 130 · Unused
`var_12485` / `_-F5B` · not sent or handled by the 11.00 client or Bifrost's server

### 131 · Unused
`var_3507` / `_-A53` · not sent or handled by the 11.00 client or Bifrost's server

### 132 · Unused
`var_6954` / `_-U10` · not sent or handled by the 11.00 client or Bifrost's server

### 133 · Unused
`var_5619` / `_-F10` · not sent or handled by the 11.00 client or Bifrost's server

### 134 · Unused
`var_3272` / `_-X4F` · not sent or handled by the 11.00 client or Bifrost's server

### 135 · Tutorial progress
`var_9560` / `_-xY` · client → server · TCP lobby · extra u32 B · Bifrost: `tutorial.TUTORIAL`

Sent when the player finishes a tutorial step that wasn't recorded yet (Class_678 → LinkUpdater.method_2470).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | done mask | uint | bit mask of finished tutorial steps |

**Needs:** logged in.
**Gives:** the progress is kept for the account.
**Bifrost's server:** `bh_server/tutorial.py` `handle`: ORs the mask into the stored one; it is sent back in the next 2431 login.

### 136 · Unused
`var_12042` / `_-G6d` · not sent or handled by the 11.00 client or Bifrost's server

### 137 · Unused
`var_7220` / `_-l6` · not sent or handled by the 11.00 client or Bifrost's server

### 138 · Unused
`var_3909` / `_-t3Y` · not sent or handled by the 11.00 client or Bifrost's server

### 139 · Reroll daily mission
`var_3928` / `_-a4v` · client → server · TCP lobby · extra u32 B · Bifrost: `missions.REROLL`

Sent when the player rerolls one daily mission (Class_282.method_5608).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | mission id | uint | the mission to replace |

**Needs:** logged in; a mission list from 2800.
**Gives:** a new mission in its place.
**Bifrost's server:** `bh_server/missions.py` `handle`: replaces the mission if it is today's and the day's reroll is unused, then resends the 2800 list.

### 140 · Buy battle pass tiers
`var_11314` / `_-73M` · client → server · TCP lobby · extra u32 B · Bifrost: none

A store purchase of a battle-pass "Dummy" item (GameCore store flow → Class_21.method_67), sent when a client-side balance covers count × 35.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | path | small | battle pass path shown on the purchase screen (likely) |
| 2 | tier | small | the tier to reach (likely) |
| 3 | count | small | number of tiers bought (likely) |

**Needs:** a battle pass season.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled (Bifrost has no store; `stats.BIO_MAX` only shares the number).

### 141 · Cross-progression link ticket
`var_5878` / `_-m5u` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent from a button on the Cross Progression screen (Class_421.method_5582 → Class_2.method_3100) when the client has the server's cross-progression token and a Steam encrypted app ticket.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | length | uint | ticket length in bytes |
| 1.1 | ticket byte | byte | one byte of the Steam encrypted app ticket, repeated length times |

**Needs:** a token from the server's answer to 155.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 142 · Unused
`var_1385` / `_-hH` · not sent or handled by the 11.00 client or Bifrost's server

### 143 · Unused
`var_14996` / `_-S59` · not sent or handled by the 11.00 client or Bifrost's server

### 144 · Unused
`var_7567` / `_-J6K` · not sent or handled by the 11.00 client or Bifrost's server

### 145 · Unused
`var_11237` / `_-Q4G` · not sent or handled by the 11.00 client or Bifrost's server

### 146 · Unused
`var_15476` / `_-a3b` · not sent or handled by the 11.00 client or Bifrost's server

### 147 · Cross-progression lookup ticket
`var_1714` / `_-s4l` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent automatically once the server's cross-progression token arrives (Class_2.method_5925), and on refresh from the Cross Progression screen (Class_2.method_9058); same payload as 141.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | length | uint | ticket length in bytes |
| 1.1 | ticket byte | byte | one byte of the Steam encrypted app ticket, repeated length times |

**Needs:** a token from the server's answer to 155.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 148 · Unused
`var_10911` / `_-X20` · not sent or handled by the 11.00 client or Bifrost's server

### 149 · Unused
`var_15589` / `_-f11` · not sent or handled by the 11.00 client or Bifrost's server

### 150 · Unused
`var_6305` / `_-C37` · not sent or handled by the 11.00 client or Bifrost's server

### 151 · Unused
`var_13667` / `_-d6r` · not sent or handled by the 11.00 client or Bifrost's server

### 152 · Unused
`var_1315` / `_-O1f` · not sent or handled by the 11.00 client or Bifrost's server

### 153 · Unused
`var_9654` / `_-pc` · not sent or handled by the 11.00 client or Bifrost's server

### 154 · Cross-progression merge choice
`var_9763` / `_-d16` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent when the player confirms a choice on the Cross Progression screen (Class_421.method_867); with nothing changed the client shows "UI_Brawlhalla_CrossProgression_No_Changes" instead. The two pairs are swapped depending on which side the player picked.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | id A | uint | own player id, or the other account's id when swapped |
| 2 | platform A | byte | own platform, or the other account's platform when swapped |
| 3 | id B | uint | the other of the two ids (0 when no other account is selected) |
| 4 | platform B | byte | the other of the two platforms |

**Needs:** cross-progression data from the server.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 155 · Ask cross-progression status
`var_5731` / `_-Ge` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent when the Cross Progression screen opens (at most every 20 seconds), on its refresh button before a token is known, and from GameCore (Class_2.method_3574).

No payload.

**Needs:** connected.
**Gives:** on the official server, a token string and flag that make the client send 147 (Class_2.method_5925).
**Bifrost's server:** not handled.

### 156 · Ask arcade records
`var_1460` / `_-E5D` · client → server · TCP lobby · extra u32 B · Bifrost: `stats.ARCADE_ASK`

Sent by the arcade screen (Class_672.method_4213).

No payload.

**Needs:** logged in.
**Gives:** the player's best arcade runs.
**Bifrost's server:** `bh_server/stats.py` `handle`: sends 2531 with the account's best arcade result per legend.

### 157 · Ask career stats
`var_9227` / `_-Q40` · client → server · TCP lobby · extra u32 B · Bifrost: `stats.CAREER_ASK`

Asks for the player's career statistics (LinkUpdater.method_4193).

No payload.

**Needs:** logged in.
**Gives:** per-legend and account career stats.
**Bifrost's server:** `bh_server/stats.py` `handle`: sends the career packets (2519 per legend, 2520 account) when the player has an account.

### 158 · Invite player for playlist
`var_8219` / `_-C1k` · client → server · TCP lobby · extra u32 B · Bifrost: `social.INVITE_PLAYLIST`

Invites another player into the sender's room for a given playlist (LinkUpdater.method_5242).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the invitee |
| 2 | playlist | uint | playlist id |

**Needs:** the sender is in a room.
**Gives:** the invitee gets an invite (or the host gets a suggestion).
**Bifrost's server:** `bh_server/social.py` `Social._dispatch`: like 34 invite: refuses outside a room, offline targets or same group (failure codes such as 2404); a non-host's invite becomes a suggestion to the host; otherwise sends the invite with the playlist.

### 159 · Lobby emote
`var_1223` / `_-O5c` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.EMOTE`

Sent when the player uses an emoji from the emote wheel in a lobby (LinkUpdater.SendEmoji, from Class_619 and Class_691).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | emoji | uint | emoji id |
| 2 | extra | uint | a value from the emote wheel, 0 by default (unclear) |

**Needs:** in a room.
**Gives:** room members see the emote.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle`: unless the sender spectates, broadcasts 2538 (sender id, extra, emoji) to the room.

### 160 · Epic platform packet
`var_3905` / `_-u3k` · client → server · TCP lobby · extra u32 B · Bifrost: none

Forwards a packet that the Epic native extension has waiting (GameCore.method_520, ANE_EpicAir.GetNextPacket).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | length | small | byte count |
| 2 | data | raw n | the Epic extension's bytes |

**Needs:** the Epic extension is active.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 161 · Epic session ready
`var_8695` / `_-Q6v` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent once when the Epic native extension reports a session (GameCore.method_520, ANE_EpicAir.HasSession).

No payload.

**Needs:** the Epic extension is active.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 162 · Rematch vote
`var_5959` / `_-E65` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.REMATCH_WANT`

The player's answer on the after-match rematch prompt (LinkUpdater.method_2424).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | want | bool | true for rematch, false to decline |

**Needs:** a rematch window opened by the server (2543).
**Gives:** room members see the vote; when everyone wants it, a rematch starts.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.rematch_want`: ignored outside a rematch window; broadcasts 2542 (player id, byte 1 want / 2 decline); when all players want it, after a short wait puts each party back in its own lobby.

### 163 · Set handicap
`var_713` / `_-c1I` · client → server · TCP lobby · extra u32 B · Bifrost: `lobby.HANDICAP`

Sent from a custom room's handicap controls for a player or bot slot (LinkUpdater.method_651; the handicap part is Class_191.method_5143).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | has handicap | bool | false means default values |
| 1.1 | lives | small | stock count; only if field 1 is true |
| 1.2 | damage dealt | uint | percent, 0 or 100 = normal (likely dealt); only if field 1 is true |
| 1.3 | damage taken | uint | percent, 0 or 100 = normal (likely taken); only if field 1 is true |
| 2 | is bot | bool | true for a bot slot |
| 3 | slot or player | uint | bot: slot index (players first, then bots); player: player id |
| 4 | count or guest | uint | bot: number of players in the room; player: local guest index, 0 for the player (likely) |

**Needs:** in a custom room with handicaps on.
**Gives:** the slot's handicap, shown to the room.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.set_handicap`: only in custom rooms with handicaps on and not starting; host may set any slot, others only themselves; lives ≤ 99, percents 0 or 10–1000; broadcasts 2544.

### 164 · Age answer
`var_841` / `_-J1S` · client → server · TCP lobby · extra u32 B · Bifrost: `playerprefs.AGE`

The player's answer to the age prompt (Class_745.method_2818).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | age | byte | age in years |

**Needs:** logged in.
**Gives:** the age is kept.
**Bifrost's server:** `bh_server/playerprefs.py` `handle`: keeps values 1–85 for the player, ignores others.

### 165 · Unused
`var_13588` / `_-X1O` · not sent or handled by the 11.00 client or Bifrost's server

### 166 · Set emoji slot
`var_15151` / `_-f2W` · client → server · TCP lobby · extra u32 B · Bifrost: `picks.EMOJI`

Sent when the player puts an owned emoji in one of the 20 emote-wheel slots (Class_744.method_4592); the slot number stays on the client.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | emoji | uint | emoji id |

**Needs:** logged in.
**Gives:** the emoji pick is saved.
**Bifrost's server:** `bh_server/picks.py` `apply`: finds the emoji's category slot from EmojiTypes.xml and saves it there (`stats.XP_FULL_ACTIVITY_DAMAGE` only shares the number).

### 167 · Scoreboard reached
`var_2791` / `_-24s` · client → server · TCP lobby · extra u32 B · Bifrost: `reports.SCOREBOARD_SPECTATOR`

Sent when the after-match scoreboard finishes setting up (Class_542.method_2861), only when a lobby state value is 1 and a flag on the scoreboard data is set (unclear).

No payload.

**Needs:** a match just ended.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** `bh_server/reports.py` `handle`: logs it as a spectator at the rematch scoreboard.

### 168 · Ads: ask with language
`var_14415` / `_-Ux` · client → server · TCP lobby · extra u32 B · Bifrost: none

Part of the rewarded-video ads client (Class_306, events "ClickWatchAd", "ExitAds", ...). Sent when ads aren't enabled yet, retried at most once a minute (Class_306.method_6507).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | language | string | the game's current language code |

**Needs:** an ads session id from the server.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 169 · Ads: ask for ads
`var_2526` / `_-21Q` · client → server · TCP lobby · extra u32 B · Bifrost: none

Asks for ads to show (Class_306.method_7111); true right after the server enables ads, false when the queue is empty or the language changed.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | first | bool | true right after ads were enabled |
| 2 | language changed | bool | only if field 1 is false |
| 3 | language | string | new language code; only if field 2 is true |

**Needs:** ads enabled by the server.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 170 · Ads: progress
`var_5995` / `_-OB` · client → server · TCP lobby · extra u32 B · Bifrost: none

Reports a playing ad's progress (Class_306.method_5074, from GameCore ad callbacks): 0 when it starts, 100 when it finishes.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | ad id | string | the ad being shown |
| 2 | progress | uint | 0 at start, 100 at end |
| 3 | value | uint | a counter from the ads screen (unclear) |

**Needs:** an ad from the server's list.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 171 · Ads: closed
`var_11906` / `_-o1K` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent when the player leaves an ad (Class_306.method_6379, "ExitAds").

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | ad id | string | the ad |
| 2 | value | uint | from the ad callback (unclear) |
| 3 | flag | bool | from the ad callback (unclear) |

**Needs:** an ad being shown.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 172 · Ads: ask for session
`var_14115` / `_-e5` · client → server · TCP lobby · extra u32 B · Bifrost: none

Asks for an ads session when there is none or it expired, retried at most once a minute (Class_306.method_5378).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | enabled | bool | whether ads were enabled before |

**Needs:** connected.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 173 · Ads: session keep-alive
`var_4676` / `_-J1o` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent just before another ads packet when 25 minutes passed since the last one (Class_306.method_1023).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | session | string | the ads session id the server gave |

**Needs:** an ads session.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 174 · Unused
`var_8908` / `_-cT` · not sent or handled by the 11.00 client or Bifrost's server

### 175 · Lost match connection
`var_4348` / `_-q1d` · client → server · TCP lobby · extra u32 B · Bifrost: none

Tells the lobby server the game server connection was lost during a match ("Error_LOST_CONNECTION_DURING_GAME"): from the in-match check (GameCore.method_2232) or when the match ends with no game server connection (GameCore.method_151).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | during match | bool | true from the in-match check, false from the match-end path |
| 2 | time | uint | a game time counter at the loss; 0 when field 1 is false (unclear) |

**Needs:** a match was running.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 176 · Local match result
`var_3382` / `_-R6s` · client → server · TCP lobby · extra u32 B · Bifrost: `reports.LOCAL_RESULT`

A summary of a finished match played on the client (LinkUpdater.method_8184).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | own player id |
| 2 | duration | uint | match length in ms |
| 3 | mode | uint | game mode |
| 4 | level | uint | map id |
| 5 | entities | uint | number of player entities |
| 6 | bots | uint | how many of them are bots |

**Needs:** a local match ended.
**Gives:** a record on the server.
**Bifrost's server:** `bh_server/reports.py` `handle`: logs it and keeps a "local" record (limited per session).

### 177 · Telemetry packet
`var_9880` / `_-I1y` · client → server · TCP lobby · extra u32 B · Bifrost: none

Forwards the telemetry native extension's next packet (GameCore.method_520, ANE_DnaManager.GetNextPacket); not on Mac, only when telemetry is on.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | length | small | byte count |
| 2 | data | raw n | the telemetry bytes |

**Needs:** telemetry enabled.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 178 · Hello
`var_3208` / `_-r30` · client → server · TCP lobby · no extra u32 · Bifrost: `protocol.HELLO`

The first packet on a new lobby connection (GameCore.onServerConnected), followed at once by 30 Version. It is excluded from the extra u32 B rule.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | protocol | string | "Brawlhalla client to server protocol 1.0" |

**Needs:** TCP connection open.
**Gives:** nothing back; the server knows the protocol.
**Bifrost's server:** `bh_server/protocol.py` `Session.on_packet`: compares the string with its protocol constant, records the result and logs it.

### 179 · Unused
`var_8663` / `_-q3J` · not sent or handled by the 11.00 client or Bifrost's server

### 180 · Queue preferences
`var_2931` / `_-s1m` · client → server · TCP lobby · extra u32 B · Bifrost: `queueprefs.QUEUE_PREFS`

Sends the player's saved preferences for the current playlist (LinkUpdater.method_8285).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | playlist | small | playlist id |
| 2 | flags | uint | preference flags |
| 3 | ban 1 | small | first banned map |
| 4 | ban 2 | small | second banned map |

**Needs:** a current playlist with stored preferences.
**Gives:** the preferences are kept.
**Bifrost's server:** `bh_server/queueprefs.py` `Desk.handle`: saves the row (up to 500 a session); they are sent back in 2431 login.

### 181 · Join skirmish faction
`var_4041` / `_-N3h` · client → server · TCP lobby · extra u32 B · Bifrost: `skirmish.JOIN`

The player picks a faction in a skirmish event (Class_583.method_1306).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | faction | small | faction id |
| 2 | skirmish | small | event id |

**Needs:** a running skirmish event.
**Gives:** the player belongs to the faction.
**Bifrost's server:** `bh_server/skirmish.py` `handle`: joins the faction if the event is running and the player has none, sends 2921 (event, faction) and 2923 status.

### 182 · Unused
`var_4174` / `_-04R` · not sent or handled by the 11.00 client or Bifrost's server

### 183 · Unused
`var_7835` / `_-22f` · not sent or handled by the 11.00 client or Bifrost's server

### 184 · Ask owned-items refresh
`var_14034` / `_-d1G` · client → server · TCP lobby · extra u32 B · Bifrost: none

Sent when the client notices store items it doesn't know about and the store screen is open (ClientManager), or from Class_496 (Class_744.method_525); only once until the item data arrives again (Class_744.method_1982 clears the flag).

No payload.

**Needs:** connected.
**Gives:** on the official server, a fresh owned-items list (likely).
**Bifrost's server:** not handled.

### 185 · Pick defeat pose
`var_13312` / `_-Q3Z` · client → server · TCP lobby · extra u32 B · Bifrost: `picks.SETS` ("defeat")

Like 126, for the defeat pose set (Class_744.method_9114 → method_1280 with this type; victory poses use 63).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | item | uint | pose id |
| 2 | exclusive | bool | true: this becomes the only one picked |
| 3 | selected | bool | true to add it, false to remove it |

**Needs:** logged in.
**Gives:** the pick is saved for the account.
**Bifrost's server:** `bh_server/picks.py` `apply`: updates the "defeat" set and saves the picks.

### 186 · Choose battle pass path
`var_4091` / `_-j4v` · client → server · TCP lobby · extra u32 B · Bifrost: `battlepass.CHOOSE`

The player picks a battle pass path (Class_21.method_8099), at most once every 5 seconds.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | season | uint | battle pass season |
| 2 | path | small | chosen path |

**Needs:** an active battle pass season.
**Gives:** the path is set.
**Bifrost's server:** `bh_server/battlepass.py` `handle`: picks the path if the season is current and resends the battle pass packets.

### 187 · Match event log
`var_5120` / `_-SB` · client → server · TCP lobby · extra u32 B · Bifrost: none

Uploads the match's event log, a bit stream recorded by Class_329 during the match, when the match ends and the lobby connection is up (LinkUpdater.method_3175).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | match id | uint | an id from the match-start packet (GameCore var_7710) |
| 2 | second id | uint | another id from that packet (unclear) |
| 3 | version | string | "11.00" |
| 4 | length | uint | log size in bytes |
| 4.1 | log byte | byte | one byte of the log, repeated length times |

**Needs:** a match was played.
**Gives:** nothing from Bifrost's server.
**Bifrost's server:** not handled.

### 188 · Ask cup list
`var_10165` / `_-t12` · client → server · TCP lobby · extra u32 B · Bifrost: `cups.LIST_ASK`

Sent when the Cup Hub screen has no cup data, or a cup isn't cached yet (Class_432.method_8266, Class_109.method_6026); the cup id is not written.

No payload.

**Needs:** logged in.
**Gives:** the cup list and the player's registrations.
**Bifrost's server:** `bh_server/cups.py` `handle`: sends 2950 cup list and 2953 registrations.

### 189 · Unused
`var_3663` / `_-45d` · not sent or handled by the 11.00 client or Bifrost's server

### 190 · Unused
`var_13255` / `_-L6V` · not sent or handled by the 11.00 client or Bifrost's server

### 191 · Ask cup history
`var_6974` / `_-t1S` · client → server · TCP lobby · extra u32 B · Bifrost: `cups.HISTORY_ASK`

Sent by the Cup History screen (Class_430.method_3548).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | cup id | uint | the cup |

**Needs:** a cup list.
**Gives:** the cup's brackets.
**Bifrost's server:** `bh_server/cups.py` `handle`: sends 2954 brackets.

### 192 · Ask cup bracket
`var_3276` / `_-U3q` · client → server · TCP lobby · extra u32 B · Bifrost: `cups.BRACKET_ASK`

Asks for one bracket of a cup (Class_109.method_220).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | cup id | uint | the cup |
| 2 | bracket | uint | bracket id |

**Needs:** a cup list.
**Gives:** that bracket.
**Bifrost's server:** `bh_server/cups.py` `handle`: sends 2955.

### 193 · Register for cup
`var_9996` / `_-G3M` · client → server · TCP lobby · extra u32 B · Bifrost: `cups.REGISTER`

Signs up for a cup (Class_109.method_2413).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | cup id | uint | the cup |

**Needs:** an open cup; on Bifrost, a signed-in account.
**Gives:** updated registrations.
**Bifrost's server:** `bh_server/cups.py` `handle`: registers the account if the cup is open, then sends 2953.

### 194 · Unregister from cup
`var_11367` / `_-81V` · client → server · TCP lobby · extra u32 B · Bifrost: `cups.UNREGISTER`

Withdraws from a cup (Class_109.method_5005).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | cup id | uint | the cup |

**Needs:** registered for that cup.
**Gives:** updated registrations.
**Bifrost's server:** `bh_server/cups.py` `handle`: unregisters if allowed, then sends 2953.

### 195 · Unknown
`var_13496` / `_-u4d` · client → server · TCP lobby · extra u32 B · Bifrost: none

Its only sender, Class_45.method_1469, is never called. Class_45 holds lists filled by a server packet (LinkUpdater.method_8289); what this packet asks is unknown.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | id | uint | (unclear) |

**Needs:** never sent.
**Gives:** nothing.
**Bifrost's server:** not handled.

### 196 · Unknown
`var_15464` / `_-u34` · client → server · TCP lobby · extra u32 B · Bifrost: none

Its only sender, Class_45.method_4879, is never called; same class as 195.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | id | uint | (unclear) |
| 2 | flag | bool | (unclear) |

**Needs:** never sent.
**Gives:** nothing.
**Bifrost's server:** not handled.

### 500 · Range marker (not a packet)
`var_10956` / `_-42x` · never sent; used only by LinkUpdater.method_1372

Types below 500 carry extra u32 B, except 16 and 178. `bugreports.MAX_NOTE` only shares the number.

### 1700 · Range marker (not a packet)
`var_3953` / `_-KS` · never sent; used only by LinkUpdater.method_6377

Types 1701–1999 carry extra u32 A.

### 2000 · Range marker (not a packet)
`var_12795` / `_-ym` · never sent; used only by LinkUpdater.method_6377

Ends the 1701–1999 range and starts 2001–2299, which also carry extra u32 A. `cloudsave.MAX_UPLOADS`, `replays.KEEP` and `stats.XP_BONUS_TO` only share the number.

### 2300 · Range marker (not a packet)
`var_4101` / `_-Z3w` · never sent; used only by LinkUpdater.method_6377

Ends the 2001–2299 extra u32 A range.

### 2400 · Unused
`var_4510` / `_-7Q` · not sent or handled by the 11.00 client or Bifrost's server (`stats.K_HIGH_FROM` is the number 2400 used as a rating threshold, not this packet)

### 2401 · Match participants
`var_1859` / `_-E3z` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.PARTICIPANTS`

Sent when a match starts (or a replay is shown) to list who is playing and who is watching. The client clears its participant list, fills it from both lists, and opens the scoreboard and chat roster for the match.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | players | list, `bool` before each item | the match's players |
| 1.1 | player id | uint | |
| 1.2 | platform | uint | platform number (Bifrost sends 1, PC) |
| 1.3 | display name | string | |
| 1.4 | legend id | uint | legend shown for the player; "Unknown" if not in the legend table |
| 1.5 | account name | string | second name kept with the entry (Bifrost repeats the display name) |
| 2 | spectators | list, `bool` before each item | |
| 2.1 | player id | uint | |
| 2.2 | platform | uint | |
| 2.3 | display name | string | |
| 2.4 | account name | string | |

**Needs:** a room that is starting its match.
**Gives:** the client's participant list (used by the scoreboard and match chat); scoreboard refreshed.
**Bifrost's server:** `bh_server/lobby.py` `participants_packet`, broadcast by `send_start`; also sent by `stats.py` when replaying a stored result.

### 2402 · Leave match acknowledged
`var_7359` / `_-L42` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.LEAVE_MATCH_DONE`

Confirms that the client left the match it was in. If the rewards screen is waiting for it the client goes back to the main menu and closes that screen; otherwise it returns to the menu only when it is not on the rewards, scoreboard or character-select screens.

No payload.

**Needs:** sent after the client's 85 (leave match).
**Gives:** the client back at the main menu (game state 0).
**Bifrost's server:** `bh_server/lobby.py` `Lobby.leave_match`.

### 2403 · Join or spectate refused by privacy
`var_5667` / `_-B3E` · server → client · TCP lobby · extra u32 A · Bifrost: `social.PRIVACY_FAILED`

Tells the client that joining or spectating a friend's or clanmate's room failed because of that room's privacy setting. The client shows the matching error and refreshes the main menu.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | reason | uint | 1 spectate friend, 2 join friend, 5 spectate clanmate, 6 join clanmate (`Error_*_PRIVACY_FAIL` popups) |

**Needs:** a join/spectate-friend request (83, 84).
**Gives:** an error popup.
**Bifrost's server:** `bh_server/social.py` `_to_friend` (via `_fail`) when a custom room is neither public nor open to friends.

### 2404 · Friend or clan action failed
`var_9802` / `_-n1q` · server → client · TCP lobby · extra u32 A · Bifrost: `social.ACTION_FAILED`

Tells the client that a social action failed. The client shows the error for the code, refreshes the main menu and hides the "waiting to spectate" screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | reason | uint | 1 spectate friend, 2 join friend, 3 invite friend, 4 friend already in your group, 5-8 the same for clanmates, 9 crossplay join refused, 10 post-match invite failed, 11 the suggesting player is gone |

**Needs:** a social request (invite, join, spectate, suggest).
**Gives:** an error popup.
**Bifrost's server:** `bh_server/social.py` `_fail`, used by the invite, accept, suggest and join/spectate-friend handlers.

### 2405 · Friend status changed
`var_10220` / `_-Mq` · server → client · TCP lobby · extra u32 A · Bifrost: `social.FRIEND_STATUS`

Updates one friend's online status. If the friend is in the client's list, its status is changed and the social hub and friends screen are redrawn.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | friend id | uint | |
| 2 | status | uint | Bifrost's values: 1 offline, 2 online, 3 custom room, 4 in queue, 5 playing, 6 spectating, 7 in lobby, 8 training, 9 couch party, 10 tournament |
| 3 | detail | uint | only if status is 3, 4, 5 or 7 (unclear; a room or playlist detail) |

**Needs:** the friend list (2406) already received.
**Gives:** updated status on the friends screens.
**Bifrost's server:** `bh_server/social.py` `refresh` (status changes) and `remove_friend` (sends offline).

### 2406 · Friend list
`var_3727` / `_-q5y` · server → client · TCP lobby · extra u32 A · Bifrost: `social.FRIEND_LIST`

The full friend list, sent when the player comes online. Known friends are updated and new ones added; the "friends unavailable" flag (see 2473) is cleared and the friends screens are redrawn.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | friends | list, `bool` before each item | |
| 1.1 | friend id | uint | |
| 1.2 | name | string | |
| 1.3 | second name | string | kept only when the friend is new (unclear; Bifrost sends "") |
| 1.4 | status | uint | as in 2405 |
| 1.5 | detail | uint | only if status is 3, 4, 5 or 7 |
| 1.6 | avatar | uint | friend's avatar/portrait id |

**Needs:** login (2431).
**Gives:** the client's friend list.
**Bifrost's server:** `bh_server/social.py` `online` via `_send_rows`.

### 2407 · Friends added
`var_5562` / `_-N2h` · server → client · TCP lobby · extra u32 A · Bifrost: `social.FRIENDS_ADDED`

Adds or updates some friends without replacing the list. Same row as 2406, but the list is counted.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | count | uint | number of rows |
| 2 | friends | count × row | |
| 2.1 | friend id | uint | |
| 2.2 | name | string | |
| 2.3 | second name | string | (unclear) |
| 2.4 | status | uint | as in 2405 |
| 2.5 | detail | uint | only if status is 3, 4, 5 or 7 |
| 2.6 | avatar | uint | |

**Needs:** nothing beyond login.
**Gives:** new or refreshed friend entries.
**Bifrost's server:** `bh_server/social.py` `befriended`, `refresh` (a friend came online) and `renamed`.

### 2408 · Friend avatar changed
`var_1248` / `_-U6f` · server → client · TCP lobby · extra u32 A · Bifrost: none

Changes one friend's avatar and redraws the friends screens.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | friend id | uint | |
| 2 | avatar | uint | same value as field 1.6 of 2406 |

**Needs:** the friend list.
**Gives:** the friend's new avatar.
**Bifrost's server:** not sent.

### 2409 · You are now a spectator
`var_9248` / `_-86` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.NOW_SPECTATOR`

Tells the client that it was moved from a player slot to the room's spectators. The client removes itself from the slots, adds itself as a spectator, redraws character select and shows the party chat.

No payload.

**Needs:** the client is a player in a room.
**Gives:** room role = spectator.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.demote` (93).

### 2410 · Unknown (empty handler)
`var_6850` / `_-723` · server → client · TCP lobby · extra u32 A · Bifrost: none

The 11.00 client registers a handler for it that does nothing.

No payload.

**Needs:** nothing.
**Gives:** nothing.
**Bifrost's server:** not sent.

### 2411 · Spectator joined
`var_1593` / `_-955` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.SPECTATOR_JOINED`

Someone joined the room as a spectator. The client adds them to the spectator and participant lists.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | platform | uint | |
| 3 | display name | string | |
| 4 | account name | string | |
| 5 | demoted | bool | true when a player was moved to spectators (only changes an internal chat message kind) |

**Needs:** the client is in the room.
**Gives:** an extra spectator in the room.
**Bifrost's server:** `bh_server/lobby.py` `spectator_packet`, sent from `spectate_room` and `Lobby.demote`.

### 2412 · Left the room
`var_14689` / `_-b1B` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.LEFT_SELF`

Removes someone from the room. When the id is the receiver's own and the flag is false, the client leaves the room entirely: clears character select, returns to the main menu and empties its participant list. For another id it removes that player and clears their typing mark.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | who left |
| 2 | flag | bool | (unclear; when true the receiver does not leave even for its own id) |

**Needs:** the client is in a room.
**Gives:** the client out of the room (own id) or a smaller roster.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.leave` and the queue-cancel branch of `Lobby.handle`; always flag false. Bifrost's client also watches it (SpectateFeed ends a spectated match).

### 2413 · Party or game chat line
`var_6142` / `_-M5x` · server → client · TCP lobby · extra u32 A · Bifrost: `social.CHAT`

A chat message from someone in the room. Ignored when the client's chat is switched off; otherwise added to the chat box under the sender's name.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | sender id | uint | |
| 2 | sender sub-slot | byte | guest index, used with the id to find the name |
| 3 | party | bool | true: Party channel, false: Game channel |
| 4 | text | string | |

**Needs:** the client is in a room.
**Gives:** a chat line.
**Bifrost's server:** `bh_server/social.py` `_chat` (reply to 77), broadcast to the room.

### 2414 · Typing indicator
`var_5119` / `_-I6x` · server → client · TCP lobby · extra u32 A · Bifrost: `social.TYPING_SHOWN`

Shows or hides "is typing" for a room member in the chat box.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | typing | bool | |

**Needs:** another member sent 78.
**Gives:** chat box typing mark.
**Bifrost's server:** `bh_server/social.py` `_typing`, broadcast to the room except the typer.

### 2415 · Left chat channel
`var_13808` / `_-yE` · server → client · TCP lobby · extra u32 A · Bifrost: `social.CH_LEFT`

Someone left a named chat channel (General, Clan, ...). The client prints "X has left channel [name]" ("You have" for itself) and removes them from that channel's member list.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | channel id | uint | chat channel number from the game's chat channel table |
| 2 | player id | uint | |
| 3 | name | string | |

**Needs:** the client knows the channel (2417).
**Gives:** smaller channel member list, chat line.
**Bifrost's server:** `bh_server/social.py` `_leave_general` (General channel only).

### 2416 · Joined chat channel
`var_13934` / `_-MT` · server → client · TCP lobby · extra u32 A · Bifrost: `social.CH_JOINED`

Someone joined a chat channel the client already has. The client adds the member and prints "X has joined channel [name]".

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | channel id | uint | |
| 2 | player id | uint | |
| 3 | name | string | |
| 4 | second name | string | (unclear; Bifrost sends "") |

**Needs:** 2417 for that channel first (otherwise ignored).
**Gives:** new channel member.
**Bifrost's server:** `bh_server/social.py` `_join_general`.

### 2417 · Chat channel members
`var_4718` / `_-Q19` · server → client · TCP lobby · extra u32 A · Bifrost: `social.CH_MEMBERS`

The member list of a chat channel; creates the channel on the client if it does not exist yet.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | channel id | uint | |
| 2 | count | uint | |
| 3 | members | count × row | |
| 3.1 | player id | uint | |
| 3.2 | name | string | |
| 3.3 | second name | string | (unclear) |

**Needs:** nothing.
**Gives:** a channel the client can show messages for.
**Bifrost's server:** `bh_server/social.py` `_join_general`, sent before 2416.

### 2418 · Chat channel message
`var_14008` / `_-Y3E` · server → client · TCP lobby · extra u32 A · Bifrost: `social.CH_MESSAGE`

A line said in a chat channel; the sender's name is looked up in the channel's members ("Unknown" if missing).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | sender id | uint | |
| 2 | channel id | uint | |
| 3 | text | string | |

**Needs:** 2417 for the channel.
**Gives:** a chat line.
**Bifrost's server:** `bh_server/social.py` `_general_say` (reply to 76 for the General channel).

### 2419 · Error, back to menu
`var_6208` / `_-h3w` · server → client · TCP lobby · extra u32 A · Bifrost: none

Shows a server-supplied error and backs out: sets the client's mode to the menu state, closes the room-code and main-menu panels, hides the match preview and drops a pending object (unclear which flow this ends).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | message | string | error text or string key shown in a popup |

**Needs:** (unclear)
**Gives:** an error popup and the client out of the flow it was in.
**Bifrost's server:** not sent.

### 2420 · Spectate ticket
`var_13251` / `_-R2u` · server → client · TCP lobby · extra u32 A · Bifrost: `spectate.TICKET`

Gives a spectator where to connect to watch a match. The client becomes a spectator, stores its id, the host, port, ticket and room, and shows the "waiting to spectate" screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the receiver's own id |
| 2 | host | string | address to watch from (Bifrost sends "" and its client fills in the server's host) |
| 3 | port | small | |
| 4 | ticket | string | token presented when connecting |
| 5 | room | uint | room number being watched |

**Needs:** the client is a spectator of a room whose match started.
**Gives:** spectate connection details; spectate waiting screen.
**Bifrost's server:** `bh_server/spectate.py` `ticket_packet`, from `late_ticket` and `Desk.handle` when a feed starts. Bifrost's client (SpectateFeed) also builds and dispatches its own 2420 locally.

### 2421 · Purchase failed
`var_11593` / `_-K6i` · server → client · TCP lobby · extra u32 A · Bifrost: none

Reports a failed store purchase and refreshes the "buy idols" screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | reason | uint | 1 sale period ended (also turns the sale flag off), 2 purchase timed out, 3 unknown error, try again |

**Needs:** a purchase request.
**Gives:** an error popup.
**Bifrost's server:** not sent.

### 2422 · Match details
`var_15001` / `_-g4q` · server → client · TCP (connection not established) · extra u32 A · Bifrost: none

The full description of a match about to be played: game rules and every player with their loadouts. The client builds the match-preview data (shown by the "match preview" loading screen), stores players' names and clans, and either starts normally or takes a special path when field 3 is 2. Many fields are read into locals the 11.00 client never uses.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (unclear) | uint | not used |
| 2 | level id | uint | the map |
| 3 | mode | uint | 2 takes a separate start path (unclear); otherwise sends matchmaking telemetry |
| 4 | (unclear) | small | |
| 5 | (unclear) | small | |
| 6 | flags | uint | bit 1, bit 128 (adds field 15.31), bit 64 (adds field 15.32), bit 256 (unclear meanings) |
| 7 | (unclear) | u64 | not used |
| 8 | (unclear) | uint | not used |
| 9 | loadouts per player | uint | count for field 15.34 |
| 10 | (unclear) | uint | not used |
| 11 | game rules | 15 × uint | same block as in the room settings (see 2445 field 3.4) |
| 12 | (unclear) | small | not used |
| 13 | (unclear) | uint | above 1 sets a match option |
| 14 | (unclear) | uint | |
| 15 | players | list, `bool` before each item | |
| 15.1 | player id | uint | |
| 15.2-15.5 | (unclear) | 4 × uint | |
| 15.6 | name | string | |
| 15.7 | (unclear) | uint | not used |
| 15.8 | clan | string | |
| 15.9 | (unclear) | small | |
| 15.10-15.20 | (unclear) | 11 × uint | |
| 15.21 | (unclear) | 8 × uint | |
| 15.22 | (unclear) | 2 × small | not used |
| 15.23 | (unclear) | list, `bool` before each pair of uints | |
| 15.24 | (unclear) | small | |
| 15.25 | (unclear) | uint | |
| 15.26-15.27 | (unclear) | 2 × small | |
| 15.28 | team | uint | |
| 15.29 | (unclear) | uint | |
| 15.30 | (unclear) | small | if any player has 0 here, all are set to 0 |
| 15.31 | (unclear) | uint | only if flag 128 |
| 15.32 | handicap | bool, then small, uint, uint if true | only if flag 64 |
| 15.33 | bot-like | bool | true: a uint follows (and the name may be a skin's name key, then localized); false: a string follows (unclear) |
| 15.34 | loadouts | field 9 × (8 uint) | legend id, skin id, then 6 uints (unclear); if 15.33 is false each also has bool, uint, uint |

**Needs:** a match being started.
**Gives:** match-preview data for the loading screen and match setup.
**Bifrost's server:** not sent.

### 2423 · Level select closed
`var_6943` / `_-V6v` · server → client · TCP lobby · extra u32 A · Bifrost: none

Closes the level-select screen and goes back to character select (or the party screen); also tells the broadcast overlay when it is on.

No payload.

**Needs:** level select open (2460).
**Gives:** level select closed.
**Bifrost's server:** not sent.

### 2424 · Level select winner
`var_12779` / `_-64C` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.LEVEL_WINNER`

Announces the chosen map at the end of level select. Ignored unless the level-select screen is open. In the per-player pick mode it highlights the winner tile of the given player; in the voting/striking modes it shows the result.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | level id | uint | |
| 2 | player id | uint | whose pick won (Bifrost sends the host) |
| 3 | sub-slot | uint | |

**Needs:** 2460 opened level select.
**Gives:** the chosen map on the level-select screen.
**Bifrost's server:** `bh_server/lobby.py` `finish_level_select`.

### 2425 · Player done striking
`var_11212` / `_-91K` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.LEVEL_STRUCK`

Marks a player as done striking maps on the level-select screen (and on the broadcast overlay).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | slot | uint | |

**Needs:** level select open.
**Gives:** updated level-select state.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.level_select` (after 58).

### 2426 · Server notice
`var_11819` / `_-x5s` · server → client · TCP lobby · extra u32 A · Bifrost: `social.SERVER_NOTICE`

A message from the server: either an error-style popup or the server-notification banner.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | text | string | text or string key |
| 2 | popup | bool | true: popup; false: notification banner |

**Needs:** nothing.
**Gives:** a message on screen.
**Bifrost's server:** `bh_server/social.py` `server_notice_packet` (announcements, login notice), `bh_server/tournaments.py`, and `bh_server/lobby.py` for a tournament's banned-legend reason.

### 2427 · No game server available
`var_12535` / `_-l5d` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.NO_GAME_SERVER`

The match could not start because no game server was available. The client cancels queue state, clears character select, returns to the menu, shows `Error_NO_VALID_GAMESERVERS` and drops any game-server connection.

No payload.

**Needs:** a match about to start.
**Gives:** the client back at the main menu.
**Bifrost's server:** `bh_server/lobby.py` `begin_matched` when the matched room cannot start.

### 2428 · Queue closed
`var_7963` / `_-y4B` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.QUEUE_CANCELLED`

Ends the queue wait. With the flag set it just hides the waiting-queue screen; otherwise a spectator also closes the spectate-wait screen, and a player leaves queue state.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | hide only | bool | Bifrost always sends false |

**Needs:** the room was queueing (2472).
**Gives:** queue state off.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` (32 cancel queue), `Lobby.leave` and `begin_matched` (right before a matched start). Bifrost's client (StartCover) uses it as a cue to cover the screen.

### 2429 · Participant left the match
`var_12903` / `_-91x` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.PARTICIPANT_LEFT`

Removes one player from the match participant list (2401).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |

**Needs:** 2401.
**Gives:** smaller participant list.
**Bifrost's server:** `bh_server/lobby.py` `participant_left_packet`, from `Lobby.leave_match` and `Lobby.leave`; also `bh_server/stats.py`.

### 2430 · Your name changed
`var_14562` / `_-u3W` · server → client · TCP lobby · extra u32 A · Bifrost: `protocol.NAME_CHANGED`

Sets the receiver's own display name and refreshes the social hub.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | name | string | new display name |

**Needs:** login.
**Gives:** the client's display name.
**Bifrost's server:** `bh_server/protocol.py` `Session.on_packet` (reply to Bifrost's 11002 rename, with the old name if refused) and `signed_in`. Bifrost's client (PlayerName) waits for it after a rename.

### 2431 · Login accepted
`var_1811` / `_-64L` · server → client · TCP lobby · extra u32 A · Bifrost: `protocol.LOGIN_OK`

The answer to a successful login: the player's identity, level, settings, playlist catalog, ratings, preferences, mute list, store ownership and account links. The client stores all of it, redraws the menus and store, applies the client theme and timed event, and marks itself logged in.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | account name | string | |
| 2 | user id | uint | the player's id |
| 3 | has extra | bool | |
| 3.1 | (unclear) | bool | only if 3 |
| 3.2 | (unclear) | uint | only if 3; both passed to the tutorial/progress object |
| 4 | display name | string | |
| 5 | xp | uint | account XP |
| 6 | level | small | |
| 7 | level start xp | uint | XP where the level starts |
| 8 | next level xp | uint | XP of the next level (level progress = (5-7)/(8-7)) |
| 9 | tutorial mask | uint | completed tutorial steps |
| 10 | (unclear) | string | |
| 11 | store region tag | string | hides store bundles not offered for it (unclear exact meaning) |
| 12 | (unclear) | string | |
| 13 | currency | byte | currency id used for real-money idol prices (see 2468) |
| 14 | (unclear) | bool | |
| 15 | (unclear) | uint | |
| 16 | (unclear) | bool | the main menu checks it |
| 17 | preferred region | uint | |
| 18 | region mask | uint | one bit per region (Bifrost: "servers" setting) |
| 19 | region availability | one bool per region in the game's region table, no count | |
| 20 | playlist catalog | | record shape as in `bh_server/playlists.py` `write_playlist` |
| 20.1 | casual playlists | list, `bool` before each item | playlist record, then expiry uint |
| 20.2 | ranked playlists | list, `bool` before each item | playlist record, then expiry uint |
| 20.3 | special playlist | bool, then one playlist record and uint if true | (unclear) |
| 21 | (unclear) | list, `bool` before each item | u64, 5 × uint, small; read and discarded |
| 22 | (unclear) | list, `bool` before each item | u64, uint, string, 13 × uint; read and discarded |
| 23 | ratings | list, `bool` before each item | |
| 23.1 | user id | uint | |
| 23.2 | rating ladder id | small | |
| 23.3 | games | uint | |
| 23.4 | wins | uint | |
| 23.5 | rating | small | |
| 23.6 | stars | small | |
| 23.7 | peak rating | small | |
| 24 | queue preferences | list, `bool` before each item | |
| 24.1 | playlist id | small | |
| 24.2 | flags | uint | |
| 24.3 | banned map 1 | small | |
| 24.4 | banned map 2 | small | |
| 25 | mute list | list, `bool` before each item | player id uint, state uint (as 2470) |
| 26 | (unclear) | bool | |
| 27 | owned store items | three lists, `bool` before each item | (bool sign + small id, uint), (bool sign + small id, uint) for a second item table, (uint id) |
| 28 | (unclear) | uint | store-related setting |
| 29 | (unclear) | string | store-related setting |
| 30 | server time | uint | compared with cup deadlines |
| 31 | sale active | uint | non-zero turns the sale flag on |
| 32 | timed event id | uint | |
| 33 | timed event time left | uint | Bifrost sends seconds remaining |
| 34 | (unclear) | bool | given to the main menu |
| 35 | (unclear) | list, `bool` before each uint | client-manager entries; a change flags store news |
| 36 | client theme | uint | |
| 37 | (unclear) | bool | |
| 38 | age setting | byte | Bifrost's name; passed to an age-gated check |
| 39 | (unclear) | uint | not used |
| 40 | (unclear) | uint | |
| 41-44 | cross-progression | 4 × uint | account link fields; 42 = 1 means a cross-progress account |
| 45 | platform id | string | used as the PSN id |
| 46 | linked account | bool | |
| 46.1 | (unclear) | uint | only if 46 |
| 46.2 | (unclear) | uint | only if 46 |
| 46.3 | (unclear) | string | only if 46 |
| 46.4 | has more | bool | only if 46 |
| 46.5 | (unclear) | string | only if 46.4 |

**Needs:** sent after the client's 30 Version and a login request (20/88; Bifrost may first run its own 11007-11010 sign-in).
**Gives:** logged-in client state, the playlist catalog, ratings, mutes, store ownership; main menu ready.
**Bifrost's server:** `bh_server/protocol.py` `login_ok`, sent from `Session.on_packet`; field 37 only for 11.00 clients.

### 2432 · Chat status line
`var_5225` / `_-L44` · server → client · TCP lobby · extra u32 A · Bifrost: `social.STATUS_LINE`

Adds a plain system line to the chat box.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | text | string | |

**Needs:** nothing.
**Gives:** a chat line.
**Bifrost's server:** `bh_server/social.py` `_status` (help text for its `!g`, `!leave` and other chat commands).

### 2433 · Unused
`PKTTYPE_UNUSED_2433` / `PKTTYPE_UNUSED_2433` · not sent or handled by the 11.00 client or Bifrost's server

### 2434 · Game server assignment
`var_13447` / `_-h2G` · server → client · TCP lobby · extra u32 A · Bifrost: none

Tells the client which game server hosts its match. The client stores the details and opens the UDP and TCP game-server connections; once TCP connects it sends 10400 with its id and the ticket.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the receiver's id |
| 2 | level id | uint | |
| 3 | ticket | string | sent back in 10400 |
| 4 | host | string | game server address |
| 5 | TCP port | uint | |
| 6 | UDP port | small | |
| 7 | use Network Next | bool | |

**Needs:** a match being started.
**Gives:** game-server connections.
**Bifrost's server:** not sent (Bifrost uses its own start packets).

### 2435 · Rematch timer ended
`var_1568` / `_-84x` · server → client · TCP lobby · extra u32 A · Bifrost: none

Stops the room's rematch timer. Then either marks the room as searching and refreshes the waiting-queue screen, or shows that screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | searching | bool | |
| 2 | show queue screen | bool | used only when 1 is false |

**Needs:** a room with a rematch timer (2445 field 9).
**Gives:** timer cleared; queue screen state.
**Bifrost's server:** not sent.

### 2436 · Match found
`var_9035` / `_-01q` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.MATCH_FOUND_GAME`

Tells a queueing room that a match was found. The client records the stage, updates the scoreboard if it is open, and redraws the waiting-queue screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | stage | uint | 1 = found (Bifrost always sends 1) |
| 2 | match tag | string | kept by the telemetry object (Bifrost sends "") |

**Needs:** the room is queueing (2472).
**Gives:** "match found" state.
**Bifrost's server:** `bh_server/lobby.py` `Matchmaker.found`. Bifrost's client (StartCover) starts watching for the match start on it.

### 2437 · Room member changed
`var_12014` / `_-22P` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.PLAYER_CHANGED`

Updates one existing room slot (picks, ready state, team, colour, loadouts).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | entry | roster entry | as in 2445 field 15 |
| 2 | clear flag | bool | true clears a per-slot flag (unclear) |

**Needs:** the slot exists (2445/2449).
**Gives:** the slot's new state on character select.
**Bifrost's server:** `bh_server/lobby.py` `entry_changed`, from `Lobby.handle` (41 lock in, 43 unready) and bot changes.

### 2438 · Team changed
`var_8166` / `_-c4T` · server → client · TCP lobby · extra u32 A · Bifrost: none

Sets the team of one slot and redraws character select.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | by slot | bool | |
| 1.1 | slot | uint | only if 1; applies only to a bot-type slot |
| 1.2 | team | uint | only if 1 |
| 1.3 | player id | uint | only if 1 is false |
| 1.4 | sub-slot | uint | only if 1 is false |
| 1.5 | team | uint | only if 1 is false |

**Needs:** a room.
**Gives:** slot team.
**Bifrost's server:** not sent.

### 2439 · Invited to a room
`var_8427` / `_-I1v` · server → client · TCP lobby · extra u32 A · Bifrost: `social.INVITED`

An invitation for the receiver. The client adds it to its invite notifications and pending invites and redraws the social hub.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | inviter id | uint | |
| 2 | inviter name | string | |
| 3 | invite id | uint | answered with 35/36 |
| 4 | playlist | bool sign + uint | -1 for none |
| 5 | has second name | bool | |
| 5.1 | second name | string | only if 5; shown as "name + second" |

**Needs:** someone invited the receiver.
**Gives:** a pending invite.
**Bifrost's server:** `bh_server/social.py` `_invite`, `server_invite`, and reused for friend requests and offers (`_offer_request`, `offer`).

### 2440 · Room member invited someone
`var_5466` / `_-32k` · server → client · TCP lobby · extra u32 A · Bifrost: `social.LOBBY_INVITED`

Tells the room that a member invited someone: prints "X has invited Y to the lobby" and adds Y as invited.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | inviter id | uint | |
| 2 | inviter name | string | |
| 3 | invitee id | uint | |
| 4 | invitee sub-slot | uint | |
| 5 | invitee name | string | |
| 6 | playlist | bool sign + uint | -1 for none; named in the message |
| 7 | has second name | bool | |
| 7.1 | second name | string | only if 7 |

**Needs:** the receiver is in the room.
**Gives:** invited entry and chat line.
**Bifrost's server:** `bh_server/social.py` `_invite` and `server_invite`.

### 2441 · Invite suggested to host
`var_5734` / `_-GW` · server → client · TCP lobby · extra u32 A · Bifrost: `social.SUGGESTED`

A member suggests inviting someone; sent to the host. Duplicates are ignored; otherwise a chat line "X has suggested Y" and, for the host, a suggestion notification.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | suggestion id | uint | with 2, identifies the suggestion |
| 2 | (unclear) | uint | Bifrost sends the suggested player's id |
| 3 | suggester name | string | |
| 4 | suggested player id | uint | |
| 5 | sub-slot | uint | |
| 6 | suggested name | string | |
| 7 | playlist | bool sign + uint | |

**Needs:** the receiver hosts the room.
**Gives:** a suggestion to act on.
**Bifrost's server:** `bh_server/social.py` `_suggest`.

### 2442 · Invite accepted
`var_3102` / `_-u4c` · server → client · TCP lobby · extra u32 A · Bifrost: `social.INVITE_ACCEPTED`

Marks an invited player as having accepted.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | name | string | |

**Needs:** 2440.
**Gives:** invite state.
**Bifrost's server:** `bh_server/social.py` `_accept`.

### 2443 · Invite declined
`var_1527` / `_-93z` · server → client · TCP lobby · extra u32 A · Bifrost: `social.INVITE_DECLINED`

Marks an invited player as having declined.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | name | string | |

**Needs:** 2440.
**Gives:** invite state.
**Bifrost's server:** `bh_server/social.py` `_dispatch` (36).

### 2444 · Someone left the room
`var_14341` / `_-s1E` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.LEFT_OTHER`

Removes a bot, or removes another player's slot and shows "has left" on character select (and in the scoreboard chat when it is open).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | bot | bool | true: remove a bot, nothing more follows |
| 1.1 | player id | uint | only if 1 is false |
| 1.2 | sub-slot | uint | only if 1 is false |
| 1.3 | quiet | bool | only if 1 is false; true skips the chat line (Bifrost uses it when a player became a spectator) |

**Needs:** a room.
**Gives:** smaller roster.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.manage_party` (45 remove bot), `Lobby.demote`, `Lobby.leave`; `bh_server/social.py` `_kick`.

### 2445 · Room state
`var_11569` / `_-D1L` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.ROOM`

The full state of the room the client is now in: settings, host, cup data, players, bots, invites and spectators. The client rebuilds its room, opens character select (game state 1), closes menus and store screens, shows party chat and the queue screen when needed, and can show the "invite guildmates" prompt.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | room number | uint | |
| 2 | (unclear) | uint | Bifrost sends 0 |
| 3 | settings | | room settings block (also the whole of 2448) |
| 3.1 | playlist id | uint | 0 = custom room |
| 3.2 | custom room type | uint | |
| 3.3 | (unclear) | small | only if 3.1 is 0 |
| 3.4 | game rules | 15 × uint | only if 3.1 is 0; 1st flags, 3rd match duration, 6th scoring type, others (unclear) |
| 3.5 | room flags | uint | (Bifrost derives privacy from it) |
| 3.6 | (unclear) | byte | |
| 3.7 | level select mode | small | |
| 3.8 | (unclear) | bool | |
| 3.9 | handicaps on | bool | adds the handicap blocks below |
| 4 | (unclear) | bool | with 6, shows the waiting-queue screen |
| 5 | host id | uint | ignored for cup rooms |
| 6 | spectator | bool | the receiver is a spectator |
| 7 | open lobby tab | bool | opens the room's lobby tab when the room flags allow |
| 8 | guild invite prompt | bool | shows "invite guildmates" |
| 9 | rematch timer | uint | ms; non-zero starts a rematch timer |
| 10 | description | string | |
| 11 | queue room | bool | (Bifrost: the room is for a playlist) |
| 12 | cup data | 9 × uint | cup set id, cup id, bracket id, (unclear), wins required, next set if win, next set if loss, score 1, score 2 |
| 13 | (unclear) | list, `bool` before each item | value uint, index uint; a slot-order map |
| 14 | players | list, `bool` before each item | each a roster entry (field 15), then a handicap block if 3.9 |
| 15 | roster entry | | shared by 2437 and 2449 |
| 15.1 | bot | bool | |
| 15.2 | slot | uint | only if bot |
| 15.3 | player id | uint | only if not bot |
| 15.4 | name | string | only if not bot |
| 15.5 | clan | string | only if not bot |
| 15.6 | (unclear) | 2 × uint | only if not bot |
| 15.7 | platform | uint | only if not bot |
| 15.8 | account name | string | only if not bot |
| 15.9 | (unclear) | uint | only if not bot |
| 15.10 | sub-slot | uint | only if not bot; guest index |
| 15.11 | team | uint | |
| 15.12 | ready | uint | |
| 15.13 | colour | uint | |
| 15.14 | (unclear) | 3 × uint | |
| 15.15 | avatar | small | |
| 15.16 | (unclear) | uint | cosmetic |
| 15.17 | (unclear) | small | cosmetic |
| 15.18 | (unclear) | bool | |
| 15.19 | loadout count | uint | |
| 15.20 | loadouts | count × item | random bool, (unclear) bool that drops the skin, legend id uint, skin id uint, uint (unclear), weapon skins uint (low 15 bits weapon 1, high 16 bits weapon 2), 2 × uint (unclear) |
| 16 | bots | list, `bool` before each item | |
| 16.1 | bot level | uint | |
| 16.2 | full entry | bool | |
| 16.3 | entry | roster entry | only if 16.2 |
| 16.4 | team | uint | only if 16.2 is false |
| 16.5 | (unclear) | uint | only if 16.2 is false |
| 16.6 | handicap | bool, then small, uint, uint if true | only if 3.9 |
| 17 | (unclear) | list, `bool` before each item | uint, uint, string, uint (Bifrost sends none) |
| 18 | spectators | list, `bool` before each item | player id uint, platform uint, name string, account name string |

**Needs:** the client created, joined or was moved into a room (33, 38, invites, queue).
**Gives:** the room and the character-select screen.
**Bifrost's server:** `bh_server/lobby.py` `room_packet` (11.00 shape with field 12 and list 13; older clients get a shape without them). Bifrost's client sends its cosmetics and mod items when it arrives and uses it in StartCover and SpectateFeed.

### 2446 · Room request failed
`var_5332` / `_-73p` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.ROOM_FAILED`

Creating or joining a room failed. Shows the message (as a known error key if it is one), closes the room-code and public-games screens, and passes the code to the room state.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | code | uint | (unclear; Bifrost sends 0) |
| 2 | has message | bool | |
| 2.1 | message | string | only if 2; error key or plain text |

**Needs:** 33 create or 38 join.
**Gives:** an error popup.
**Bifrost's server:** `bh_server/lobby.py` `failed`.

### 2447 · Room full, spectate instead?
`var_5521` / `_-i2p` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.FULL_SPECTATE`

The room the client tried to join is full; closes the public-games list and opens the "game full" screen that offers to spectate it.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | room number | uint | |

**Needs:** 38 join.
**Gives:** the "game full" screen.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.join_room` (custom rooms only).

### 2448 · Room settings changed
`var_730` / `_-d3Z` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.SETTINGS_CHANGED`

New settings for the current room; the client applies them and redraws character select, the lobby tab and the party screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | settings | | the settings block of 2445 field 3 |

**Needs:** a room.
**Gives:** current room settings.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` (37 settings), also echoed back to refuse a change in a tournament room.

### 2449 · Player or bot joined
`var_14194` / `_-96V` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.JOINED`

Adds a bot or a player slot. For a player it shows "has joined" in chat and on the slot; if it is the receiver itself and the flag is set, the receiver has gone from spectator to player.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | bot | bool | |
| 1.1 | bot level | uint | only if bot |
| 1.2 | team | uint | only if bot |
| 1.3 | ready | uint | only if bot |
| 1.4 | entry | roster entry | only if not bot; as 2445 field 15 |
| 1.5 | from spectator | bool | only if not bot |

**Needs:** a room.
**Gives:** a new slot.
**Bifrost's server:** `bh_server/lobby.py` `join_room`, `spectator_plays`, `manage_party` (44 add bot), `training_bot`; `bh_server/social.py` `_move_into`.

### 2450 · Room member renamed
`var_13438` / `_-w5E` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.NAME_CHANGED`

Updates a room member's name and clan everywhere (character select, and the scoreboard during a match).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | name | string | |
| 3 | clan | string | |

**Needs:** a room.
**Gives:** new name.
**Bifrost's server:** `bh_server/lobby.py` `renamed`.

### 2451 · Room ratings
`var_107` / `_-3` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.ROOM_RATINGS`

Ratings of the room's players for the queued ranked playlist, and optionally a team rating; shown on character select.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player ratings | list, `bool` before each item | same row as 2431 field 23 |
| 2 | has team rating | bool | |
| 2.1 | rating ladder id | small | only if 2 |
| 2.2 | player a | uint | only if 2 |
| 2.3 | player b | uint | only if 2 |
| 2.4 | games | uint | only if 2 |
| 2.5 | wins | uint | only if 2 |
| 2.6 | rating | small | only if 2 |
| 2.7 | stars | small | only if 2 |
| 2.8 | peak rating | small | only if 2 |

**Needs:** a ranked room.
**Gives:** ratings shown in the room.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.room_ratings` (2v2 team-rated playlists).

### 2452 · Unused
`var_11246` / `_-g20` · not sent or handled by the 11.00 client or Bifrost's server

### 2453 · Kicked from the room
`var_13891` / `_-21M` · server → client · TCP lobby · extra u32 A · Bifrost: `social.KICKED`

The receiver (or one of its guests) was removed from the room. For the receiver it closes character select, returns to the menu and shows the reason; for a guest it shows "guest kicked".

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | reason | uint | 1 banned from the room, 2 kicked, 3 playlist does not exist |
| 2 | guest | uint | 0 = the receiver; otherwise the guest number |

**Needs:** a room.
**Gives:** the client out of the room.
**Bifrost's server:** `bh_server/social.py` `_kick` (39 kick, 40 ban).

### 2454 · Slot un-readied
`var_9474` / `_-ge` · server → client · TCP lobby · extra u32 A · Bifrost: none

Sets a slot back to not ready and unlocks its picks on character select.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | bot | bool | |
| 2 | player id | uint | only if 1 is false |
| 3 | sub-slot or bot slot | uint | |
| 4 | (unclear) | bool | passed to the slot's display |
| 5 | (unclear) | bool | passed to the pick reset |

**Needs:** a room.
**Gives:** slot not ready.
**Bifrost's server:** not sent.

### 2455 · New host
`var_4855` / `_-c5L` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.NEW_HOST`

Makes another member the room's host and refreshes the host-only parts of the screens.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | host id | uint | |
| 2 | (unclear) | uint | Bifrost sends 0 |

**Needs:** a room.
**Gives:** new host.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.leave`; `bh_server/social.py` `_make_leader` (82).

### 2456 · Room data out of sync
`var_3190` / `_-k2S` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.OUT_OF_SYNC`

Closes character select, returns to the menu and shows `Error_DATA_OUT_OF_SYNC`.

No payload.

**Needs:** a room request the server cannot match to a room.
**Gives:** the client back at the main menu.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` when 41/43 arrive outside a room.

### 2457 · Crossplay setting conflict
`var_147` / `_-R9` · server → client · TCP lobby · extra u32 A · Bifrost: none

Closes character select, returns to the menu and shows `Error_FAIL_CROSSPLAY_SETTING`.

No payload.

**Needs:** (unclear)
**Gives:** the client back at the main menu.
**Bifrost's server:** not sent.

### 2458 · Picks reset
`var_111` / `_-S6i` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.PICKS_RESET`

Resets one slot's picks on character select.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | bot | bool | |
| 2 | player id | uint | only if 1 is false |
| 3 | sub-slot or bot slot | uint | (Bifrost passes the refused loadout index) |

**Needs:** a room.
**Gives:** picks cleared for that slot.
**Bifrost's server:** `bh_server/lobby.py` `picks_reset_packet`, from `Lobby.handle` when a locked-in legend is switched off.

### 2459 · Level voted or struck
`var_4765` / `_-XO` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.LEVEL_VOTED`

Shows a player's vote or strike on the level-select screen (and the broadcast overlay).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (unclear) | bool | also needed for the overlay update |
| 2 | player id | uint | |
| 3 | slot | uint | |
| 4 | level id | uint | |
| 5 | all but | bool | |
| 6 | (unclear) | small | |
| 7 | (unclear) | bool | |

**Needs:** level select open.
**Gives:** updated vote state.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.level_select` (57).

### 2460 · Level select opened
`var_14156` / `_-d2T` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.LEVEL_SELECT_OPEN`

Opens level select (when the flag is set): applies the revealed picks, stores a value, hides the scoreboard for spectators and shows the level-select screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | open | bool | |
| 1.1 | picks | only if 1 | picks block |
| 1.1.1 | loadouts per player | uint | |
| 1.1.2 | players | list, `bool` before each item | player id uint, sub-slot uint, uint (unclear), then per loadout: random bool, legend id uint, skin id uint, 2 × uint (unclear); applied only to the receiver's own empty slots |
| 1.2 | (unclear) | uint | only if 1 |

**Needs:** the room's members are locked in.
**Gives:** the level-select screen.
**Bifrost's server:** `bh_server/lobby.py` `open_level_select` (11.00 clients get field 1.2). Bifrost's client (P2PMatch) prepares its peer pings when it arrives.

### 2461 · Bot level changed
`var_15462` / `_-C6C` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.BOT_LEVEL_CHANGED`

Sets a bot slot's level.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | slot | uint | |
| 2 | level | uint | |

**Needs:** a room with bots.
**Gives:** bot level.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.manage_party` (46).

### 2462 · Unused
`var_13623` / `_-c5j` · not sent or handled by the 11.00 client or Bifrost's server

### 2463 · Server message (with number)
`var_6619` / `_-Q2w` · server → client · connection not established · no extra u32 · Bifrost: none

Like 2465 with a leading number: shows the message and disconnects when asked.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (unclear) | uint | not used |
| 2 | message | string | |
| 3 | disconnect | bool | |

**Needs:** nothing.
**Gives:** as 2465.
**Bifrost's server:** not sent.

### 2464 · Unused
`var_6260` / `_-C1K` · not sent or handled by the 11.00 client or Bifrost's server

### 2465 · Server message
`var_11870` / `_-A18` · server → client · TCP lobby · extra u32 A · Bifrost: `social.SERVER_MESSAGE`

Shows a message popup. Some texts are special: an "Incorrect Version" message disconnects and shows the update screen, "Server is restarting" sets a restart flag, cross-progression keys get their own popup. With the flag set the client disconnects from the server. Type 16 uses the same handler.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | message | string | text or string key |
| 2 | disconnect | bool | |

**Needs:** nothing.
**Gives:** a popup, maybe a disconnect.
**Bifrost's server:** `bh_server/social.py` `kick` and `bh_server/protocol.py` `signed_out_elsewhere` (both with false, followed by 12002).

### 2466 · Game server assignment (rejoin)
`var_8547` / `_-uO` · server → client · TCP lobby · extra u32 A · Bifrost: none

Same fields as 2434, but the client first closes a screen and, once connected, sends 10405 instead of 10400.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | level id | uint | |
| 3 | ticket | string | |
| 4 | host | string | |
| 5 | TCP port | uint | |
| 6 | UDP port | small | |
| 7 | use Network Next | bool | |

**Needs:** a running match to return to (unclear).
**Gives:** game-server connections.
**Bifrost's server:** not sent.

### 2467 · Unused
`var_14386` / `_-T6V` · not sent or handled by the 11.00 client or Bifrost's server

### 2468 · Store currency changed
`var_12724` / `_-m1Q` · server → client · TCP lobby · extra u32 A · Bifrost: none

Sets the currency id used for real-money idol prices (login field 13).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | currency | byte | |

**Needs:** login.
**Gives:** store prices in that currency.
**Bifrost's server:** not sent.

### 2469 · Unused
`var_6990` / `_-26j` · not sent or handled by the 11.00 client or Bifrost's server

### 2470 · Mute state
`var_14041` / `_-R1l` · server → client · TCP lobby · extra u32 A · Bifrost: `reports.MUTE_STATE`

Sets whether the receiver has muted a player; updates the lobby hub and the scoreboard.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | state | uint | Bifrost: 1 muted, 2 unmuted |

**Needs:** login (the full list comes in 2431 field 25).
**Gives:** mute entry.
**Bifrost's server:** `bh_server/reports.py` `after_login` and `mute`.

### 2471 · Profile picks
`var_14800` / `_-K6s` · server → client · TCP lobby · extra u32 A · Bifrost: `picks.LOADOUT`

The player's profile choices: emotes, emojis, avatar, title, plus per-legend values. The client loads them into its store/inventory object, keeps a copy of the packet, and refreshes the social hub and character select.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | account key | string | any value accepted |
| 2 | (unclear) | uint | default 210 in the client |
| 3 | (unclear) | uint | |
| 4 | per-legend values | list, `bool` before each item | legend id uint, value uint (unclear) |
| 5 | emotes | 8 × uint | |
| 6 | emojis | 20 × uint | |
| 7 | avatar | uint | |
| 8 | title | uint | |

**Needs:** login.
**Gives:** profile picks.
**Bifrost's server:** `bh_server/picks.py` `loadout_packet`, from `Desk.after_login`. Bifrost's client (ProfilePicks) checks it against the picks it saved.

### 2472 · Queue answer
`var_7261` / `_-Q3X` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.QUEUE_WAITING`

Answer to "Ready for Battle". For spectators it shows or hides the waiting-queue screen. For players, when accepted, it applies the picks block and starts queue state; the room's searching flag follows the answer.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | accepted | bool | |
| 2 | picks | picks block, as 2460 field 1.1 | only if 1 and the receiver is a player |

**Needs:** 31 Ready for Battle from the room's host.
**Gives:** queue state.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` (31).

### 2473 · Friend list unavailable
`var_6571` / `_-X2T` · server → client · TCP lobby · extra u32 A · Bifrost: none

Sets the flag that makes the friends screen show its "unavailable" panel; 2406 clears it.

No payload.

**Needs:** nothing.
**Gives:** friends screen in "unavailable" state.
**Bifrost's server:** not sent.

### 2474 · Left the chat
`var_12199` / `_-b2w` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.CHAT_LEFT`

Prints "X has left the chat." and removes the player from the participant list.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |

**Needs:** 2401.
**Gives:** smaller participant list, chat line.
**Bifrost's server:** `bh_server/lobby.py` `chat_left_packet`, from `Lobby.leave_match` and `Lobby.leave`.

### 2475 · Region availability
`var_13239` / `_-Y5a` · server → client · TCP lobby · extra u32 A · Bifrost: none

Replaces the per-region availability flags (login field 19) and refreshes the waiting-queue screen while queueing.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | available | one bool per region in the game's region table, no count | |

**Needs:** login.
**Gives:** region availability.
**Bifrost's server:** not sent.

### 2476 · Spectate failed
`var_14982` / `_-lz` · server → client · TCP lobby · extra u32 A · Bifrost: none

Puts the client back in menu state and shows "Error in Spectate. Try Again."

No payload.

**Needs:** a spectate attempt.
**Gives:** an error popup.
**Bifrost's server:** not sent.

### 2477 · Rejoin offer
`var_2709` / `_-e3F` · server → client · TCP lobby · extra u32 A · Bifrost: `rejoin.PROMPT`

Opens the rejoin screen so the player can return to a match still running.

No payload.

**Needs:** the player has a seat in a running match.
**Gives:** the rejoin screen.
**Bifrost's server:** `bh_server/rejoin.py` `prompt`.

### 2478 · Ranked teams
`var_6215` / `_-i15` · server → client · TCP lobby · extra u32 A · Bifrost: `stats.TEAMS`

Replaces the player's list of 2v2 ranked teams.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | teams | list, `bool` before each item | |
| 1.1 | player id | uint | |
| 1.2 | teammate id | uint | |
| 1.3 | team name | string | |
| 1.4 | games | uint | |
| 1.5 | wins | uint | |
| 1.6 | rank | uint | |
| 1.7 | stars | uint | |
| 1.8 | rating | uint | |

**Needs:** login.
**Gives:** the 2v2 team list.
**Bifrost's server:** `bh_server/stats.py` `teams_packet`, from `Desk.after_login` and after rated results.

### 2479 · Ladder rank
`var_7408` / `_-Ax` · server → client · TCP lobby · extra u32 A · Bifrost: `stats.RANK`

Sets the rank and region of a known rating (a player's, or a team's when the flag is set).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | rating ladder id | uint | |
| 3 | rank | uint | |
| 4 | team | bool | |
| 4.1 | teammate id | uint | only if 4 |
| 5 | region | uint | |

**Needs:** the rating row known (2431 field 23 or 2451).
**Gives:** rank shown with the rating.
**Bifrost's server:** `bh_server/stats.py` `rank_packet` builds it, but nothing calls it.

### 2480 · Public room list
`var_11956` / `_-MN` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.PUBLIC_ROOMS`

The answer to 94 (list public rooms). The client empties its public-room list, reads one record per open custom room, filters out full rooms and rooms whose filters the player turned off, and redraws the room browser.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | another room follows | bool | before each room; false ends the list |
| 1.1 | room number | uint | the room to join |
| 1.2 | second id | uint | (unclear); Bifrost writes the room number again |
| 1.3 | players | uint | players (and bots) in the room |
| 1.4 | limit | uint | room capacity; full rooms can be hidden |
| 1.5 | description | string | the room's description text |
| 1.6 | (unknown) | string | (unclear); Bifrost writes "" |
| 1.7 | (unknown) | uint | used with fields 1.5 and 1.8 for display (unclear, maybe platform); Bifrost writes 0 |
| 1.8 | host name | string | |
| 1.9 | room option | uint | a settings byte the browser can filter on (unclear); Bifrost sends settings `b4224` |
| 1.10 | host id | uint | |
| 1.11 | timestamp | uint | Bifrost writes the current Unix time |
| 1.12 | flag | bool | (unclear); Bifrost sends settings `b6100` |
| 1.13 | select mode | small | the room's legend-select mode |
| 1.14 | game settings | 15 × uint | the room's match settings: 1 flags (team bit), 3 match time in seconds, 5 stocks, 6 scoring type; the rest (unclear) |
| 1.15 | player ids | bool-list of uint | `bool` true before each player id, false ends |
| 1.16 | spectator ids | bool-list of uint | same shape |
| 1.17 | started | bool | the match is already running |

**Needs:** sent after 94 (list public rooms).
**Gives:** the room browser list (`GameCore.var_1046`) and its filtered view, redrawn.
**Bifrost's server:** `bh_server/lobby.py` `public_rooms_packet`, sent from the lobby handler on 94; lists custom public rooms with full game settings, up to 100.

### 2481 · Public room list empty
`var_2270` / `_-16K` · server → client · TCP lobby · extra u32 A · Bifrost: none

Clears the public room browser: the client empties both the full and the filtered room lists and redraws the screen.

No payload.

**Needs:** nothing.
**Gives:** an empty room browser.
**Bifrost's server:** not sent (Bifrost sends 2480 with an empty list instead).

### 2482 · Region and servers set
`var_14552` / `_-k4n` · server → client · TCP lobby · extra u32 A · Bifrost: `playerprefs.REGION_SET`

Confirms the player's matchmaking region and server selection. The client stores both, clears a pending-change flag, and refreshes the room screen if it is open.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | region | uint | the chosen region |
| 2 | servers | uint | the allowed-servers bit mask |

**Needs:** sent after 96 (set region) or 97 (set servers).
**Gives:** `GameCore.var_10578` (region) and `var_5932` (servers).
**Bifrost's server:** `bh_server/playerprefs.py` `region_packet`, sent after saving 96 or 97.

### 2483 · Ratings
`var_10612` / `_-r2Y` · server → client · TCP lobby · extra u32 A · Bifrost: `stats.RATINGS`

Delivers ranked rating records. Each record is stored under the key "e<ladder>u<player>"; a rank already known for that key is kept when the new record has none.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | another record follows | bool | before each record; false ends |
| 1.1 | player id | uint | |
| 1.2 | ladder | small | the rating ladder (1v1, 2v2, ...) |
| 1.3 | games | uint | |
| 1.4 | wins | uint | |
| 1.5 | rating | small | current rating |
| 1.6 | star rating | small | the rank-tier value shown (50 is treated specially, see 2546) |
| 1.7 | peak rating | small | |

**Needs:** login.
**Gives:** entries in the client's rating table (`GameCore.var_11559`).
**Bifrost's server:** `bh_server/stats.py` `ratings_packet` / `rating_packets`, sent after login (`after_login`) and after a counted match.

### 2484 · Featured live matches
`var_1108` / `_-h2H` · server → client · TCP lobby · extra u32 A · Bifrost: none

A list of matches running now that the player can watch. The loading screen cycles through them and shows "name (legend) vs name (legend)"; choosing one sends the match id back to spectate it.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | another match follows | bool | before each match; false ends |
| 1.1 | match id | uint | sent back to spectate it |
| 1.2 | (unknown) | uint | (unclear, maybe the playlist) |
| 1.3 | age | uint | seconds since the match started; the client turns it into a start time |
| 1.4 | another player follows | bool | before each player; false ends |
| 1.4.1 | name | string | |
| 1.4.2 | legend | uint | legend id, shown by name |
| 1.4.3 | (unknown) | uint | (unclear) |
| 1.4.4 | (unknown) | uint | (unclear) |

**Needs:** nothing known.
**Gives:** the featured-match list (`GameCore.var_3145`), used by the loading screen.
**Bifrost's server:** not sent.

### 2485 · Guild system message
`var_5886` / `_-R4e` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.SYSTEM`

A guild error or notice by code. The client looks the code up in the guild message table, localises it, adds the extra text and prints it in the Clan chat channel.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | code | uint | message code (e.g. 7 cannot invite, 8 already in a clan, 16 leader cannot quit) |
| 2 | extra text | string | appended after the message |

**Needs:** a guild action (100–124) that failed.
**Gives:** a line in Clan chat; marks the guild service as reachable.
**Bifrost's server:** `bh_server/clans.py` `_fail` (non-create failures), with "" as the extra text.

### 2486 · Guild creation failed
`var_7468` / `_-D1a` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.CREATE_FAILED`

Tells the guild-creation screen why creating failed; the screen shows the error for the code.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | code | uint | failure code (e.g. 28 bad name length, 29 bad characters, 30 name taken, 34 already in a guild, 38 not allowed) |

**Needs:** sent after 100 (create guild).
**Gives:** an error on the Guilds Create screen.
**Bifrost's server:** `bh_server/clans.py` `_fail(create=True)` from the 100 handler.

### 2487 · Guild state
`var_4338` / `_-Q15` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.STATE`

The full guild snapshot: the player's guild, its members, pending applicants, and the player's own applications. With a guild, the client fills the guild model, posts the welcome, chat instructions and guild notice in chat, and refreshes every guild screen; without one, it clears the guild.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (unknown) | uint | read and ignored; Bifrost writes 0 |
| 2 | in a guild | bool | fields 2.x follow only if true |
| 2.1 | guild id | uint | |
| 2.2 | name | string | |
| 2.3 | emblem | small | avatar id used as emblem |
| 2.4 | tier | uint | |
| 2.5 | last week's place | uint | |
| 2.6 | (unknown) | string | (unclear); Bifrost writes "" |
| 2.7 | (unknown) | uint | (unclear); Bifrost writes 0 |
| 2.8 | level | uint | |
| 2.9 | level progress text | string | e.g. "1,200/5,000" |
| 2.10 | level progress | f32 | bar fill 0..1 |
| 2.11 | tier progress text | string | |
| 2.12 | tier progress | f32 | bar fill 0..1 |
| 2.13 | (unknown) | uint | (unclear); Bifrost writes 0 |
| 2.14 | total XP text | string | |
| 2.15 | notice | string | message of the day |
| 2.16 | tags | string | comma list of tag ids |
| 2.17 | recruiting | byte | 1 open to applications |
| 2.18 | description | string | |
| 2.19 | this week's place | uint | |
| 2.20 | last week's score | uint | |
| 2.21 | this week's score | uint | |
| 2.22 | duel state | uint | Bifrost writes 1 while the weekly duel runs; 2552 resets it to 0 |
| 2.23 | week end | uint | a time (Bifrost: seconds since login) |
| 2.24 | week end (again) | uint | (unclear) |
| 2.25 | (unknown) | uint | (unclear); Bifrost writes 0 |
| 2.26 | another member follows | bool | before each member; false ends |
| 2.26.1 | player id | uint | |
| 2.26.2 | name | string | |
| 2.26.3 | rank | uint | 0 leader, 1 officer, 2 member, 3 recruit |
| 2.26.4 | joined | uint | join time |
| 2.26.5 | last seen | uint | a time (unclear) |
| 2.26.6 | status | uint | online status |
| 2.26.7 | status detail | uint | |
| 2.26.8 | platform user id | string | (unclear); stored in the player cache |
| 2.26.9 | XP | uint | total guild XP given |
| 2.26.10 | week XP | uint | |
| 2.26.11 | XP (contribution) | uint | also set by 2510 |
| 2.26.12 | week XP (contribution) | uint | also set by 2510 |
| 2.26.13 | (unknown) | uint | read and not used |
| 2.26.14 | portrait | small | avatar id |
| 2.26.15 | 1v1 Elo | small | |
| 2.26.16 | 2v2 Elo | small | |
| 2.26.17 | platform | byte | |
| 2.26.18 | (unknown) | uint | also set by 2510 (unclear) |
| 2.27 | has applicants | bool | 2.27.x follow only if true |
| 2.27.1 | another applicant follows | bool | before each; each item is the 2502 application record (13 fields) |
| 3 | has own applications | bool | 3.x follow only if true |
| 3.1 | another follows | bool | before each pair; false ends |
| 3.1.1 | guild id | uint | a guild the player applied to |
| 3.1.2 | status | uint | 1 pending |

**Needs:** login (lobby), or a guild change (create, join, week turn).
**Gives:** the guild model (`GameCore.var_13103`), the player cache, chat lines, refreshed guild screens; opens the social hub when idle in the menu.
**Bifrost's server:** `bh_server/clans.py` `state` / `send_state`, sent on login (`online`), after creating or joining a guild and at the week turn.

### 2488 · Guild member status
`var_6422` / `_-15e` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.MEMBER_STATUS`

A guild member's online status changed; the member list redraws.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | status | uint | online status |
| 3 | status detail | uint | |

**Needs:** the player is in a guild (2487).
**Gives:** updated member status.
**Bifrost's server:** `bh_server/clans.py` `status_changed`, broadcast to online guild members.

### 2489 · Guild rank changed
`var_6067` / `_-wT` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.RANK_CHANGED`

A member was promoted or demoted. The client updates the member's rank (and the leader if the new rank is 0) and prints "X has promoted/demoted Y to <rank>." in Clan chat; a change across the officer line reloads the guild screen for the affected player.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | actor id | uint | who made the change |
| 2 | target id | uint | whose rank changed |
| 3 | target name | string | |
| 4 | old rank | uint | 0 leader … 3 recruit |
| 5 | new rank | uint | lower than old means promoted |

**Needs:** the player is in a guild (2487).
**Gives:** updated ranks and a chat line.
**Bifrost's server:** `bh_server/clans.py` `_dispatch` on 105, 106, 107 (two 2489s for a leadership handover).

### 2490 · Guild member left
`var_635` / `_-i4M` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.LEFT`

Someone left or was kicked. If it was the player, the guild is cleared and the guild screen reloads; otherwise the member is removed. A Clan chat line explains it ("has left", "has kicked … from the guild").

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | guild id | uint | |
| 2 | player id | uint | who left |
| 3 | kicked | bool | true if removed by an officer |
| 4 | guild name | string | used in "You have left guild …" |
| 5 | kicker name | string | used when kicked |

**Needs:** the player is in a guild (2487).
**Gives:** an updated member list, or no guild.
**Bifrost's server:** `bh_server/clans.py` `_dispatch` on 104 (kick), 108 (leave) and 101 (disband: one 2490 per online member).

### 2491 · Guild invite received
`var_11131` / `_-NS` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.INVITED`

An invitation to join a guild. Ignored if the player is already in one; otherwise it prints "UI_Guilds_Invited" in Clan chat, adds an inbox notification and refreshes the inbox.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | guild id | uint | |
| 2 | guild name | string | |
| 3 | inviter id | uint | |
| 4 | inviter name | string | |

**Needs:** another member sent 102 (invite) for this player.
**Gives:** an invite notification the player can answer with 103.
**Bifrost's server:** `bh_server/clans.py` `_dispatch` on 102.

### 2492 · Guild member joined
`var_8296` / `_-k3t` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.JOINED`

A new member joined the player's guild. The client adds them (unless it is the player), caches their platform data, prints "<name> has joined the clan via <how>!" and removes them from the applicant list.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | name | string | |
| 3 | rank | uint | |
| 4 | joined | uint | join time |
| 5 | last seen | uint | (unclear) |
| 6 | status | uint | online status |
| 7 | status detail | uint | |
| 8 | platform user id | string | (unclear) |
| 9 | XP | uint | |
| 10 | week XP | uint | |
| 11 | via | string | how they joined (e.g. "an invite") |
| 12 | portrait | small | |
| 13 | 1v1 Elo | small | |
| 14 | 2v2 Elo | small | |
| 15 | platform | byte | |

**Needs:** the player is in a guild (2487).
**Gives:** a new member in the list and a chat line.
**Bifrost's server:** `bh_server/clans.py` `_join` (after 103 accept or 118 accept); the joiner gets 2487 instead.

### 2493 · Guild notice changed
`var_4068` / `_-U2Z` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.NOTICE`

The guild's notice (message of the day) changed; the client stores it, prints it in the Status channel and closes the edit dialog.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | guild id | uint | |
| 2 | notice | string | |

**Needs:** an officer sent 109.
**Gives:** the new notice.
**Bifrost's server:** `bh_server/clans.py` `_dispatch` on 109.

### 2494 · Guild chat message
`var_12292` / `_-o1H` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.CLAN_CHAT`

A guild chat line. Dropped while the player's chat is restricted (2550); otherwise shown in the guild or officer channel with the sender's name.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | sender id | uint | |
| 2 | text | string | |
| 3 | officers only | bool | picks the officer channel |

**Needs:** the player is in a guild (2487).
**Gives:** a chat line.
**Bifrost's server:** `bh_server/clans.py` `_dispatch` on 110, to officers only when field 3 is true.

### 2495 · Guild service lost
`var_8308` / `_-z4e` · server → client · TCP lobby · extra u32 A · Bifrost: none

If guild data had been received, the client prints "UI_Guilds_Lost_Connection" in Clan chat, clears the guild and redraws the guild screens.

No payload.

**Needs:** guild data received earlier.
**Gives:** no guild until the next 2487.
**Bifrost's server:** not sent.

### 2496 · Guild XP update
`var_3437` / `_-42m` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.XP_UPDATE`

The guild's level and tier progress after XP was earned, plus one member's new total; the guild screens redraw.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | level | uint | |
| 2 | level progress | f32 | |
| 3 | level progress text | string | |
| 4 | (unknown) | bool | read and not used |
| 5 | tier | uint | |
| 6 | tier progress text | string | |
| 7 | tier progress | f32 | |
| 8 | (unknown) | bool | read and not used |
| 9 | player id | uint | the member who earned it |
| 10 | member XP | uint | their new total |

**Needs:** the player is in a guild (2487).
**Gives:** updated guild level, tier and member XP.
**Bifrost's server:** `bh_server/clans.py` `add_xp`, after a match result gives XP.

### 2497 · Guild XP gained
`var_3092` / `_-e4L` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.XP_GAINED`

Tells the player how much guild XP they just earned, as a "UI_Guilds_Gained_XP" line in the ClanXP chat channel.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | amount | uint | |
| 2 | guild name | string | |

**Needs:** the player is in a guild.
**Gives:** a chat line.
**Bifrost's server:** `bh_server/clans.py` `add_xp`, to the earner only.

### 2498 · Guild emblem changed
`var_9903` / `_-36b` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.EMBLEM`

The guild emblem changed; the guild screens redraw.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | emblem | small | avatar id |

**Needs:** an officer set it (Bifrost: 64 with the guild flag).
**Gives:** the new emblem.
**Bifrost's server:** `bh_server/clans.py` `_emblem`.

### 2499 · Guild tags changed
`var_10240` / `_-93L` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.TAGS_SET`

The guild's tags changed; the client parses them and redraws the guild settings screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | tags | string | comma list of tag ids |

**Needs:** an officer sent 115.
**Gives:** the new tags.
**Bifrost's server:** `bh_server/clans.py` `_dispatch` on 115.

### 2500 · Guild description changed
`var_2140` / `_-A4z` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.DESCRIPTION_SET`

The guild description changed; the client stores it, closes the edit dialog and redraws.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | description | string | |

**Needs:** an officer sent 116.
**Gives:** the new description.
**Bifrost's server:** `bh_server/clans.py` `_dispatch` on 116.

### 2501 · Guild recruiting changed
`var_7470` / `_-l4l` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.RECRUITING_SET`

Whether the guild accepts applications; the settings screen redraws if open.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | recruiting | byte | 1 open, 0 closed |

**Needs:** an officer sent 114.
**Gives:** the recruiting flag.
**Bifrost's server:** `bh_server/clans.py` `_dispatch` on 114.

### 2502 · Guild application
`var_10770` / `_-L2J` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.APPLICATION`

One application changed. If it is the player's own, the client updates its own-applications map (status 6 removes it); for officers, status 1 adds an applicant and status 6 removes one. The recruit and applicant screens redraw.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | guild id | uint | |
| 2 | player id | uint | the applicant |
| 3 | name | string | |
| 4 | (unknown) | uint | (unclear) |
| 5 | (unknown) | uint | (unclear) |
| 6 | (unknown) | uint | (unclear) |
| 7 | status | byte | 1 pending, 6 withdrawn/answered |
| 8 | (unknown) | bool + small | signed small (unclear) |
| 9 | (unknown) | uint | (unclear) |
| 10 | (unknown) | bool + small | signed small (unclear) |
| 11 | (unknown) | bool + small | signed small (unclear) |
| 12 | (unknown) | uint | (unclear) |
| 13 | tags | string | the applicant's tags |

**Needs:** 117 (apply / withdraw) or 118 (answer an applicant).
**Gives:** updated applications.
**Bifrost's server:** `bh_server/clans.py` `_application_row` from `_dispatch` on 117 and 118 (fields 4–13 written as zeros and "").

### 2503 · Guild recruit list page
`var_7349` / `_-5s` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.RECRUIT_PAGE`

One page of guilds that are recruiting. Page 0 clears the lists; on the last page the client sorts them several ways and redraws the recruit browser.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | page | uint | 0-based |
| 2 | page count | uint | |
| 3 | another guild follows | bool | before each guild; false ends |
| 3.1 | guild id | uint | entries with id 0 are skipped |
| 3.2 | name | string | |
| 3.3 | leader id | uint | |
| 3.4 | leader name | string | |
| 3.5 | members | uint | |
| 3.6 | capacity | uint | |
| 3.7 | emblem | uint | |
| 3.8 | tier | uint | |
| 3.9 | (unknown) | uint | (unclear); Bifrost writes 0 |
| 3.10 | XP | uint | |
| 3.11 | created | uint | creation time |
| 3.12 | tags | string | |

**Needs:** sent after 119 (recruit list).
**Gives:** the recruit browser lists.
**Bifrost's server:** `bh_server/clans.py` `_dispatch` on 119, as one page.

### 2504 · Guild mission list
`var_4808` / `_-W` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.MISSION_LIST`

The guild missions and their progress. Unknown mission ids are skipped; for chained missions the client shows the first one not yet in state 2. The missions screen redraws.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | another mission follows | bool | before each; false ends |
| 1.1 | mission id | uint | |
| 1.2 | (unknown) | uint | progress (unclear) |
| 1.3 | (unknown) | uint | (unclear) |
| 1.4 | state | uint | 2 moves on to the next mission in a chain (unclear, maybe "claimed") |

**Needs:** sent after 124 (missions).
**Gives:** the guild mission list.
**Bifrost's server:** `bh_server/clans.py` `_dispatch` on 124, always an empty list.

### 2505 · Guild battle log
`var_10188` / `_-y5H` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.BATTLE_LOG_LIST`

The guild's recent matches against other guilds; replaces the list and redraws the battle log screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | count | uint | number of entries |
| 1.1 | time | uint | Bifrost: seconds relative to login |
| 1.2 | playlist | small | |
| 1.3 | three teammates | 3 × (uint, string, small) | player id, name, (unclear small) |
| 1.4 | opponent guild id | uint | |
| 1.5 | opponent guild name | string | |
| 1.6 | opponent emblem | small | |
| 1.7 | opponent tier | byte | |
| 1.8 | three opponents | 3 × (uint, string, small) | player id, name, (unclear small) |
| 1.9 | points | bool + small | signed small; negative for a loss |

**Needs:** sent after 120 (battle log).
**Gives:** the battle log list.
**Bifrost's server:** `bh_server/clans.py` `_battle_log`, from `_dispatch` on 120.

### 2506 · Guild applicant removed
`var_6761` / `_-S4f` · server → client · TCP lobby · extra u32 A · Bifrost: none

Removes one player from the guild's applicant list and redraws the applicants screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |

**Needs:** the player is an officer with applicants (2487).
**Gives:** a shorter applicant list.
**Bifrost's server:** not sent (Bifrost uses 2502 with status 6).

### 2507 · Guild member portrait
`var_11079` / `_-j5U` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.MEMBER_AVATAR`

A member changed their avatar; the member list redraws.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | portrait | small | avatar id |

**Needs:** the player is in a guild.
**Gives:** the member's new portrait.
**Bifrost's server:** `bh_server/clans.py` `avatar_changed`.

### 2508 · Guild member Elo
`var_14779` / `_-A1` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.MEMBER_ELO`

A member's ratings after a match; the member list redraws.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | 1v1 Elo | small | |
| 3 | 2v2 Elo | small | |

**Needs:** the player is in a guild.
**Gives:** updated member ratings.
**Bifrost's server:** `bh_server/clans.py` `on_result`, after a match result.

### 2509 · Guild weekly score
`var_2790` / `_-R4p` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.WEEK_SCORE`

This week's score for a guild; applied only when it is the player's guild.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | guild id | uint | |
| 2 | score | uint | |

**Needs:** the player is in a guild.
**Gives:** the week score on the guild screen.
**Bifrost's server:** `bh_server/clans.py` `add_xp`. Handled by the guild model (`Class_68.method_6907`).

### 2510 · Guild member contributions
`var_2750` / `_-P1Q` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.CONTRIBUTIONS`

Updates members' contribution counters for the player's guild; the guild and member screens redraw.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | another entry follows | bool | before each; false ends |
| 1.1 | guild id | uint | entries for other guilds are ignored |
| 1.2 | player id | uint | |
| 1.3 | total XP | uint | |
| 1.4 | week XP | uint | |
| 1.5 | (unknown) | uint | (unclear); Bifrost writes 0 |

**Needs:** the player is in a guild.
**Gives:** updated member contributions.
**Bifrost's server:** `bh_server/clans.py` `add_xp`. Handled by `Class_68.method_4203`.

### 2511 · Guild leaderboard 1
`var_3353` / `_-x52` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.BOARDS[0]`

One page of the first guild leaderboard. Page 1 clears the list; the last page redraws the Guilds Leaderboard screen. 2512–2515 share this layout for the other boards.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | page | small | 1-based |
| 2 | page count | small | |
| 3 | rows | small | count of rows that follow |
| 3.1 | place | uint | |
| 3.2 | guild id | uint | |
| 3.3 | name | string | |
| 3.4 | emblem | uint | |
| 3.5 | last week's score | uint | |
| 3.6 | XP | uint | shown formatted |
| 3.7 | Elo | small | |
| 3.8 | this week's score | uint | |
| 3.9 | XP (again) | uint | shown formatted (unclear which XP) |
| 3.10 | tier | small | |
| 3.11 | leader id | uint | |
| 3.12 | leader name | string | |

**Needs:** sent after 121 (leaderboard) with board 0.
**Gives:** the board's rows (`Class_68.method_5326`).
**Bifrost's server:** `bh_server/clans.py` `_send_board`, sorted by last week's score.

### 2512 · Guild leaderboard 2
`var_2068` / `_-WQ` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.BOARDS[1]`

Second guild leaderboard; same layout as 2511.

Fields: as 2511.

**Needs:** 121 with board 1.
**Gives:** the board's rows (`Class_68.method_1540`).
**Bifrost's server:** `bh_server/clans.py` `_send_board`, sorted by this week's score.

### 2513 · Guild leaderboard 3
`var_9321` / `_-55X` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.BOARDS[2]`

Third guild leaderboard; same layout as 2511.

Fields: as 2511.

**Needs:** 121 with board 2.
**Gives:** the board's rows (`Class_68.method_9169`).
**Bifrost's server:** `bh_server/clans.py` `_send_board`, sorted by total XP.

### 2514 · Guild leaderboard 4
`var_6925` / `_-g3S` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.BOARDS[3]`

Fourth guild leaderboard; same layout as 2511.

Fields: as 2511.

**Needs:** 121 with board 3.
**Gives:** the board's rows (`Class_68.method_4933`).
**Bifrost's server:** `bh_server/clans.py` `_send_board`, sorted by Elo.

### 2515 · Guild leaderboard 5
`var_5360` / `_-510` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.BOARDS[4]`

Fifth guild leaderboard; same layout as 2511.

Fields: as 2511.

**Needs:** 121 with board 4.
**Gives:** the board's rows (`Class_68.method_3169`).
**Bifrost's server:** `bh_server/clans.py` `_send_board`, sorted by total XP.

### 2516 · Guild leaderboard empty
`var_202` / `_-k1s` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.BOARD_EMPTY`

Tells the Guilds Leaderboard screen there is nothing to show.

No payload.

**Needs:** sent after 121.
**Gives:** an empty leaderboard view.
**Bifrost's server:** `bh_server/clans.py` `_send_board` when no guild exists.

### 2517 · Guild player info
`var_8934` / `_-A3F` · server → client · TCP lobby · extra u32 A · Bifrost: none

Platform details for one player, stored in the client's player cache (used for platform icons and profile links on guild screens). Guild screens ask for it with 122 when a player is missing from the cache.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | platform | byte | |
| 3 | name | string | |
| 4 | platform user id | string | (unclear) |

**Needs:** sent after 122 (player info).
**Gives:** a player-cache entry.
**Bifrost's server:** not sent (122 is read and ignored).

### 2518 · Guild duel
`var_7081` / `_-W1H` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.DUEL`

One side of this week's guild duel: the player's own guild or the opponent. The client builds the duel record with its members and redraws the duel screens. An empty opponent record (duel id 0, not own) clears the opponent.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | present | bool | (unclear); Bifrost writes true for a real record |
| 2 | own guild | bool | true for the player's side |
| 3 | duel id | uint | Bifrost: the week number; 0 with field 2 false ends here |
| 4 | emblem | uint | |
| 5 | name | string | |
| 6 | (unknown) | uint | (unclear); Bifrost writes 0 |
| 7 | (unknown) | string | (unclear); Bifrost writes "" |
| 8 | tier | uint | |
| 9 | members | uint | |
| 10 | duel wins | uint | |
| 11 | place | uint | |
| 12 | week score | uint | |
| 13 | (unknown) | uint | subtracted from field 12 for a difference (unclear) |
| 14 | (unknown) | uint | (unclear) |
| 15 | (unknown) | uint | (unclear) |
| 16 | (unknown) | bool | (unclear) |
| 17 | member count | uint | number of member records |
| 17.1 | player id | uint | 0 skips the record |
| 17.2 | name | string | |
| 17.3 | portrait | uint | |
| 17.4 | rank | uint | |
| 17.5 | week XP | uint | |
| 17.6 | (unknown) | uint | subtracted from 17.5 (unclear) |
| 18 | online count | uint | members online |

**Needs:** sent after 123 (missions refresh / duel).
**Gives:** the duel records (`Class_68.method_9240`).
**Bifrost's server:** `bh_server/clans.py` `send_duel` / `_duel_record`, two packets per request.

### 2519 · Legend career stats
`var_8891` / `_-x4n` · server → client · TCP lobby · extra u32 A · Bifrost: `stats.LEGEND_CAREER`

Per-legend lifetime stats for the stats screen; stored by legend id, then the stats screen redraws.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | another legend follows | bool | before each; false ends |
| 1.1 | legend id | uint | |
| 1.2 | damage dealt | string | number as text |
| 1.3 | damage taken | string | |
| 1.4 | KOs | uint | |
| 1.5 | falls | uint | |
| 1.6 | self-KOs | uint | |
| 1.7 | team KOs | uint | |
| 1.8 | time played | uint | seconds |
| 1.9 | games | uint | |
| 1.10 | wins | uint | |
| 1.11 | damage unarmed | string | |
| 1.12 | damage thrown | string | |
| 1.13 | damage weapon 1 | string | |
| 1.14 | damage weapon 2 | string | |
| 1.15 | damage gadgets | string | |
| 1.16 | KOs unarmed | uint | |
| 1.17 | KOs thrown | uint | |
| 1.18 | KOs weapon 1 | uint | |
| 1.19 | KOs weapon 2 | uint | |
| 1.20 | KOs gadgets | uint | |
| 1.21 | weapon 1 held | uint | seconds |
| 1.22 | weapon 2 held | uint | seconds |

**Needs:** sent after 157 (career stats request).
**Gives:** legend stats (`GameCore.var_8731`).
**Bifrost's server:** `bh_server/stats.py` `career_packets`, on 157 and after a counted match.

### 2520 · Account career stats
`var_8947` / `_-B4C` · server → client · TCP lobby · extra u32 A · Bifrost: `stats.ACCOUNT_CAREER`

Account-wide lifetime stats for the stats screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | games | uint | |
| 2 | wins | uint | |
| 3 | bomb damage | string | |
| 4 | mine damage | string | |
| 5 | spikeball damage | string | |
| 6 | flyby damage | string | Bifrost's name for it (unclear which object) |
| 7 | snowball hits | uint | |
| 8 | bomb KOs | uint | |
| 9 | mine KOs | uint | |
| 10 | spikeball KOs | uint | |
| 11 | flyby KOs | uint | |
| 12 | snowball KOs | uint | |

**Needs:** sent after 157, together with 2519.
**Gives:** account stats (`GameCore.var_12765`).
**Bifrost's server:** `bh_server/stats.py` `career_packets`.

### 2521 · Twitch account status
`var_5003` / `_-429` · server → client · TCP lobby · extra u32 A · Bifrost: none

The player's Twitch link state. Status 2 means "No twitch account found." when the player tries a Twitch action.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | status | uint | 1 linked, 2 none (default 2) |
| 2 | Twitch name | string | |

**Needs:** nothing known.
**Gives:** `GameCore.var_8693` / `var_4876`.
**Bifrost's server:** not sent.

### 2522 · Twitch link start
`var_12047` / `_-a5y` · server → client · TCP lobby · extra u32 A · Bifrost: none

Opens the Twitch OAuth authorize page in the browser, with the given value as the OAuth state.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | state token | string | appended as `state=` |

**Needs:** the player asked to link Twitch (unclear which request).
**Gives:** a browser window to twitch.tv.
**Bifrost's server:** not sent.

### 2523 · Twitch Mammoth Coins received
`var_3883` / `_-j4h` · server → client · TCP lobby · extra u32 A · Bifrost: none

Coins granted (counted by telemetry as "SessionIdolsTwitch"). The client sets the new balance, adds an inbox notification for the amount and refreshes the inbox and store.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | amount | uint | coins granted |
| 2 | new balance | bool + uint | signed uint; the Mammoth Coin (idol) balance |

**Needs:** nothing known.
**Gives:** `GameCore.var_2737` (coin balance) and a notification.
**Bifrost's server:** not sent.

### 2524 · Twitch account linked
`var_11603` / `_-i7` · server → client · TCP lobby · extra u32 A · Bifrost: none

Marks Twitch as linked (status 1) and stores the name.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | Twitch name | string | |

**Needs:** a finished Twitch link (2522).
**Gives:** `GameCore.var_8693` = 1 and the name.
**Bifrost's server:** not sent.

### 2525 · Entitlement granted
`var_7771` / `_-y1y` · server → client · TCP lobby · extra u32 A · Bifrost: none

A purchase or DLC entitlement was granted. The client refreshes the store and inventory screens, shows the "new item" popup for entitlements with a display name and art (in the menu or store), records telemetry, and counts any included coins as DLC coins.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | entitlement id | uint | index into the entitlement table |

**Needs:** a store purchase or DLC claim.
**Gives:** the new-item popup and refreshed store.
**Bifrost's server:** not sent.

### 2526 · Unknown (check counter)
`var_6744` / `_-M5S` · server → client · TCP lobby · extra u32 A · Bifrost: none

If the flag is true the client resets a counter to 0; otherwise it increments it and compares it with 5, with no visible effect.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | ok | bool | true resets the counter |
| 2 | (unknown) | uint | read and not used |

**Needs:** unknown.
**Gives:** `GameCore.var_11904` (unused elsewhere).
**Bifrost's server:** not sent.

### 2527 · Account XP
`var_2626` / `_-D5Z` · server → client · TCP lobby · extra u32 A · Bifrost: `stats.ACCOUNT_XP`

The player's account XP and level; the XP bar redraws.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | XP | uint | total account XP |
| 2 | level | small | |
| 3 | level start | uint | XP at the start of this level |
| 4 | next level | uint | XP needed for the next level |

**Needs:** a counted match (in Bifrost; the 11.00 game may also send it at other times).
**Gives:** `GameCore.var_14047`, `var_10646`, `var_5672`, `var_7784`.
**Bifrost's server:** `bh_server/stats.py` `progress_packets`, after a match. Bifrost's client logs it in `MatchReport`.

### 2528 · Feature flags
`var_9335` / `_-76D` · server → client · TCP lobby · extra u32 A · Bifrost: `protocol.FEATURE_FLAGS`

Server-side switches. The client resets every flag-driven setting, then applies each key/value pair; a key is cut at its first "-" so several entries can share a name. Unknown keys are ignored. Known keys: disableStaticEvent, disablechallenges, disabledna, disableguilds, disablehero, disablelevel, disablepostmatchinvite(s), disablegame / disablescoring / disablescoringtype, disabletcpfallback, displaygameclientupdatewarning (a number above 36 shows the outdated-version warning), enablePurchaseConfirmation, featuringnewaccountxp, featuringweightadjustments, inputdelaystrategy, terminatedna.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | another flag follows | bool | before each pair; false ends |
| 1.1 | key | string | flag name, optionally with a "-suffix" |
| 1.2 | value | string | "TRUE", a number, or a list, by key |

**Needs:** sent after 2431 (login accepted).
**Gives:** the flag settings (`GameApp.method_7086`), a record of all pairs, and `GameApp.var_1170` = received.
**Bifrost's server:** `bh_server/protocol.py` `feature_flags`, right after login; always sends disabledna=TRUE and adds the configured `feature_flags` (skipping DNA keys).

### 2529 · Unused
`var_14960` / `_-y36` · not sent or handled by the 11.00 client or Bifrost's server

### 2530 · Ranked opponent record
`var_4298` / `_-O4D` · server → client · TCP lobby · extra u32 A · Bifrost: none

A win-loss pair for the ranked 1v1/2v2 match preview screens (probably the record against this opponent). The client stores it and, if a ranked preview is open, shows "wins - losses" or two numbers, or hides it when the flag is set.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | none | bool | true: no record, fields 2–3 absent |
| 2 | games | uint | only if field 1 is false |
| 3 | wins | uint | only if field 1 is false; losses = games − wins |

**Needs:** a ranked match found (unclear).
**Gives:** `GameCore.var_4175` and the preview display.
**Bifrost's server:** not sent.

### 2531 · Arcade best scores
`var_12610` / `_-n5s` · server → client · TCP lobby · extra u32 A · Bifrost: `stats.ARCADE_BESTS`

The player's best arcade run per legend; the client stores them, marks them loaded and redraws.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | another legend follows | bool | before each; false ends |
| 1.1 | legend id | uint | |
| 1.2 | score | uint | |
| 1.3 | placement | uint | |

**Needs:** sent after 156 (arcade request).
**Gives:** `GameCore.var_6952` by legend.
**Bifrost's server:** `bh_server/stats.py` `arcade_packet`, on 156.

### 2532 · Unused
`var_4625` / `_-c18` · not sent or handled by the 11.00 client or Bifrost's server

### 2533 · Account link URL
`var_8828` / `_-p24` · server → client · TCP lobby · extra u32 A · Bifrost: none

Answer to a cross-platform account-link request: on success the client opens oauth.brawlhalla.com/linkaccount.php with the platform, the key and the language; on failure it shows "error has occured".

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | ok | bool | |
| 2 | key | string | only if field 1 is true |

**Needs:** the player asked to link accounts (unclear which request).
**Gives:** a browser window.
**Bifrost's server:** not sent.

### 2534 · Purchase telemetry
`var_13202` / `_-05y` · server → client · TCP lobby · extra u32 A · Bifrost: none

Details of a completed in-app purchase, sent on as a "player.iap" telemetry event when telemetry is on.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | store transaction id | string | |
| 2 | Houston transaction id | string | |
| 3 | item id | string | |

**Needs:** a store purchase.
**Gives:** a telemetry event only.
**Bifrost's server:** not sent.

### 2535 · Inventory refresh
`var_12379` / `_-G1f` · server → client · TCP lobby · extra u32 A · Bifrost: none

If the inventory screen (social hub inventory) is open, marks all its sections dirty and redraws it.

No payload.

**Needs:** nothing.
**Gives:** a redrawn inventory screen.
**Bifrost's server:** not sent.

### 2536 · Live content state
`var_8033` / `_-j1j` · server → client · TCP lobby · extra u32 A · Bifrost: none

The current battle pass, timed event, timed promotions, client theme and whether ranked cups are on. The client updates each and refreshes the store, battle pass and main menu screens.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (battle pass) | uint | (unclear) |
| 2 | battle pass id | uint | |
| 3 | (battle pass) | uint | (unclear) |
| 4 | (battle pass) | uint | passed to the battle pass screen (unclear) |
| 5 | timed event id | uint | |
| 6 | timed event value | uint | (unclear, maybe phase) |
| 7 | promotions | bool-list of uint | `bool` true before each timed promotion id |
| 8 | client theme | uint | menu theme id |
| 9 | cups enabled | bool | shows the Cup mode in ranked options |

**Needs:** login (unclear).
**Gives:** active battle pass, event, promotions, theme.
**Bifrost's server:** not sent.

### 2537 · Store rotation and playlists
`var_3165` / `_-s4I` · server → client · TCP lobby · extra u32 A · Bifrost: none

Replaces the store's rotating lists, the legend rotation and the playlist definitions, then redraws. Only the outline is certain; the playlist record is long.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | another item follows | bool | list 1: marked store items |
| 1.1 | store item id | bool + small | signed small |
| 1.2 | value | uint | (unclear) |
| 2 | another entry follows | bool | list 2: entries with art (unclear) |
| 2.1 | id | bool + small | signed small |
| 2.2 | value | uint | (unclear) |
| 3 | another item follows | bool | list 3 |
| 3.1 | store item id | uint | |
| 4 | another legend follows | bool | legend rotation (unclear) |
| 4.1 | legend id | uint | |
| 4.2 | value | uint | (unclear) |
| 5 | another playlist follows | bool | list A of playlists |
| 5.1 | playlist record | see below | then a uint (unclear) |
| 6 | another playlist follows | bool | list B of playlists |
| 6.1 | playlist record | see below | then a uint |
| 7 | has a featured playlist | bool | one more record + uint if true (unclear, maybe Brawl of the Week) |

Playlist record (`Class_309.method_2226`), in order: string key, small playlist id, small ladder id, string type (e.g. "BrawlOfTheWeek"), string display-name key, string, bool + optional string, small, string, small, small, bool, small, small, small, small count + that many strings (game modes), four bools (the third is used as "team"), small (game count compared in ranked previews), bool, bool + optional string, bool, five strings, small count + that many strings, and more fields not traced.

**Needs:** login (unclear).
**Gives:** store rotation, legend rotation, playlist tables.
**Bifrost's server:** not sent.

### 2538 · Emote shown
`var_14651` / `_-H2Q` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.EMOTE_SHOWN`

A player in the room used an emote; the client plays it on that player's slot.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | sub-slot | uint | |
| 3 | emote id | uint | |

**Needs:** a room member sent 159 (emote).
**Gives:** an emote on screen.
**Bifrost's server:** `bh_server/lobby.py` lobby handler on 159, broadcast to the room (not from spectators).

### 2539 · Anti-cheat message
`var_7494` / `_-s2R` · server → client · TCP lobby · extra u32 A · Bifrost: none

Bytes for the Epic anti-cheat client, passed straight to the Epic AIR extension. Ignored when that extension is not in use.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | data | small + bytes | small length, then that many bytes |

**Needs:** anti-cheat running (2540).
**Gives:** nothing in game state.
**Bifrost's server:** not sent.

### 2540 · Anti-cheat start
`var_14014` / `_-D4v` · server → client · TCP lobby · extra u32 A · Bifrost: none

Starts the Epic anti-cheat login, using the frame's optional u32 and a client-held byte list. Ignored when the Epic extension is not in use.

No payload. (The handler reads only the frame's extra u32.)

**Needs:** the Epic extension active.
**Gives:** anti-cheat session started.
**Bifrost's server:** not sent.

### 2541 · Anti-cheat lock-in refused
`var_6321` / `_-750` · server → client · TCP lobby · extra u32 A · Bifrost: none

Shows why the player can't lock in: "Error_EAC_LOCK_IN_BAN", "Error_EAC_LOCK_IN_DELAY" or "Error_EAC_LOCK_IN_NOT_RUNNING".

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | delayed | bool | |
| 2 | banned | bool | takes priority |

**Needs:** a lock-in attempt.
**Gives:** an error popup.
**Bifrost's server:** not sent.

### 2542 · Rematch vote
`var_13442` / `_-E6U` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.REMATCH_STATE`

A player's rematch choice on the end-of-match screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |
| 2 | state | byte | 1 wants a rematch, 2 declines |

**Needs:** an open rematch window (2543).
**Gives:** the vote shown on the end screen.
**Bifrost's server:** `bh_server/lobby.py` `rematch_want` (on 162) and `leave_match` (on 85, state 2).

### 2543 · Rematch window
`var_1990` / `_-W1C` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.REMATCH_WINDOW`

Opens the rematch timer on the end-of-match screen and, in a series, the score so far. Duplicate ids with different win counts abort the series part.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | has timer | bool | |
| 2 | time | uint | ms until the window closes; only if field 1 is true |
| 3 | has series | bool | fields 4–6 only if true |
| 4 | teams | bool | ids are teams, not players |
| 5 | wins needed | small | Bifrost: from the series rules |
| 6 | another score follows | bool | before each; false ends |
| 6.1 | wins | small | |
| 6.2 | team or player id | uint | |

**Needs:** a reported online match.
**Gives:** the rematch deadline and series scores.
**Bifrost's server:** `bh_server/lobby.py` `match_reported`, 20 s window.

### 2544 · Handicap changed
`var_14695` / `_-A1l` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.HANDICAP_CHANGED`

A room slot's handicap changed; the client updates that slot and redraws the room.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | bot | bool | |
| 2 | bot slot | uint | only if field 1 is true |
| 3 | player id | uint | only if field 1 is false |
| 4 | sub-slot | uint | only if field 1 is false |
| 5 | set | bool | false resets to default |
| 6 | (handicap) | small | only if field 5 is true (unclear) |
| 7 | (handicap) | uint | only if field 5 is true (unclear) |
| 8 | (handicap) | uint | only if field 5 is true (unclear) |

**Needs:** 163 (set handicap).
**Gives:** the slot's handicap.
**Bifrost's server:** `bh_server/lobby.py` `set_handicap` / `write_handicap`.

### 2545 · Server error
`var_15121` / `_-n2p` · server → client · TCP lobby · extra u32 A · Bifrost: `social.SERVER_ERROR`

Shows a localised error, either as a toast or a popup. "Error_CrossProgression_NoRefundsDuring" becomes "Error_CrossInventory_NoRefundsDuring" in cross-inventory mode.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | error key | string | localisation key |
| 2 | toast | bool | true: toast; false: popup |

**Needs:** nothing.
**Gives:** an error message.
**Bifrost's server:** `bh_server/social.py` `error_key` (e.g. "Error_INVITE_FAILURE_ROOM_FULL").

### 2546 · Rating flag
`var_9489` / `_-k2d` · server → client · TCP lobby · extra u32 A · Bifrost: none

Sets a flag on a known rating record that the client treats like a star rating of 50 (probably the top rank).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | ladder | uint | 2v2 ranked uses team keys |
| 2 | player id | uint | |
| 3 | partner id | uint | used only for 2v2 (unclear) |
| 4 | flag | bool | |

**Needs:** the rating record exists (2483).
**Gives:** `var_7381` on that record.
**Bifrost's server:** not sent.

### 2547 · Rematch cancelled
`var_14702` / `_-h3l` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.REMATCH_CANCELLED`

The rematch fell through. The client resets the room's rematch state, closes the waiting screen and shows "UI_Rematch_Canceled_Header" / "…_Message".

No payload.

**Needs:** a rematch in progress.
**Gives:** the room back to normal.
**Bifrost's server:** `bh_server/lobby.py` `rematch_begin` when a party left.

### 2548 · Back from scoreboard
`var_8259` / `_-V5m` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.SCOREBOARD_DONE`

Another room member left the end-of-match screen; the room screen updates when that changes its state.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | |

**Needs:** a finished match in the room.
**Gives:** that member marked back in the room.
**Bifrost's server:** `bh_server/lobby.py` `back_from_scoreboard` (on 11073).

### 2549 · Unused
`var_12604` / `_-F1a` · not sent or handled by the 11.00 client or Bifrost's server

### 2550 · Chat restricted
`var_5511` / `_-cI` · server → client · TCP lobby · extra u32 A · Bifrost: `social.CHAT_RESTRICTED`

Turns the player's chat restriction on or off. While on, incoming guild chat (2494) is dropped.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | restricted | bool | |

**Needs:** nothing.
**Gives:** `GameCore.var_5776`.
**Bifrost's server:** `bh_server/social.py` `restriction_packet` (login while muted, `mute`, `unmute`). Bifrost's client (`ChatRestriction`) shows the chat box again after it.

### 2551 · Admin command
`var_10975` / `_-13c` · server → client · TCP lobby · extra u32 A · Bifrost: `themes.ADMIN_COMMAND`

A text command the client runs. The first word picks it: ALLOCATIONS, CLIENTTHEME <id> (switch the menu theme), RESETCODEOFCONDUCT, SETDATE <date> [time] (override the clock and redraw), XBOXSTORE (no effect); others are ignored.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | command line | string | words separated by spaces |

**Needs:** nothing.
**Gives:** the command's effect.
**Bifrost's server:** `bh_server/themes.py` `command_packet` / `theme_packet` ("CLIENTTHEME n"), from the console `theme` command.

### 2552 · Guild week reset
`var_9105` / `_-f2R` · server → client · TCP lobby · extra u32 A · Bifrost: `clans.EVENT_RESET`

A new guild week began; the client clears the duel state.

No payload.

**Needs:** the player is in a guild.
**Gives:** duel state 0 (then Bifrost sends 2487).
**Bifrost's server:** `bh_server/clans.py` `week_turn`.

### 2553 · Unused
`var_2685` / `_-K6K` · not sent or handled by the 11.00 client or Bifrost's server

### 2700 · Match progress and rewards
`var_818` / `_-W2r` · server → client · TCP lobby · extra u32 A · Bifrost: `stats.PROGRESS`

Sent after a match: the account's XP, the legend's XP, rating changes, gold earned and mission progress from that match. The handler (`Class_282.method_7715`, the mission manager) builds the end-of-match progress records the result screens show, updates the account level and XP, the legend level, and mission progress, and reports the numbers to telemetry.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | user id | uint | whose results these are (Bifrost sends the player id) |
| 2 | entries | list, `bool` before each | one record per legend slot or mission |
| 2.1 | mission id | uint | 0 for the match record; otherwise a mission whose progress this entry reports |
| 2.2 | legend id | uint | the legend played; 0 for a mission-only entry |
| 2.3 | slot | uint | 0 = main record (carries the extra blocks below); other values pick a secondary legend slot (unclear) |
| 2.4 | playlist | small | playlist id (telemetry only) |
| 2.5 | (unclear) | small | only if 2.2 ≠ 0; used to find the result record; Bifrost sends 0 |
| 2.6 | legend XP gained | uint | only if 2.2 ≠ 0 |
| 2.7 | legend XP | uint | only if 2.2 ≠ 0; total after the match |
| 2.8 | legend level before | uint | only if 2.2 ≠ 0 |
| 2.9 | legend level after | uint | only if 2.2 ≠ 0 |
| 2.10 | level start XP before | uint | only if 2.2 ≠ 0 |
| 2.11 | level start XP after | uint | only if 2.2 ≠ 0 |
| 2.12 | next level XP | uint | only if 2.2 ≠ 0 |
| 2.13 | unlocked colours | list of uint, `bool` before each | only if 2.2 ≠ 0; colour schemes unlocked by the level-up |
| 2.14 | unlocked legend rewards | uint | only if 2.2 ≠ 0; bit mask, each set bit is one of the legend's level rewards (unclear which kind) |
| 2.15 | gold from legend level-up | uint | only if 2.2 ≠ 0 |
| 2.16 | next colour reward | uint | only if 2.2 ≠ 0; shown as the next unlock (0 = none) |
| 2.17 | next legend rewards | uint | only if 2.2 ≠ 0; bit mask as 2.14, shown as the next unlock |
| 2.18 | (unclear) | uint | only if 2.2 ≠ 0; stored on the record |
| 2.19 | message | string | only if 2.2 ≠ 0, 2.3 = 0 and 2.6 = 0 (no legend XP); stored on the record (unclear) |
| 2.20 | Elo before | uint | only if 2.2 ≠ 0 and 2.3 = 0 |
| 2.21 | Elo after | uint | same condition |
| 2.22 | team or legend Elo before | uint | same condition |
| 2.23 | team or legend Elo after | uint | same condition |
| 2.24 | stars before | uint | same condition (tier) |
| 2.25 | stars after | uint | same condition |
| 2.26 | team or legend stars before | uint | same condition |
| 2.27 | team or legend stars after | uint | same condition |
| 2.28 | games | uint | same condition (Bifrost: ranked games played) |
| 2.29 | team games | uint | same condition |
| 2.30 | gold earned | uint | only if 2.3 = 0; from the match or mission, added to the session's gold total |
| 2.31 | account XP gained | uint | only if 2.3 = 0 |
| 2.32 | XP multiplier | f32 | only if 2.3 = 0 |
| 2.33 | second multiplier | f32 | only if 2.3 = 0 (unclear; Bifrost sends 1.0) |
| 2.34 | account XP | uint | only if 2.3 = 0; total after |
| 2.35 | account level before | uint | only if 2.3 = 0 |
| 2.36 | account level after | uint | only if 2.3 = 0; becomes the account level |
| 2.37 | level start XP before | uint | only if 2.3 = 0 |
| 2.38 | level start XP after | uint | only if 2.3 = 0 |
| 2.39 | next level XP | uint | only if 2.3 = 0 |
| 2.40 | gold from account level-up | uint | only if 2.3 = 0 |
| 2.41 | (unclear) | uint | only if 2.3 = 0; stored on the record |

**Needs:** a finished match the server has counted; mission ids and legend ids from the game's data tables.
**Gives:** the end-of-match progress screens (XP bars, rating change, level-ups), the account level and XP, legend levels, mission progress; a type-3 mission entry can open the mission-complete flow.
**Bifrost's server:** `bh_server/stats.py` `progress_packets` builds it (one main record, then one entry per completed mission) when a match result is settled, followed by 2527; Bifrost's client (`MatchReport`) only logs it.

### 2701 · Unused
`var_10757` / `_-u51` · not sent or handled by the 11.00 client or Bifrost's server

### 2702 · Legend ratings
`var_15141` / `_-U1p` · server → client · TCP lobby · extra u32 A · Bifrost: `stats.LEGEND_RATINGS`

The per-legend ranked ratings of the account. Each entry is stored on the game core (`GameCore.method_9218`) for the legend stats pages.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (ignored) | uint | read and dropped; Bifrost sends 0 |
| 2 | legends | list, `bool` before each | |
| 2.1 | legend id | uint | |
| 2.2 | rating | uint | |
| 2.3 | games | uint | |
| 2.4 | wins | uint | |
| 2.5 | tier | uint | star/tier value (Bifrost: stars) |
| 2.6 | peak rating | uint | |

**Needs:** login.
**Gives:** per-legend rating, games, wins, tier and peak.
**Bifrost's server:** `bh_server/stats.py` `legend_ratings_packet`, sent after a settled match when the account has legend ladders.

### 2703 · Unused
`var_4229` / `_-gR` · not sent or handled by the 11.00 client or Bifrost's server (the saved-data cache `Class_293` recognises the number when reloading packets but does nothing with it)

### 2740 · Store item list (unused by the UI)
`var_13547` / `_-21X` · server → client · TCP lobby · extra u32 A · Bifrost: none

A list of store item ids. The handler (`Class_744.method_3984`, the inventory) keeps the ones the player does not own in a list that nothing else in 11.00 reads.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | count | small | number of items |
| 2 | item id | small × count | store item ids |

**Needs:** store data loaded.
**Gives:** an internal list of unowned store items (no visible effect).
**Bifrost's server:** not sent.

### 2750 · Sponsored video settings
`var_8013` / `_-L` · server → client · TCP lobby · extra u32 A · Bifrost: none

Configures the sponsored-video (watch an ad for a reward) page, `Class_306.method_343`. It builds the reward milestone list for the video page and decides whether the video feature starts now.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (ignored) | bool | read and dropped |
| 2 | step | uint | milestone spacing (unclear) |
| 3 | first | uint | first milestone; 0 = same as step (unclear) |
| 4 | divisor | uint | milestones divisible by this are skipped (unclear) |
| 5 | (ignored) | bool | |
| 6 | (unclear) | uint | |
| 7 | limit | uint | last milestone; 0 = field 6 (unclear) |
| 8 | (unclear) | uint | read, unused |
| 9 | (ignored) | bool | |
| 10 | (unclear) | uint | stored |
| 11 | capped | bool | if true, field 12 follows and nothing else |
| 12 | count | uint | only if 11 is true: shown on the video page |
| 13 | disabled | bool | only if 11 is false; true stops here |
| 14 | start now | bool | only if 11 and 13 are false; true starts the video feature, false remembers the time |

**Needs:** login.
**Gives:** video page milestones and on/off state.
**Bifrost's server:** not sent.

### 2751 · Sponsored video list
`var_13589` / `_-p3R` · server → client · TCP lobby · extra u32 A · Bifrost: none

Replaces the list of available sponsored videos (`Class_306.method_8853`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | videos | list, `bool` before each | |
| 1.1 | video id | string | |
| 1.2 | (unclear) | uint | |
| 1.3 | (unclear) | string | second string, e.g. a source (unclear) |
| 1.4 | expires in | uint | seconds until the entry expires |

**Needs:** 2750.
**Gives:** the list of videos to offer; an empty list marks videos unavailable.
**Bifrost's server:** not sent.

### 2752 · Sponsored video state
`var_6784` / `_-d2G` · server → client · TCP lobby · extra u32 A · Bifrost: none

Tells the client whether a sponsored video session is running (`Class_306.method_3484`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | active | bool | |
| 2 | video id | string | only if 1 is true |
| 3 | seconds | uint | only if 1 is true; time limit |
| 4 | play now | bool | only if 1 is true (unclear) |
| 5 | (unclear) | bool | only if 1 is false; false logs a telemetry event |

**Needs:** 2751.
**Gives:** the current video and its deadline, or none.
**Bifrost's server:** not sent.

### 2753 · Sponsored video stopped
`var_14060` / `_-N2l` · server → client · TCP lobby · extra u32 A · Bifrost: none

The server ended the current video; if it was playing, the client stops it and shows "UI_SponsoredVideo_ServerStoppedVideo" (`Class_306.method_108`). No payload.

**Needs:** a video in progress.
**Gives:** the video list cleared and the player returned from the video.
**Bifrost's server:** not sent.

### 2754 · Sponsored video reward
`var_8465` / `_-d1J` · server → client · TCP lobby · extra u32 A · Bifrost: none

A reward for a watched video (`Class_306.method_6607`): added to the video page's counter and the current reward pod, and logged to telemetry.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | amount | uint | reward amount |

**Needs:** a watched video.
**Gives:** an updated reward count on the video page.
**Bifrost's server:** not sent.

### 2755 · Sponsored video list cleared
`var_10154` / `_-n1T` · server → client · TCP lobby · extra u32 A · Bifrost: none

Empties the sponsored video list (`Class_306.method_1975`). No payload.

**Needs:** nothing.
**Gives:** no videos offered.
**Bifrost's server:** not sent.

### 2760 · Telemetry (DNA) data
`var_15275` / `_-H3l` · server → client · TCP lobby · extra u32 A · Bifrost: none

Opaque bytes for the native DNA telemetry extension. On non-Mac builds with telemetry enabled, `GameCore.handleDnaPacket` reads them and hands them to `ANE_DnaManager`.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | length | small | byte count |
| 2 | data | raw n | telemetry bytes |

**Needs:** telemetry enabled.
**Gives:** nothing visible.
**Bifrost's server:** not sent.

### 2780 · Second friend list
`var_3657` / `_-25M` · server → client · TCP lobby · extra u32 A · Bifrost: none

Fills a friend-style list kept by `Class_45` (contacts plus incoming and outgoing requests). No screen in 11.00 reads this data; the game's visible friend list uses 2405/2406.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | contact count | uint | |
| 1.1 | user id | uint | per contact |
| 1.2 | name | string | |
| 1.3 | (unclear) | uint | stored; 2785 updates it |
| 2 | first request count | uint | entries go to the list 2782 fills when its flag is true |
| 2.1 | user id | uint | |
| 2.2 | name | string | |
| 2.3 | (unclear) | uint | |
| 2.4 | (unclear) | byte | |
| 3 | second request count | uint | entries go to the other request list |
| 3.1–3.4 | as 2.1–2.4 | | |

**Needs:** login.
**Gives:** an internal contact list (no visible effect).
**Bifrost's server:** not sent.

### 2781 · Second friend list: unknown flags
`var_12771` / `_-73B` · server → client · TCP lobby · extra u32 A · Bifrost: none

Belongs with 2780–2785; the handler reads two `bool`s and a `uint` and discards them.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (unclear) | bool | |
| 2 | (unclear) | bool | |
| 3 | (unclear) | uint | |

**Needs:** nothing.
**Gives:** nothing.
**Bifrost's server:** not sent.

### 2782 · Second friend list: request added
`var_14368` / `_-j59` · server → client · TCP lobby · extra u32 A · Bifrost: none

Adds one request to one of `Class_45`'s two request lists.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | which list | bool | true = first list, false = second (likely incoming vs outgoing; unclear which) |
| 2 | user id | uint | |
| 3 | name | string | |
| 4 | (unclear) | uint | |
| 5 | (unclear) | byte | |

**Needs:** 2780.
**Gives:** an internal request entry.
**Bifrost's server:** not sent.

### 2783 · Second friend list: request resolved
`var_5201` / `_-k49` · server → client · TCP lobby · extra u32 A · Bifrost: none

Removes a request; if it was accepted, adds the user as a contact.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | which list | bool | as in 2782 |
| 2 | accepted | bool | true adds the user to the contacts |
| 3 | user id | uint | |
| 4 | name | string | |

**Needs:** 2782.
**Gives:** updated internal lists.
**Bifrost's server:** not sent.

### 2784 · Second friend list: status
`var_3684` / `_-e3k` · server → client · TCP lobby · extra u32 A · Bifrost: none

Sets a contact's status.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | user id | uint | |
| 2 | status | uint | status code |
| 3 | detail | uint | only if status is 3, 4, 5 or 7 (unclear; likely what the contact is doing) |

**Needs:** 2780.
**Gives:** an internal status (not shown).
**Bifrost's server:** not sent.

### 2785 · Second friend list: contact value
`var_7532` / `_-y5B` · server → client · TCP lobby · extra u32 A · Bifrost: none

Updates the third value of a contact from 2780.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | user id | uint | |
| 2 | value | uint | (unclear) |

**Needs:** 2780.
**Gives:** an internal value.
**Bifrost's server:** not sent.

### 2800 · Mission list
`var_13585` / `_-Bo` · server → client · TCP lobby · extra u32 A · Bifrost: `missions.LIST`

The player's missions (dailies and other mission types) with their progress. `Class_282.method_7780` rebuilds every mission category from it, then the wrapper refreshes the mission, battle pass, social hub and event-dailies screens and saves the packet to the offline cache.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (ignored) | uint | Bifrost sends 0 |
| 2 | missions | list, `bool` before each | |
| 2.1 | mission id | uint | from the mission table; unknown ids are skipped |
| 2.2 | state | small | 0 = active; other values done/claimed (unclear exact codes) |
| 2.3 | progress | small | |
| 2.4 | timestamp | uint | |
| 3 | (unclear) | bool | false marks the missions as new |
| 4 | reroll available | bool | (Bifrost's meaning) |
| 5 | (unclear) | bool | |
| 6 | refresh time | uint | seconds until the next daily refresh (Bifrost's meaning) |
| 7 | (unclear) | bool | |

**Needs:** login.
**Gives:** the mission lists and progress on the mission screens.
**Bifrost's server:** `bh_server/missions.py` `Missions.packet`, sent by `Hub.send_list` after login, after a reroll and when a day rolls over.

### 2801 · Unused
`var_9778` / `_-A2M` · not sent or handled by the 11.00 client or Bifrost's server

### 2802 · Achievement unlocked
`var_13325` / `_-04k` · server → client · TCP lobby · extra u32 A · Bifrost: `achievements.UNLOCK`

Tells the client an achievement is earned. The handler looks the id up in the achievement table and asks the platform achievement layer (`Class_4`) to unlock it.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (ignored) | uint | Bifrost sends 0 |
| 2 | achievement id | uint | |

**Needs:** a valid achievement id (an unknown id is not checked).
**Gives:** a platform achievement unlock.
**Bifrost's server:** `bh_server/achievements.py` `unlock_packet`, sent by `Hub._send` for newly earned achievements; the client acknowledges with 129.

### 2803 · Battle pass missions
`var_11680` / `_-Q1D` · server → client · TCP lobby · extra u32 A · Bifrost: `battlepass.MISSIONS`

The battle pass season header and challenge progress (`Class_21.method_1031` via `method_5207`). Refreshes the main menu and challenge screens and is saved to the offline cache.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (ignored) | uint | Bifrost sends 0 |
| 2 | season number | uint | (Bifrost: season id mod 100) |
| 3 | season id | uint | selects the season data |
| 4 | (unclear) | uint | Bifrost sends 1 |
| 5 | weeks | uint | weeks unlocked (Bifrost's meaning) |
| 6 | challenges | list, `bool` before each | |
| 6.1 | mission id | uint | |
| 6.2 | state | small | 2 is counted as complete |
| 6.3 | progress | small | |
| 6.4 | timestamp | uint | |

**Needs:** login.
**Gives:** the active season and its challenges.
**Bifrost's server:** `bh_server/battlepass.py` `Pass.missions_packet`, via `Hub.send_all`.

### 2804 · Battle pass progress
`var_10466` / `_-Pp` · server → client · TCP lobby · extra u32 A · Bifrost: `battlepass.PROGRESS`

The player's battle pass tier and progress (`Class_21.method_4338` via `method_5468`). Refreshes the battle pass screens, can open the tier-up screen and logs tier-ups to telemetry.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (ignored) | uint | Bifrost sends 0 |
| 2 | tier | byte | |
| 3 | (unclear) | bool | pass ownership flag; a change refreshes screens |
| 4 | (unclear) | bool | second pass ownership flag |
| 5 | stars | small | when it rises while the challenges screen is open, the battle pass stars screen opens (Bifrost: stars) |
| 6 | (unclear) | small | |
| 7 | XP boost | f32 | shown as "+N%" |
| 8 | progress | small | current tier progress |
| 9 | goal | small | progress needed |
| 10 | (unclear) | small | |
| 11 | (unclear) | bool | |
| 12 | (unclear) | uint | |
| 13 | path | small | chosen path (Bifrost's meaning) |
| 14 | path count | small | n; 0 means fields 15–17 are absent |
| 15 | choice pending | bool | only if n > 0 (Bifrost's meaning) |
| 16 | maxed | bool | only if n > 0 (Bifrost's meaning) |
| 17 | path tiers | small × (n + 1) | only if n > 0 |

**Needs:** 2803.
**Gives:** tier, progress and boost on the battle pass screens.
**Bifrost's server:** `bh_server/battlepass.py` `Pass.progress_packet`, via `Hub.send_all`.

### 2805 · Battle pass ladder
`var_12111` / `_-04n` · server → client · TCP lobby · extra u32 A · Bifrost: `battlepass.LADDER`

A battle pass level ladder (`Class_22`), stored on the battle pass manager. What it drives is unclear.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (unclear) | small | added to field 2 for a total |
| 2 | (unclear) | small | |
| 3 | progress | small | |
| 4 | goal | small | |
| 5 | (unclear) | small | |
| 6 | steps | list, `bool` before each | |
| 6.1 | goal | small | |
| 6.2 | (unclear) | small | summed over the list |

**Needs:** 2803.
**Gives:** ladder data (unclear use).
**Bifrost's server:** `bh_server/battlepass.py` `Pass.ladder_packet` (all zeros, empty list), via `Hub.send_all`.

### 2850 · Cross-progression linked accounts
`var_685` / `_-S4L` · server → client · TCP lobby · extra u32 A · Bifrost: none

The accounts linked to this one for cross-progression (`Class_2.method_2510`). The client puts its own account first, sets status flags ("linked, no other" / "found other") and refreshes the cross-progression screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | accounts | list, `bool` before each | |
| 1.1 | platform | byte | platform id; 7 is treated specially (likely the Ubisoft account; unclear) |
| 1.2 | user id | uint | |
| 1.3 | name | string | |
| 1.4 | level | uint | account level |
| 1.5 | (unclear) | uint | nonzero on a platform-7 entry means another account was found |
| 1.6 | (unclear) | bool | |

**Needs:** 2853 and the client's link request.
**Gives:** the cross-progression account list and status.
**Bifrost's server:** not sent.

### 2851 · Cross-progression: no link
`var_11005` / `_-LC` · server → client · TCP lobby · extra u32 A · Bifrost: none

Why no linked accounts could be shown (`Class_2.method_7911`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | reason | uint | 5 = not linked, 6 = error fetching the Ubisoft account, 9 = error fetching linked accounts |

**Needs:** the client's link request.
**Gives:** the matching message on the cross-progression screen.
**Bifrost's server:** not sent.

### 2852 · Cross-progression unavailable
`var_14906` / `_-w1o` · server → client · TCP lobby · extra u32 A · Bifrost: none

The cross-progression service is down; the screen shows "Service unavailable" (`Class_2.method_1332`). No payload.

**Needs:** nothing.
**Gives:** the unavailable message.
**Bifrost's server:** not sent.

### 2853 · Cross-progression token
`var_2605` / `_-O3G` · server → client · TCP lobby · extra u32 A · Bifrost: none

Gives the client a token for cross-progression requests (`Class_2.method_5925`); the client stores it and immediately sends its linked-account request with its platform ids.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | token | string | needed before the client sends link requests |
| 2 | (unclear) | bool | |

**Needs:** login.
**Gives:** the token; triggers the client's request that 2850/2851 answer.
**Bifrost's server:** not sent.

### 2854 · Cross-progression guest id
`var_3061` / `_-X3A` · server → client · TCP lobby · extra u32 A · Bifrost: none

A guest Ubisoft id the client keeps and reports in telemetry (`CrossProgressionGuestUbisoftID`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | guest id | string | |

**Needs:** nothing.
**Gives:** a stored id (telemetry only).
**Bifrost's server:** not sent.

### 2900 · Currency balances
`var_14366` / `_-V6N` · server → client · TCP lobby · extra u32 A · Bifrost: none

The player's wallet (`LinkUpdater.method_8090`): gold, Mammoth coins (idols), glory and other currencies, plus the current store event. Refreshes the store screens.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | gold | signed uint (`bool` sign + `uint`) | |
| 2 | idols | signed uint | Mammoth coins |
| 3 | glory | signed uint | ranked currency |
| 4 | (unclear) | signed uint | a fourth balance the store shows in place of glory in some cases |
| 5 | other currencies | list, `bool` before each | |
| 5.1 | currency id | small | |
| 5.2 | balance | signed uint | |
| 6 | store event id | uint | 0 = none |
| 7 | (unclear) | uint | stored with the store event |

**Needs:** login.
**Gives:** the balances and the active store event.
**Bifrost's server:** not sent.

### 2901 · Inventory
`var_15454` / `_-Y3f` · server → client · TCP lobby · extra u32 A · Bifrost: `picks.INVENTORY`

Everything the account owns (`Class_744.method_7808`, version 112 layout): cosmetic ownership sets, legend levels and refundable purchases. Saved to the offline cache; the inventory is marked loaded.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (unclear) | uint | Bifrost sends 0 |
| 2 | ownership sets | 21 sets | each set is a list with `bool` before each item and two `uint`s per item (bit words, per Bifrost). Sets 3–13 are, by the refund handler's use of them: weapon skins, taunts, player themes, sidekicks, KO effects, avatars, podiums, borders, emoji, companions, emitter groups; sets 1, 2 and 14–21 unclear. Bifrost sends 14 empty sets, then its profile picks in sets 15–21 (KO effect, theme, frame, victory, defeat, sidekick, podium) |
| 3 | (unclear) | bool | |
| 4 | (unclear) | bool | |
| 5 | legends | list, `bool` before each | |
| 5.1 | legend id | uint | |
| 5.2 | level | uint | |
| 5.3 | XP | uint | |
| 5.4 | level start XP | uint | |
| 5.5 | next level XP | uint | |
| 5.6 | (unclear) | uint | |
| 5.7 | (unclear) | uint | |
| 5.8 | (unclear) | list of uint, `bool` before each | per-legend set |
| 6 | refundable purchases | list, `bool` before each | same layout as 2911 |

**Needs:** login; store and legend data.
**Gives:** ownership of cosmetics and legends, legend levels, the refund list.
**Bifrost's server:** `bh_server/picks.py` `inventory_packet`, sent after 2471 when profile picks are sent; Bifrost's client (`ProfilePicks`) watches for it.

### 2902 · Store item flags
`var_15098` / `_-t45` · server → client · TCP lobby · extra u32 A · Bifrost: none

Per-item state values for store items (`Class_744.method_6744`); the client sets 1 itself for an item it marks, and the battle pass screen checks for 2.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | items | list, `bool` before each | |
| 1.1 | item id | uint | |
| 1.2 | state | small | (unclear; 1 and 2 are used) |
| 2 | (unclear) | bool | true clears an internal flag |

**Needs:** store data.
**Gives:** item flags; refreshes the inventory view.
**Bifrost's server:** not sent.

### 2903 · Unknown id list
`var_1723` / `_-71O` · server → client · TCP lobby · extra u32 A · Bifrost: none

The handler reads a list of `uint`s (`bool` before each) and discards them.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | ids | list of uint, `bool` before each | (unclear) |

**Needs:** nothing.
**Gives:** nothing.
**Bifrost's server:** not sent.

### 2904 · Unused
`var_8077` / `_-h5Z` · not sent or handled by the 11.00 client or Bifrost's server

### 2905 · Unused
`var_13709` / `_-x8` · not sent or handled by the 11.00 client or Bifrost's server

### 2906 · Unknown id list
`var_7623` / `_-93s` · server → client · TCP lobby · extra u32 A · Bifrost: none

Same as 2903: a list of `uint`s (`bool` before each) read and discarded.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | ids | list of uint, `bool` before each | (unclear) |

**Needs:** nothing.
**Gives:** nothing.
**Bifrost's server:** not sent.

### 2907 · Idol pack purchased
`var_4564` / `_-f3h` · server → client · TCP lobby · extra u32 A · Bifrost: none

A real-money Mammoth coin (idol) pack purchase went through (`LinkUpdater.method_1525`). The client sets the new idol balance, closes the buy-idols screen, refreshes the store and shows the purchase feedback for the pack.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | pack | uint | index of the idol pack product |
| 2 | idols | signed uint | new idol balance; any increase is logged as idols bought |
| 3 | (unclear) | bool | an account flag also sent at login |

**Needs:** a purchase through the platform store.
**Gives:** new balance and a purchase screen.
**Bifrost's server:** not sent.

### 2908 · Idols granted
`var_14572` / `_-73` · server → client · TCP lobby · extra u32 A · Bifrost: none

Idols were added to the account (`LinkUpdater.method_3231`). If the amount matches a known idol pack and the store is open, the purchase feedback is shown; otherwise a notification is queued.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | amount | uint | idols added; 0 = only update the balance |
| 2 | idols | signed uint | new idol balance |

**Needs:** nothing.
**Gives:** new balance and a feedback screen or notification.
**Bifrost's server:** not sent.

### 2909 · Store purchase result
`var_2619` / `_-r5K` · server → client · TCP lobby · extra u32 A · Bifrost: none

The answer to a store purchase or code redemption (`LinkUpdater.method_544`). On failure it shows the store error text; on success it updates the paid currency, works out the items received (bundles expand), closes the purchase prompts and shows the purchase feedback screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | redeemed | bool | true when the item comes from the redeem-code table |
| 2 | (unclear) | bool | if 1 and 2 are both true, the item is queued for later display and nothing more is read |
| 3 | item id | uint | store item id |
| 4 | extra | uint | legend id for the feedback screen, or the pass id when 5 is true (unclear) |
| 5 | battle pass | bool | true for a battle pass purchase |
| 6 | success | bool | |
| 7 | error code | small | only if 6 is false; shown as a store error string |
| 8 | entitlements follow | bool | only if 6 is false |
| 9 | entitlements | block | only if 8 is true: three lists, `bool` before each item: (signed small store item id, uint count); (signed small chest id, uint count); (uint store item id) |
| 10 | currency id | uint | only if 6 is true and 1 is false: 1 idols, 2 gold, 3 glory, other = an extra currency |
| 11 | balance | signed uint | only with 10; new balance of that currency |

**Needs:** the client's purchase request.
**Gives:** ownership and balance changes, the feedback screen or an error.
**Bifrost's server:** not sent.

### 2910 · Refund result
`var_710` / `_-41T` · server → client · TCP lobby · extra u32 A · Bifrost: none

The answer to a refund request (`LinkUpdater.method_6799`). On success the client sets the new balances, removes the item (and bundle contents) from the inventory and the room loadout, and refreshes the store.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | item id | uint | store item id |
| 2 | legend id | uint | |
| 3 | refunds left | uint | shown on the refunds screen |
| 4 | success | bool | |
| 5 | error code | small | only if 4 is false; shown as an error string |
| 6 | gold | signed uint | only if 4 is true |
| 7 | idols | signed uint | only if 4 is true |
| 8 | glory | uint | only if 4 is true |

**Needs:** the client's refund request.
**Gives:** item removed and balances updated, or an error.
**Bifrost's server:** not sent.

### 2911 · Refundable purchases
`var_15379` / `_-q3E` · server → client · TCP lobby · extra u32 A · Bifrost: none

The purchases the player can still refund, for the refunds screen (`Class_744.method_7840`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | purchases | list, `bool` before each | |
| 1.1 | item id | uint | store item id |
| 1.2 | currency id | uint | 1 = idols |
| 1.3 | price | uint | amount paid |
| 1.4 | legend id | uint | |
| 1.5 | (unclear) | bool | |

**Needs:** login.
**Gives:** the refund list; refreshes the store.
**Bifrost's server:** not sent.

### 2912 · Chest opened
`var_11371` / `_-54v` · server → client · TCP lobby · extra u32 A · Bifrost: none

The result of opening a chest (`LinkUpdater.method_337`): the skin and its weapon skins become owned, the idol balance is updated and the chest screen plays the reveal.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | chest id | uint | |
| 2 | legend id | uint | |
| 3 | skin id | uint | costume won |
| 4 | idols | signed uint | new idol balance |

**Needs:** the client's chest request.
**Gives:** the new skin and the reveal animation.
**Bifrost's server:** not sent.

### 2913 · Chest open failed
`var_501` / `_-p2L` · server → client · TCP lobby · extra u32 A · Bifrost: none

Opening a chest failed (`LinkUpdater.method_213`); the chest screen shows the error text for the code.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | error code | uint | |
| 2 | entitlements follow | bool | |
| 3 | entitlements | block | only if 2 is true; same block as 2909 field 9 |

**Needs:** the client's chest request.
**Gives:** an error message.
**Bifrost's server:** not sent.

### 2914 · Battle pass rewards
`var_4328` / `_-BH` · server → client · TCP lobby · extra u32 A · Bifrost: `battlepass.REWARDS`

Rewards reached on the battle pass (`Class_21.method_7357`). The client adds them to the unlocks list, sets the idol balance and opens the battle pass unlocks screen when appropriate.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | rewards | list, `bool` before each | |
| 1.1 | reward id | byte | in the current season's reward table; an unknown id stops the handler |
| 1.2 | colour id | uint | only if the reward type is RandomColor |
| 2 | idols | signed uint | new idol balance |
| 3 | (unclear) | bool | read, unused |

**Needs:** 2803 (season id).
**Gives:** the unlocks screen.
**Bifrost's server:** `bh_server/battlepass.py` `Pass.rewards_packet`, via `Hub.send_all` when tiers were reached.

### 2915 · Owned colours in room
`var_8733` / `_-H3h` · server → client · TCP lobby · extra u32 A · Bifrost: `lobby.OWNED_COLOURS`

Which colour schemes a room member owns, so the room's colour choices are right (`LinkUpdater.method_3496`). Ignored when the client has no room loadout.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | user id | uint | the room member |
| 2 | colours | list of uint, `bool` before each | colours usable on any legend |
| 3 | legends | list, `bool` before each | |
| 3.1 | legend id | uint | |
| 3.2 | colours | list of uint, `bool` before each | colours owned for that legend |

**Needs:** a room (sent after room/joined packets).
**Gives:** per-member colour ownership in the room.
**Bifrost's server:** `bh_server/lobby.py` `owned_colours_packets`, sent by `protocol.py` after every 2445 or 2449, one per member, listing every colour in the game data.

### 2916 · Gold refund notice
`var_15336` / `_-f6m` · server → client · TCP lobby · extra u32 A · Bifrost: none

Gold was refunded; the purchase feedback screen shows a "Gold refund" entry (`LinkUpdater.method_5032`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | gold | uint | amount; 0 does nothing |

**Needs:** nothing.
**Gives:** a feedback screen entry.
**Bifrost's server:** not sent.

### 2917 · Sponsored video count
`var_10086` / `_-x1k` · server → client · TCP lobby · extra u32 A · Bifrost: none

Sets the counter on the sponsored video page (`Class_306.method_7163`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | count | uint | (unclear; likely videos watched) |
| 2 | (unclear) | bool | read, unused |

**Needs:** 2750.
**Gives:** an updated count on the video page.
**Bifrost's server:** not sent.

### 2918 · Item granted (shown later)
`var_13230` / `_-YO` · server → client · TCP lobby · extra u32 A · Bifrost: none

An item was granted; the client queues it to show on the purchase feedback screen later (`ClientManager.method_2155`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | type | string | item type, e.g. Companion, Moniker, RankedSeasonMoniker, UniversalColor |
| 2 | id | uint | id within that type |

**Needs:** nothing.
**Gives:** a queued feedback entry.
**Bifrost's server:** not sent.

### 2919 · Multiple All Legends packs
`var_3813` / `_-L6b` · server → client · TCP lobby · extra u32 A · Bifrost: none

Says whether the account owns more than one All Legends pack (`LinkUpdater.method_5154`). The first time it is true the client adds a notification and opens the "multiple All Legends" screen; the flag is saved in the game's saved data.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | has several | bool | |

**Needs:** login.
**Gives:** a saved flag and a one-time screen.
**Bifrost's server:** not sent.

### 2920 · Rewards granted
`var_11922` / `_-AK` · server → client · TCP lobby · extra u32 A · Bifrost: none

A list of rewards to show (`Class_338.method_8967`). The source decides where they go: sponsored video rewards, battle pass items or the general reward list.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | source | small | 4 = sponsored video, 5 and 8 = battle pass lists, other = general rewards |
| 2 | rewards | list, `bool` before each | |
| 2.1 | type | string | Avatar, Border, ColorScheme, Companion, Costume, Custom, Gold, Idols, KOEffect, ... |
| 2.2 | item | string | item name, or the amount for Gold and Idols |
| 2.3 | legend | string | only if type is ColorScheme or RandomColor |
| 2.4 | text | string | only if type is Custom |

**Needs:** game data for the items.
**Gives:** reward entries for the matching screen.
**Bifrost's server:** not sent.

### 2921 · Skirmish faction joined
`var_4795` / `_-W1u` · server → client · TCP lobby · extra u32 A · Bifrost: `skirmish.JOINED`

Confirms joining a skirmish faction; the client closes the faction-select screen and returns to the main menu (`Class_583.method_274`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | event id | small | |
| 2 | faction id | small | |
| 3 | (unclear) | uint | Bifrost sends 0 |

**Needs:** the client's join request (181).
**Gives:** the faction screen closed.
**Bifrost's server:** `bh_server/skirmish.py` `Hub`, answering 181, followed by 2923.

### 2922 · Skirmish final results
`var_14611` / `_-X4S` · server → client · TCP lobby · extra u32 A · Bifrost: `skirmish.FINAL`

The final standings of a finished skirmish (`Class_583.method_7256`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | event id | small | |
| 2 | (unclear) | small | Bifrost sends 0 |
| 3 | (unclear) | uint | Bifrost sends 0 |
| 4 | (unclear) | bool | |
| 5 | factions | list, `bool` before each | |
| 5.1 | faction id | small | |
| 5.2 | score | string | formatted score text |
| 5.3 | share | f32 | fraction of the total |
| 5.4 | winner | bool | |

**Needs:** a skirmish that ended.
**Gives:** result records for the skirmish screens.
**Bifrost's server:** `bh_server/skirmish.py` `final_packet`, sent after login when an event ended recently.

### 2923 · Skirmish status
`var_3406` / `_-Y1s` · server → client · TCP lobby · extra u32 A · Bifrost: `skirmish.STATUS`

The running skirmish and the standings (`Class_583.method_6726`). Refreshes the main menu buttons and the skirmish screens.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | event id | small | 0 = no skirmish; nothing else follows |
| 2 | time left | uint | seconds |
| 3 | my faction | small | 0 = not joined |
| 4 | my influence | uint | only if 3 ≠ 0 |
| 5 | suggested faction | small | only if 3 = 0 |
| 6 | factions | list, `bool` before each | |
| 6.1 | faction id | small | |
| 6.2 | score | string | formatted score text |
| 6.3 | share | f32 | fraction of the total |
| 6.4 | (unclear) | uint | only if 3 = 0 |

**Needs:** login.
**Gives:** the event state and standings.
**Bifrost's server:** `bh_server/skirmish.py` `status_packet`, sent after login, after a join and after a match that gave influence.

### 2924 · Skirmish influence gained
`var_1335` / `_-i1b` · server → client · TCP lobby · extra u32 A · Bifrost: `skirmish.INFLUENCE`

Influence points earned in a match (`Class_583.method_379`); stored with the before/after totals for the post-match display.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | points | uint | |
| 2 | multiplier | uint | 0 is treated as 1 |

**Needs:** 2923 (the current influence).
**Gives:** a post-match influence record.
**Bifrost's server:** `bh_server/skirmish.py` `Hub.on_result`, after 2923.

### 2925 · Store price overrides
`var_14104` / `_-L4o` · server → client · TCP lobby · extra u32 A · Bifrost: none

Prices that replace the data-file prices for items marked as server-priced (`Class_744.method_1982`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | items | list, `bool` before each | |
| 1.1 | item id | uint | |
| 1.2 | gold price | uint | 0 = no override |
| 1.3 | idol price | uint | 0 = no override |
| 1.4 | glory price | uint | 0 = no override |
| 1.5 | has other price | bool | |
| 1.6 | other-currency price | uint | only if 1.5 is true |

**Needs:** store data.
**Gives:** prices shown and charged in the store.
**Bifrost's server:** not sent.

### 2926 · Timed event colour win
`var_1671` / `_-I6J` · server → client · TCP lobby · extra u32 A · Bifrost: `timedevent.HIGHLIGHT`

A win with a legend in the timed event's colour (`LinkUpdater.method_9094`). If the colour is the current event's, the event progress screen marks that legend on its bingo card the next time it opens.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | legend id | uint | |
| 2 | colour id | uint | colour scheme |

**Needs:** a running timed event.
**Gives:** a highlighted legend on the event card.
**Bifrost's server:** `bh_server/timedevent.py` `highlight_packet`, sent by `Hub.on_result` when a player wins with their profile legend in the event colour.

### 2927 · Peekable colour data
`var_3231` / `_-6N` · server → client · TCP lobby · extra u32 A · Bifrost: none

Data for "PeekableColor" store items (`Class_744.method_2344`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (unclear) | uint | number shown on a PeekableColor item's store tile |
| 2 | entries | list, `bool` before each | |
| 2.1 | id | uint | (unclear) |
| 2.2 | legend id | uint | looked up by id; the default legend when missing |

**Needs:** store data.
**Gives:** values for the store tiles.
**Bifrost's server:** not sent.

### 2950 · Cup list
`var_3581` / `_-Y6Q` · server → client · TCP lobby · extra u32 A · Bifrost: `cups.LIST`

All tournament cups (`Class_109.method_4798`). The client replaces its cup list and refreshes the cup home screen.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | cups | list, `bool` before each | |
| 1.1 | cup id | uint | |
| 1.2 | name | string | |
| 1.3 | (ignored) | bool | read but not used; Bifrost sends "official" |
| 1.4 | state | byte | Bifrost: 0 open, 1 running, 2 done |
| 1.5 | time | uint | seconds to start or end |
| 1.6 | (unclear) | uint | |
| 1.7 | (unclear) | byte | |
| 1.8 | size | uint | bracket size (Bifrost: 8) |
| 1.9 | format | byte | Bifrost: 1 = double elimination |
| 1.10 | (unclear) | byte | |
| 1.11 | seeding | byte | |
| 1.12 | (unclear) | uint | |
| 1.13 | organizer | uint | 0 marks the cup as official |
| 1.14 | (unclear) | uint | |
| 1.15 | (unclear) | uint | |
| 1.16 | art | uint | |

**Needs:** the client's cup list request (188).
**Gives:** the cup list.
**Bifrost's server:** `bh_server/cups.py` `Cups.list_packet`, sent by `Desk.handle` for 188, with 2953.

### 2951 · Unknown (cups)
`var_3685` / `_-q5C` · server → client · TCP lobby · extra u32 A · Bifrost: none

Registered to `Class_109.method_3400`, which is empty. No payload is read.

**Needs:** nothing.
**Gives:** nothing.
**Bifrost's server:** not sent.

### 2952 · Cup list (second type)
`var_15384` / `_-U2b` · server → client · TCP lobby · extra u32 A · Bifrost: none

Handled by the same method as 2950 and laid out the same; the difference from 2950 is unclear.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | cups | list | as 2950 |

**Needs:** as 2950.
**Gives:** the cup list.
**Bifrost's server:** not sent.

### 2953 · Cup registrations
`var_1045` / `_-c4` · server → client · TCP lobby · extra u32 A · Bifrost: `cups.REGISTRATIONS`

Which cups the player is registered for (`Class_109.method_6533`). A cup waiting on a registration answer shows "UI_Cup_ConfirmRegistration"; unconfirmed ones go back to unregistered.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | cups | list, `bool` before each | |
| 1.1 | cup id | uint | must be in the 2950 list |
| 1.2 | registered | bool | |

**Needs:** 2950.
**Gives:** registration state on the cup screens.
**Bifrost's server:** `bh_server/cups.py` `Cups.registrations_packet`, after 188, 193 and 194.

### 2954 · Cup brackets summary
`var_8063` / `_-m25` · server → client · TCP lobby · extra u32 A · Bifrost: `cups.BRACKETS`

The brackets of the selected cup for the cup history screen (`Class_109.method_2199`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | brackets | list, `bool` before each | |
| 1.1 | bracket id | uint | |
| 1.2 | average rating | uint | |
| 1.3 | first | string | top-three name |
| 1.4 | second | string | |
| 1.5 | third | string | |
| 1.6 | state | uint | |

**Needs:** a selected cup (client request 191).
**Gives:** the cup history screen.
**Bifrost's server:** `bh_server/cups.py` `Cups.brackets_packet`, for 191.

### 2955 · Cup bracket
`var_3698` / `_-tJ` · server → client · TCP lobby · extra u32 A · Bifrost: `cups.BRACKET`

One full bracket with all sets (`Class_109.method_7408`); refreshes the bracket and social hub screens.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | bracket id | uint | |
| 2 | average rating | uint | |
| 3 | state | uint | |
| 4 | sets | list, `bool` before each | |
| 4.1 | set id | uint | |
| 4.2 | sub-bracket | uint | |
| 4.3 | round | uint | |
| 4.4 | position | uint | position in the round |
| 4.5 | result | uint | |
| 4.6 | score A | uint | |
| 4.7 | score B | uint | |
| 4.8 | group id | uint | |
| 4.9 | entrant A present | bool | false = bye |
| 4.10 | entrant index | uint | only if 4.9 is true |
| 4.11 | user ids | list of uint, `bool` before each | only if 4.9 is true |
| 4.12 | display name | string | only if 4.9 is true |
| 4.13 | seed | uint | only if 4.9 is true |
| 4.14 | entrant B | as 4.9–4.13 | |
| 4.15 | winner | uint | |

**Needs:** client request 192 (cup and bracket).
**Gives:** the bracket view.
**Bifrost's server:** `bh_server/cups.py` `Cups.bracket_packet`, for 192.

### 2956 · Cup set ready
`var_12003` / `_-eo` · server → client · TCP lobby · extra u32 A · Bifrost: `cupsets.SET_READY`

The player's next cup set is ready to play (`LinkUpdater.method_4521`). The client loads that bracket and posts or updates a cup notification with the room and check-in deadline.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | cup id | uint | |
| 2 | bracket id | uint | |
| 3 | room | uint | room number to join |
| 4 | set id | uint | |
| 5 | deadline | uint | seconds to check in |
| 6 | refresh | bool | forces the notification to be shown again |

**Needs:** a running cup the player is in.
**Gives:** a cup notification pointing at the room.
**Bifrost's server:** `bh_server/cupsets.py` `CupSets.play`.

### 2957 · Cup state
`var_1203` / `_-q46` · server → client · TCP lobby · extra u32 A · Bifrost: `cupsets.CUP_STATE`

The player's cup status for the notification (`LinkUpdater.method_6638`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | state | uint | 0 removes the notification; Bifrost sends 3 = waiting for the next set |
| 2 | cup id | uint | |
| 3 | bracket id | uint | |
| 4 | time | uint | seconds (Bifrost: until the cup ends) |

**Needs:** a cup the player is in.
**Gives:** an updated or removed cup notification.
**Bifrost's server:** `bh_server/cupsets.py` `CupSets.tell_waiting`.

### 2958 · Cup scoreboard timer
`var_15629` / `_-E1d` · server → client · TCP lobby · extra u32 A · Bifrost: none

Sets the time the scoreboard counts down to (`LinkUpdater.method_3812`); at zero the scoreboard flags the next step.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | (unclear) | uint | read, unused |
| 2 | end time | uint | compared against the client's server clock |

**Needs:** a cup set in progress (unclear).
**Gives:** a scoreboard countdown.
**Bifrost's server:** not sent.

### 2959 · Cup set score
`var_13156` / `_-X3z` · server → client · TCP lobby · extra u32 A · Bifrost: `cupsets.SET_SCORE`

The score of the room's cup set after a game (`LinkUpdater.method_6098`). The client updates the set, reloads the bracket and decides the player's next set from the winner/loser paths.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | set id | uint | must match the room's cup set |
| 2 | (unclear) | byte | 4 or 5 marks a special outcome; in one room mode it is the only field used |
| 3 | (unclear) | byte | |
| 4 | count | byte | n |
| 5 | user ids | uint × n | if the player's id is listed, the client follows the winner path |
| 6 | score 1 | byte | |
| 7 | score 2 | byte | |
| 8 | next set (winner) | uint | |
| 9 | next set (loser) | uint | |

**Needs:** a cup room.
**Gives:** the updated set score and the next set.
**Bifrost's server:** `bh_server/cupsets.py` `CupSets.on_result`, broadcast to the room's players.

### 3000 · Unused
`var_8892` / `_-N3y` · not sent or handled by the 11.00 client or Bifrost's server (it is the upper bound: types 2401–2999 carry extra u32 A)

### 10100 · Spectator joined
`var_12517` / `_-X1q` · server → client · TCP spectate

Tells a spectator that another spectator has joined the match being watched. The client adds the person to the room's spectator list and its recent-players list, refreshes the room screen, and shows a notice with the name unless the new spectator is the player themself.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the spectator's player id |
| 2 | name number | uint | (unclear) a number stored with the name, as in other player records |
| 3 | name | string | the spectator's display name |
| 4 | account name | string | (unclear) a second name string, stored with the entry |

**Needs:** the client is spectating (connected to the spectate server after 2420 and 10202).
**Gives:** one more entry in the spectator list.
**Bifrost's server:** not sent.

### 10101 · Spectator list
`var_11007` / `_-k1S` · server → client · TCP spectate

The full list of people spectating the match. If the client does not have a room yet it takes the room number it got with the spectate ticket (2420); then it adds every listed person as a spectator and refreshes the room screen. No notices are shown.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | more | bool | "another spectator follows"; false ends the list |
| 1.1 | player id | uint | the spectator's player id |
| 1.2 | name number | uint | (unclear) a number stored with the name |
| 1.3 | name | string | display name |
| 1.4 | account name | string | (unclear) second name string |

**Needs:** a spectate connection (after 2420 and 10202).
**Gives:** the room's spectator list.
**Bifrost's server:** not sent.

### 10102 · Spectated match ended early
`var_11860` / `_-X2Q` · server → client · TCP spectate · Bifrost: `spectate.ABRUPT`

The match being watched stopped early (for example the host left). The client keeps playing the recording up to the given match time, then shows "Spectating Game Has Ended Abruptly", sends 10201 and returns to the menu state.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | end time | uint | match time in ms where the stream stops |

**Needs:** the client is watching a match (after 10104).
**Gives:** an end point for spectating.
**Bifrost's server:** `bh_server/spectate.py` `Desk.handle`: when the host's upload ends early (11082 with its abrupt flag), sends 10102 with that time to every viewer of the feed.

### 10103 · Start watching after a delay
`var_6101` / `_-I6v` · server → client · TCP spectate

The client sets its start moment to now plus a fixed delay and switches to the spectating state, so playback begins once the level is loaded and the delay has passed.

No payload.

**Needs:** match setup received (10104).
**Gives:** spectating state; playback start time.
**Bifrost's server:** not sent (Bifrost uses 10107).

### 10104 · Spectate match setup
`var_8334` / `_-K4V` · server → client · TCP spectate · Bifrost: `spectate.START`

Everything a spectator needs to rebuild the match: seed, level, rules and every player with their loadout. The client loads the level, creates all players as remote entities, works out how far behind live it starts, and starts the replay recorder. Its player record is close to the one in 10306/10310 but in a different order and without the local/bot flags.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | seed | uint | random seed for the match |
| 2 | level | uint | level id |
| 3 | match kind | uint | 1 for a matchmade match (shows the queue's recent-players entry); Bifrost calls it teamMode (unclear) |
| 4 | start time | uint | match time in ms the stream starts at (Bifrost sends 0) |
| 5 | playlist | uint | queue/playlist id given to the replay recorder; 0 = none (Bifrost sends 0) |
| 6 | legends | uint | legends per player (the per-legend block below repeats this many times) |
| 7 | extras | bool | each player record ends with an extras block |
| 8 | rules | uint ×15 | the game rules block: flags, (unclear), duration, (unclear), (unclear), scoring type id, eight (unclear) values, mode (unclear) |
| 9 | players | uint | number of player records that follow |
| 9.1 | entity | uint | entity id in the match |
| 9.2 | player id | uint | the player's id |
| 9.3 | name number | uint | (unclear) number stored with the name |
| 9.4 | name | string | display name |
| 9.5 | text | string | (unclear) second string |
| 9.6–9.8 | profile values | uint ×3 | (unclear) loadout/profile values |
| 9.9–9.14 | cosmetics | uint ×6 | (unclear) cosmetic choices |
| 9.15 | slots | uint ×8 | (unclear) eight cosmetic slots |
| 9.16–9.17 | values | small ×2 | (unclear) |
| 9.18 | flag pairs | bool + uint ×2, repeated | "another pair follows", then two uints; false ends (unclear contents) |
| 9.19 | value | small | (unclear) |
| 9.20–9.22 | values | uint ×3 | (unclear) |
| 9.23 | value | small | (unclear) |
| 9.24 | legend | repeated field 6 times | one block per legend: |
| 9.24.1 | hero | uint | legend id |
| 9.24.2 | skin | uint | costume id |
| 9.24.3–9.24.5 | values | uint ×3 | (unclear) per-legend cosmetics |
| 9.25 | extras | only if field 7 | bool present; if true: small, uint, uint (unclear) |

**Needs:** a spectate connection and 10202.
**Gives:** a loaded match with all players, ready for 10106 inputs.
**Bifrost's server:** `bh_server/spectate.py` `Feed.catch_up`: sends the host client's uploaded 11080 payload unchanged as 10104 to each viewer after a valid 10202.

### 10105 · Input rows without clock
`var_7198` / `_-a4A` · server → client · TCP spectate (dispatched the same on any connection)

A batch of player inputs applied with rollback: each row is checked against the player's pending inputs and the client rolls back to the earliest changed time. Unlike 10309 it does not move the confirmed match clock.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | (unclear) only written to the diagnostic log |
| 2 | rows | uint | number of rows |
| 2.1 | entity | 4 bits | entity id |
| 2.2 | time | uint | match time in ms of the input |
| 2.3 | input | 14 bits | input bits |

**Needs:** a running match (players created).
**Gives:** corrected inputs and a rollback.
**Bifrost's server:** not sent.

### 10106 · Spectate inputs
`var_15524` / `_-26V` · server → client · TCP spectate · Bifrost: `spectate.INPUTS`

A batch of recorded inputs for the match being watched. Each row is appended to that player's input history, and the newest time seen becomes how far the stream reaches.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | rows | uint | number of rows |
| 1.1 | entity | 4 bits | entity id |
| 1.2 | time | uint | match time in ms |
| 1.3 | input | 14 bits | input bits |

**Needs:** 10104.
**Gives:** more of the match to play back.
**Bifrost's server:** `bh_server/spectate.py`: relays each 11081 upload from the host as 10106 to every viewer (`Desk.handle`), and replays all stored batches on catch-up (`Feed.catch_up`); when the uploader changes it rebuilds rows with `rows_body`.

### 10107 · Spectate playback begins
`var_2281` / `_-f3M` · server → client · TCP spectate · Bifrost: `spectate.BEGIN`

If the flag is true the client switches to the spectating state and starts playback.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | begin | bool | true starts spectating |

**Needs:** 10104.
**Gives:** playback running.
**Bifrost's server:** `bh_server/spectate.py` `begin_packet`, sent with true right after 10104 (or after the stored inputs for a "rejoin-" ticket).

### 10200 · Spectator reached the end screen
`var_13557` / `_-v2W` · client → server · TCP spectate · Bifrost: `spectate.WATCHED_END`

Sent by `Class_255.method_4177` when the end-of-match results come up while the client is spectating; the client then goes back to the menu state.

No payload.

**Needs:** spectating a match that has ended (10312 seen).
**Gives:** the server knows this viewer finished.
**Bifrost's server:** `bh_server/spectate.py` `Desk.handle`: only logs it.

### 10201 · Stop spectating
`var_8044` / `_-w4r` · client → server · TCP spectate · Bifrost: `spectate.QUIT`

Sent by `GameCore.method_4405` when the player leaves a spectated match (also after 10102's end point). If the lobby connection is down the client instead drops to the menu and reconnects.

No payload.

**Needs:** a spectate connection.
**Gives:** the server stops streaming to this viewer.
**Bifrost's server:** `bh_server/spectate.py` `Desk._quit`: removes the viewer from the feed and sends the room (2445) back to them.

### 10202 · Spectate hello
`var_13701` / `_-MB` · client → server · TCP spectate · extra u32 B · Bifrost: `spectate.HELLO`

The first packet on the spectate connection, sent by `GameCore.method_7646` as soon as the connection to the host and port from 2420 opens. The extra u32 B is never set by this client, so it is 0.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the player's id |
| 2 | ticket | string | the spectate ticket from 2420 |
| 3 | value | uint | (unclear) a field that starts at 1 |
| 4 | name | string | display name (from 2431 or 2430) |
| 5 | account name | string | the first string of 2431 |

**Needs:** 2420 (spectate ticket with host, port, ticket, room).
**Gives:** the stream: 10104, 10107 and 10106 batches.
**Bifrost's server:** `bh_server/spectate.py` `Desk._hello`: reads player id and ticket only, matches the ticket to a live feed, then sends the catch-up and keeps streaming.

### 10300 · Transfer to game server failed
`var_3569` / `_-C4V` · game server → client · TCP game server

The game server refused or lost the transfer. The client optionally tears down the match, returns to the menu state, shows "Error_FAILED_TRANSFER" and disconnects from the game server.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | leave match | bool | true also cleans up the current match |

**Needs:** a game server connection (after 10400/10405).
**Gives:** back to the menu with an error.
**Bifrost's server:** not handled.

### 10301 · Input reset
`var_8652` / `_-n4u` · both · TCP game server (from the server: any match connection)

The game server tells the client to roll back to a time and drop every unconfirmed local input and queued match event; the client does so and answers with an empty 10301.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms to roll back to (server → client only) |

The client's reply has no payload.

**Needs:** a running match.
**Gives:** a clean resync; the server gets the empty 10301 as acknowledgement.
**Bifrost's server:** not handled.

### 10302 · Player reconnected
`var_15192` / `_-L6w` · game server → client · TCP game server or UDP match

Shows the "UI_PlayerMessage_HasReconnected" notice for a player in the match.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | entity | uint | the player's entity id |

**Needs:** a running match.
**Gives:** a notice only.
**Bifrost's server:** not handled.

### 10303 · Player disconnected
`var_12158` / `_-O6T` · game server → client · TCP game server or UDP match

Shows the "UI_PlayerMessage_HasDisconnected" notice for a player; the time is passed to a match-event hook that does nothing in 11.00.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | entity | uint | the player's entity id |
| 2 | time | uint | match time in ms (unused) |

**Needs:** a running match.
**Gives:** a notice.
**Bifrost's server:** not handled.

### 10304 · Player back in control
`var_8249` / `_-f1E` · game server → client · TCP game server or UDP match

A player who was replaced by a bot (10307) takes control again. The client clears that player's pending inputs, rolls back to the first time, and at the second time removes the bot and makes the player a remote human again.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | entity | uint | the player's entity id |
| 2 | time | uint | match time in ms the player's input resumes (also the rollback point) |
| 3 | switch time | uint | match time in ms the bot is removed |

**Needs:** 10307 for that player.
**Gives:** the human controls the entity again.
**Bifrost's server:** not handled.

### 10305 · Input history rows
`var_11831` / `_-449` · game server → client · TCP game server or UDP match

Rows of inputs appended straight to each player's input history (the same reader as 10106, without moving the stream end). Likely used to catch a client up (unclear).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | rows | uint | number of rows |
| 1.1 | entity | 4 bits | entity id |
| 1.2 | time | uint | match time in ms |
| 1.3 | input | 14 bits | input bits |

**Needs:** a running match.
**Gives:** filled-in input history.
**Bifrost's server:** not handled.

### 10306 · Match setup (rejoin)
`var_2502` / `_-O1G` · game server → client · TCP game server or UDP match

The same match setup as 10310, read with the "rejoin" flag set: it first tears down any match still loaded, closes open screens, then builds the match. With the flag set the client also assigns its controller slot afresh and keeps its earlier sync state. Fields are exactly as in 10310.

See 10310 for the field table.

**Needs:** a game server connection, likely after 10405.
**Gives:** the match rebuilt for a returning player.
**Bifrost's server:** not handled.

### 10307 · Player replaced by a bot
`var_8423` / `_-V5` · game server → client · TCP game server or UDP match

At the given time a player's entity is handed to a local bot of the given level (a missing level means 4, or the bot level of a bot player in some scoring types). The confirmed clock is moved to that time.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | entity | uint | the player's entity id |
| 2 | value | uint | (unclear) read and not used |
| 3 | time | uint | match time in ms of the switch |
| 4 | bot level | uint | bot difficulty; 0 = default |
| 5 | mark | bool | true sets an extra entity flag and clears the "returning" mark (unclear meaning) |

**Needs:** a running match.
**Gives:** the entity is now played by a bot on every client.
**Bifrost's server:** not handled.

### 10308 · Own input re-timed
`var_13015` / `_-Ci` · game server → client · TCP game server or UDP match

The server moved one of this client's own pending inputs to a later time (it arrived late). The client changes that input's time and rolls back. Ignored for entities that are not controlled here.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | entity | uint | the local player's entity id |
| 2 | sent time | uint | the time the input was sent for |
| 3 | new time | uint | the time the server applied it |

**Needs:** inputs sent with 10407.
**Gives:** corrected local input timing and a rollback.
**Bifrost's server:** not handled.

### 10309 · Match inputs
`var_6309` / `_-g1u` · game server → client · TCP game server or UDP match

The main input relay. Each row is matched against that player's pending inputs (a match on the client's own input also measures its round trip), the client rolls back to the earliest changed time, and once the match has started the first field moves the confirmed match clock.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | server time | uint | match time in ms the server has confirmed |
| 2 | rows | uint | number of rows |
| 2.1 | entity | 4 bits | entity id |
| 2.2 | time | uint | match time in ms of the input |
| 2.3 | input | 14 bits | input bits |

**Needs:** a running match (10310 or 10306, then 10313).
**Gives:** everyone's inputs and the confirmed clock.
**Bifrost's server:** not handled (Bifrost's P2P matches use their own packets).

### 10310 · Match setup
`var_5239` / `_-G37` · game server → client · TCP game server or UDP match

The game server's description of the match: seed, the UDP session id, rules and every player. The client updates its room state, moves to the loading state, loads the level, creates all players (local, remote or bot), starts the replay recorder and the match preview (if one is pending). The rejoin variant 10306 uses the same layout.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | match number | uint | (unclear) stored, likely a match id |
| 2 | seed | uint | random seed |
| 3 | session id | small | goes into every UDP datagram this client sends |
| 4 | match kind | uint | 1 = matchmade ("online.Matchmaking"), else custom |
| 5 | value | uint | (unclear) read and not used |
| 6 | playlist | uint | queue/playlist id for the replay recorder |
| 7 | legends | uint | legends per player |
| 8 | report id A | uint | (unclear) id echoed in the client's match report packet |
| 9 | report id B | uint | (unclear) second id echoed with field 8 |
| 10 | value | uint | (unclear) telemetry value |
| 11 | extras | bool | each player record ends with an extras block |
| 12 | rules | uint ×15 | game rules block: flags, (unclear), duration, (unclear), (unclear), scoring type id, eight (unclear) values, mode (unclear) |
| 13 | more | bool | "another player follows"; false ends |
| 13.1 | name number | uint | (unclear) number stored with the name |
| 13.2 | name | string | display name (bots: a skin name may be swapped for a bot name) |
| 13.3 | text | string | (unclear) second string |
| 13.4 | value | uint | (unclear) |
| 13.5 | entity | uint | entity id in the match |
| 13.6 | player id | uint | the player's id |
| 13.7–13.8 | values | uint ×2 | (unclear) |
| 13.9 | local | bool | this player is played on this client |
| 13.10 | second local | bool | (unclear) an extra local player (counted separately) |
| 13.11 | bot | bool | a bot |
| 13.12 | input slot | uint | local controller slot |
| 13.13–13.18 | cosmetics | uint ×6 | (unclear) cosmetic choices |
| 13.19 | slots | uint ×8 | (unclear) eight cosmetic slots |
| 13.20–13.21 | values | small ×2 | (unclear) |
| 13.22 | flag pairs | bool + uint ×2, repeated | "another pair follows", then two uints; false ends (unclear) |
| 13.23 | value | small | (unclear) |
| 13.24 | value | uint | (unclear) |
| 13.25–13.26 | values | small ×2 | (unclear) |
| 13.27–13.28 | values | uint ×2 | (unclear) |
| 13.29 | account name | string | (unclear) account name, kept in the recent-players list |
| 13.30 | legend | repeated field 7 times | one block per legend: |
| 13.30.1 | hero | uint | legend id |
| 13.30.2 | skin | uint | costume id |
| 13.30.3 | flag | bool | (unclear) |
| 13.30.4–13.30.6 | values | uint ×3 | (unclear) per-legend cosmetics |
| 13.31 | extras | only if field 11 | bool present; if true: small, uint, uint (unclear) |

**Needs:** 10400 on the game server connection (after the lobby's 2434).
**Gives:** the match loaded; the UDP session id; the loading state, followed by 10409 and later 10404/10403/10313.
**Bifrost's server:** not handled.

### 10311 · Return to lobby
`var_3` / `_-61Z` · game server → client · TCP game server or UDP match

Ends the client's time on the game server. If the flag is set the client also clears its room and match kind; if it is in a match (or the flag is set) it closes match telemetry and leaves the match. It stores the string as its ticket and later sends 18 to the lobby with its player id and this ticket.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | room over | bool | true also clears the room |
| 2 | ticket | string | ticket for returning to the lobby |

**Needs:** a game server connection.
**Gives:** a lobby ticket; the client sends 18 next.
**Bifrost's server:** not handled.

### 10312 · Match results
`var_8078` / `_-Vf` · game server → client · TCP game server or UDP match

The end-of-match standings. The client builds a result row per player, marks flagged players in its recent-players list, updates telemetry (including a ranked 1v1 loss streak) and opens the results screen; a spectator answers with 10200.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | more | bool | "another player follows"; false ends |
| 1.1 | entity | uint | entity id |
| 1.2 | name number | uint | (unclear) number stored with the name |
| 1.3 | name | string | display name |
| 1.4 | text | string | (unclear) second string |
| 1.5 | place | small | placement; 1 = first |
| 1.6 | player id | uint | the player's id |
| 1.7 | value | small | (unclear) if any row has 0 here, all rows are set to 0 |
| 1.8 | flagged | bool | marks the player in the recent-players list (unclear meaning) |

**Needs:** a finished match.
**Gives:** the results screen.
**Bifrost's server:** not handled.

### 10313 · Match clock
`var_3375` / `_-H3M` · game server → client · TCP game server or UDP match

With the start flag the match begins at the given time: the client sets its clock, hides the loading screens, clears the "never received game server ready" timeout and enters the in-match state. Without it the time only moves the confirmed match clock forward.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | start | bool | true = the match starts now |
| 2 | time | uint | match time in ms |

**Needs:** 10310 and the client's 10403.
**Gives:** the match running, or a newer confirmed time.
**Bifrost's server:** not handled.

### 10314 · Can quit without penalty
`var_10938` / `_-A1a` · game server → client · TCP game server or UDP match

Shows "UI_PlayerMessage_CanQuitNoPenalty" about the opponent, but only during an online match that has not ended.

No payload.

**Needs:** an online match in progress.
**Gives:** a notice only.
**Bifrost's server:** not handled.

### 10315 · Own inputs confirmed
`var_1740` / `_-G5q` · game server → client · TCP game server or UDP match

The server has taken this client's inputs up to a time: the client drops those from its pending list and rolls back to the earliest one dropped.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | entity | uint | the local player's entity id |
| 2 | time | uint | match time in ms confirmed up to |

**Needs:** inputs sent with 10407.
**Gives:** a shorter pending-input list.
**Bifrost's server:** not handled.

### 10316 · UDP bundle over TCP
`var_5171` / `_-Z6k` · game server → client · TCP game server

Carries what would otherwise come as a reliable UDP datagram. The payload is byte-aligned and read by the UDP code: an acknowledgement of the client's reliable sends, then a numbered run of match messages, each of which is dispatched like any other packet.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | ack | raw 2 | u16: the client's reliable messages up to this number are confirmed |
| 2 | first | raw 2 | u16: sequence number of the first message |
| 3 | count | raw 2 | u16: number of messages |
| 3.1 | type | raw 2 | u16 packet number |
| 3.2 | length | raw 2 | u16 payload length in bytes |
| 3.3 | payload | raw length | that packet's bit-packed fields |

Messages already seen (by sequence number) are skipped.

**Needs:** a game server connection with a UDP connection object.
**Gives:** the contained match packets.
**Bifrost's server:** not handled.

### 10400 · Join game server
`var_10081` / `_-e3j` · client → game server · TCP game server · extra u32 B

Sent by `GameCore.method_1851` the moment the TCP game server connection opens, after the lobby's 2434 gave the address and ticket (the UDP socket is opened at the same time). The extra u32 B is never set, so it is 0.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the player's id |
| 2 | ticket | string | the game server ticket from 2434 |

**Needs:** 2434.
**Gives:** the server answers with 10310 (or 10300 on failure).
**Bifrost's server:** not handled (frame.py knows it carries extra u32 B).

### 10401 · Leave match
`var_14513` / `_-b4m` · client → game server · TCP game server

Sent by `GameCore.method_3260` when the player quits during a match and the lobby connection is up; otherwise the client just disconnects from the game server and reconnects.

No payload.

**Needs:** in a match.
**Gives:** the server knows the player quit.
**Bifrost's server:** not handled.

### 10402 · Match end report
`var_12221` / `_-4G` · client → game server · TCP game server

Queued by `LinkUpdater.method_6074` when an online match ends and sent once the confirmed clock passes its time, like the other match reports (10410–10423).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match end time in ms |
| 2 | sudden death | bool | (likely) the match went to sudden death |
| 3 | checksum | uint | sync checksum: sum over players of a hash of their position and velocity values |
| 4 | rules | uint ×15 | the game rules block, as in 10310 field 12 |
| 5 | more | bool | "another player follows"; false ends |
| 5.1 | entity | uint | entity id |
| 5.2 | place | uint | placement; 1 = first |
| 5.3 | finish time | uint | match time in ms the player finished (unclear) |
| 5.4 | damage | f32 | (unclear) damage value kept per player |

**Needs:** an online match ending.
**Gives:** the server's copy of the result.
**Bifrost's server:** not handled.

### 10403 · Ready to start
`var_8576` / `_-22X` · client → game server · TCP game server

Sent by `GameCore.method_687` every tick of the waiting state that 10404 sets, as soon as the level has finished loading; the client then enters the in-match state.

No payload.

**Needs:** 10404.
**Gives:** the server knows this client is ready; it answers with 10313.
**Bifrost's server:** not handled.

### 10404 · Match start time
`var_7226` / `_-n1m` · game server → client · TCP game server or UDP match

Gives the start time and puts the client in a waiting state that sends 10403 once loading is done.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | start time | uint | match time in ms to start from |

**Needs:** 10310.
**Gives:** the waiting state; 10403 follows.
**Bifrost's server:** not handled.

### 10405 · Join game server (rejoin)
`var_4043` / `_-B3S` · client → game server · TCP game server · extra u32 B

Same fields as 10400, sent by `GameCore.method_5994` when the game server connection was opened by the lobby's 2466 (which also closes the menus), likely a rejoin of a match in progress. Extra u32 B is 0.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the player's id |
| 2 | ticket | string | the game server ticket from 2466 |

**Needs:** 2466.
**Gives:** likely 10306.
**Bifrost's server:** not handled (frame.py knows it carries extra u32 B).

### 10406 · Unused
`var_15045` / `_-O2o` · not sent or handled by the 11.00 client or Bifrost's server

### 10407 · Input
`var_5730` / `_-71J` · client → game server · UDP match (reliable; TCP game server if there is no UDP)

One input change of a player on this client, sent by `Class_303.method_2982`. It goes into the reliable UDP queue: the queue is resent every 200 ms (or as soon as something new is queued) until the server acknowledges it. A reliable datagram is: u8 0, u32 player id, u16 session id (10310 field 3), u8 1, u16 first sequence, u16 count, then each message as u16 type, u16 length, payload. The client's own time of sending is kept to measure round trip when the input comes back in 10309.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | entity | 4 bits | entity id |
| 2 | time | uint | match time in ms of the input |
| 3 | input | 14 bits | input bits |

**Needs:** 10310 (session id) and a running match.
**Gives:** the server gets the input; 10309, 10308 or 10315 follow.
**Bifrost's server:** not handled.

### 10408 · UDP ping
`var_6529` / `_-p3U` · both · UDP match

`GameCore.sendUdpPing` sends this at most every 250 ms as a single unreliable datagram (u8 0, u32 player id, u16 session id, u8 0, u16 type, u16 length, payload). The server sends the same fields back; the client keeps the newest sequence and records now minus the timestamp as its ping.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | timestamp | uint | the client's clock in ms when sent |
| 2 | sequence | uint | counts up per ping |

**Needs:** a UDP match connection.
**Gives:** the round-trip time shown and logged.
**Bifrost's server:** not handled.

### 10409 · Level loaded
`var_179` / `_-03r` · client → game server · TCP game server

Sent once by `LinkUpdater.method_5073` while the client is connected to a game server and its level has finished loading.

No payload.

**Needs:** 10310 and the level loaded.
**Gives:** the server knows loading is done.
**Bifrost's server:** not handled.

### 10410 · Report: sudden death
`var_14244` / `_-q4c` · client → game server · TCP game server

Queued when sudden death starts (likely: the world marks the tied players and shows the sudden-death screen). Like all match reports it waits until the confirmed clock passes its time.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms |
| 2 | players | uint | bit mask of the entity ids in sudden death |
| 3 | checksum | uint | sync checksum (see 10402) |

**Needs:** an online match.
**Gives:** a match event for the server.
**Bifrost's server:** not handled.

### 10411 · Report: power used
`var_7243` / `_-N5y` · client → game server · TCP game server

Queued when a player's attack/power runs (unclear exactly which moment).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms |
| 2 | entity | uint | the player |
| 3 | power | uint | power id |
| 4 | root power | uint | id of the power it belongs to in the player's move set |
| 5 | flags | uint | (unclear) power flags |
| 6 | checksum | uint | sync checksum |

**Needs:** an online match.
**Gives:** a match event.
**Bifrost's server:** not handled.

### 10412 · Report: hit
`var_3385` / `_-4b` · client → game server · TCP game server

Queued when an attack connects (`Class_83`).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms |
| 2 | victim | uint | entity hit (likely) |
| 3 | attacker | uint | entity whose power hit (likely) |
| 4 | power | uint | power id |
| 5 | root power | uint | power id in the attacker's move set |
| 6 | item | uint | item id of the weapon used; 0 = none |
| 7 | value | uint | (unclear) |
| 8 | bot level | uint | the attacker's bot level; 0 = human |
| 9 | checksum | uint | sync checksum |
| 10 | amount | f32 | (unclear) likely the damage |
| 11 | flags | uint | (unclear) hit flags |

**Needs:** an online match.
**Gives:** a match event.
**Bifrost's server:** not handled.

### 10413 · Report: item picked up
`var_4513` / `_-R65` · client → game server · TCP game server

Queued when a player takes an item (likely).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms |
| 2 | entity | uint | the player |
| 3 | item | uint | item type id |
| 4 | instance | uint | this item's id in the match |
| 5 | flags | uint | (unclear) |
| 6 | checksum | uint | sync checksum |

**Needs:** an online match.
**Gives:** a match event.
**Bifrost's server:** not handled.

### 10414 · Report: item thrown or used
`var_8866` / `_-C2` · client → game server · TCP game server

Queued from the held-item code (unclear which action; the flags differ for gadgets and weapons).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms |
| 2 | entity | uint | the holder |
| 3 | item | uint | item type id |
| 4 | power item | uint | item id tied to the item's power, else the item type id |
| 5 | instance | uint | this item's id in the match |
| 6 | flags | uint | (unclear) |
| 7 | checksum | uint | sync checksum |

**Needs:** an online match.
**Gives:** a match event.
**Bifrost's server:** not handled.

### 10415 · Report: KO
`var_12364` / `_-S5e` · client → game server · TCP game server

Queued when a player is knocked out (the entity loses a life here).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms |
| 2 | entity | uint | the player knocked out |
| 3 | has attacker | bool | an attacker is known |
| 3.1 | attacker | uint | entity id; only if field 3 is true |
| 4 | flags | uint | (unclear) KO flags |
| 5 | checksum | uint | sync checksum |

**Needs:** an online match.
**Gives:** a match event.
**Bifrost's server:** not handled.

### 10416 · Unused
`var_8371` / `_-Z3G` · not sent or handled by the 11.00 client or Bifrost's server

### 10417 · Report: named action
`var_14312` / `_-z2E` · client → game server · TCP game server

Queued for named actions such as "jump.Ground", "jump.Air", "dash.Jump", dodges and some taunts; the name is turned into a number from a table, and names not in the table are not sent.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms |
| 2 | entity | uint | the player |
| 3 | action | uint | action id |
| 4 | checksum | uint | sync checksum |

**Needs:** an online match.
**Gives:** a match event.
**Bifrost's server:** not handled.

### 10418 · Report: player state
`var_6246` / `_-s3E` · client → game server · TCP game server

Queued from two places in the entity code; the flag tells which (unclear meaning).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms |
| 2 | entity | 4 bits | entity id |
| 3 | flag | bool | (unclear) |
| 4 | checksum | uint | sync checksum |

**Needs:** an online match.
**Gives:** a match event.
**Bifrost's server:** not handled.

### 10419 · Report: item spawned
`var_4352` / `_-l2J` · client → game server · TCP game server

Queued when an item appears on the stage.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms |
| 2 | item | uint | item type id |
| 3 | instance | uint | this item's id in the match |
| 4 | x | bool + uint | signed position (sign bit, then the size) |
| 5 | y | bool + uint | signed position |
| 6 | kind | uint | 1 or 2 depending on the item type (unclear) |
| 7 | checksum | uint | sync checksum |

**Needs:** an online match.
**Gives:** a match event.
**Bifrost's server:** not handled.

### 10420 · Report: item hit
`var_5235` / `_-K47` · client → game server · TCP game server

Queued from the held-item code for each other player in a hit mask (unclear).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms |
| 2 | target | uint | entity affected |
| 3 | holder | uint | entity holding the item |
| 4 | power | uint | power id |
| 5 | root power | uint | power id in the holder's move set |
| 6 | flags | uint | (unclear) |
| 7 | checksum | uint | sync checksum |

**Needs:** an online match.
**Gives:** a match event.
**Bifrost's server:** not handled.

### 10421 · Report: legend slot changed
`var_7892` / `_-L3y` · client → game server · TCP game server

Queued when a player's current legend slot changes (for example after a KO in modes that rotate legends); not sent for some entity kinds.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms |
| 2 | entity | uint | the player |
| 3 | slot | uint | (unclear) the legend slot now active |
| 4 | checksum | uint | sync checksum |

**Needs:** an online match.
**Gives:** a match event.
**Bifrost's server:** not handled.

### 10422 · Report: entity event
`var_7058` / `_-i5b` · client → game server · TCP game server

Queued from the entity and bot-spawn code (unclear which event).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms |
| 2 | entity | 4 bits | entity id |
| 3 | checksum | uint | sync checksum |

**Needs:** an online match.
**Gives:** a match event.
**Bifrost's server:** not handled.

### 10423 · Report: mode counter
`var_1882` / `_-74t` · client → game server · TCP game server

Queued by mode code when a counter changes, for example the zombie kill count in the Horde mode.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | time | uint | match time in ms |
| 2 | value | uint | the counter |
| 3 | flag | bool | (unclear) a room setting |
| 4 | checksum | uint | sync checksum |

**Needs:** an online match in such a mode.
**Gives:** a match event.
**Bifrost's server:** not handled.

### 12000 · Login challenge
`var_7535` / `_-15T` · server → client · TCP lobby · Bifrost: `protocol.CHALLENGE`

The client stores the string; when it logs in it combines it character by character (hex digits XOR) with a stored login secret (unclear which) to make the login proof it sends in 20.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | challenge | string | hex text |

**Needs:** 30 (version) from the client.
**Gives:** the client can now send its login.
**Bifrost's server:** `bh_server/protocol.py`: answers 30 with 12000 holding a random 32-character hex string.

### 12001 · Authentication refused
`var_7611` / `_-W5I` · server → client · TCP lobby

The client shows "Authentication Refused. Offline Mode Only.", disconnects from the lobby and stays offline.

No payload.

**Needs:** a login attempt.
**Gives:** offline mode.
**Bifrost's server:** not sent.

### 12002 · Go offline
`var_4384` / `_-M69` · server → client · TCP lobby · Bifrost: `social.TO_OFFLINE`

The client disconnects from the lobby, marks itself offline and does not try to reconnect.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | reason | uint | read and not used (Bifrost sends 0) |

**Needs:** a lobby connection.
**Gives:** offline mode.
**Bifrost's server:** sent after a 2465 message when a player signs in from another game (`bh_server/protocol.py` `signed_out_elsewhere`), is kicked (`bh_server/social.py` `kick`) or banned (`bh_server/moderation.py` `sign_out_packets`).

### 12003 · Update manifest
`var_2645` / `_-M2H` · server → client · TCP lobby

Points the client at an update manifest. With a URL it fetches the manifest over HTTP (5 s timeout) and applies the files it lists; without one it marks updates as done. A second 12003 while updating sets "Error_FailedToApplyUpdates".

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | has url | bool | a URL follows |
| 2 | url | string | manifest URL; only if field 1 is true |

**Needs:** a lobby connection.
**Gives:** downloaded updates, or "no update".
**Bifrost's server:** not sent.

### 12100 · Keep-alive
`var_14648` / `_-s1n` · both · TCP lobby · Bifrost: `protocol.KEEPALIVE`

The client sends this every 10 s while the lobby connection is up and notes the time of every 12100 it receives; if 20 s pass without one it disconnects and reconnects. (`var_7163` / `_-54g`, which also ends up at 12100, is the counter the game numbers its packets with, not a packet.)

No payload.

**Needs:** a lobby connection.
**Gives:** the connection is kept and checked.
**Bifrost's server:** `bh_server/protocol.py` answers each 12100 with a 12100; `bh_server/game.py` leaves it out of the packet log.

### 12101 · Unused
`var_13080` / `_-Y2o` · not sent or handled by the 11.00 client or Bifrost's server

## Bifrost packets

Bifrost's own packets, 11001 and up. The official game and servers don't have them: Bifrost's client (its additions to the game) and Bifrost's server send them over the same TCP lobby connection and framing as the game's packets, with no extra u32. A few numbers mean different things in each direction; those entries have a table per direction.

### 11001 · Account token
Bifrost · client → server · TCP lobby · Bifrost: `protocol.LINK_TOKEN`

Sends the Discord sign-in token the client saved from an earlier sign-in, so the server knows which account this game belongs to. The client sends it right after it sends 30 Version, only when it has a saved token.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | token | string | the account token from an earlier 11009 |

**Needs:** a token saved by a previous successful 11009.
**Gives:** the server treats the connection as that account (an unknown token means playing as a guest); the following login picks the account's player.
**Bifrost's server:** `bh_server/protocol.py` `on_packet`: looks the token up in the store; if a player is already known, links it to the account and tells social and clans the account is online.

### 11002 · Change player name
Bifrost · client → server · TCP lobby · Bifrost: `protocol.SET_NAME`

Asks the server to rename the player, from Bifrost's name box in the options.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | name | string | the wanted name |

**Needs:** logged in (after 2431).
**Gives:** 2430 with the new name (or the old one if refused); on refusal also an 11013 notice "Your name was not changed: ...". The room shows the new name (2450).
**Bifrost's server:** `bh_server/protocol.py` `on_packet`: `store.set_player_name`, then 2430, and `renamed` updates social, clans and the room.

### 11003 · Custom room defaults
Bifrost · client → server · TCP lobby · Bifrost: `lobby.ROOM_DEFAULTS`

The client sends the game's default custom-room game settings (a stock match) whenever it sends 33 (create room), so the server can build the new room with the game's own defaults.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1–15 | game settings | uint × 15 | the 15 game-setting numbers, written by the game's own settings writer (individual meanings not decoded here) |

**Needs:** sent just before 33.
**Gives:** the server can create custom rooms; without it a create gets 2446 "Update the client: the server needs its room settings."
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle`: stores them as `self.defaults` for later 33 creates.

### 11004 · Match start details
Bifrost · client → server · TCP lobby · Bifrost: `lobby.START_DETAILS`

Tells the server which level the lobby picked and which levels the level select may offer. The client sends it whenever it sends 55 (start), and again after an 11020 queue match is found.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | level | uint | the selected level id (0 is rejected) |
| 2 | candidate count | uint | number of level-select candidates (optional, may end here) |
| 2.1 | candidate | uint | a level id, repeated |
| 3 | random tile level | uint | level id behind the "random" tile, 0 if none |
| 4 | flags | uint | `0x100` marker plus 1 = online Training (only read if the marker bit is set) |

**Needs:** in a room; host of a custom room, or any member of a matched queue room.
**Gives:** the server knows the level; it then opens the level select (2460) or starts with 11005.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle`: queue rooms take the first member's level (swapping a level banned by a player via `queueprefs.pick`) and start when the countdown ends; custom rooms go through `rooms.set_start_level` (and Training adds a bot).

### 11005 · Start local match
Bifrost · server → client · TCP lobby · Bifrost: `lobby.START_LOCAL`

Starts the P2P match on every player's PC with a shared seed and level. If the level select is still showing, the client waits for it to finish (up to 8 s for the roulette) before launching.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | room | uint | the room number |
| 2 | seed | uint | shared random seed |
| 3 | level | uint | level id to play |
| 4 | flags | uint | optional: `0x100` marker plus 1 = Training, 2 = rejoin, 4 = tournament match |

**Needs:** the room's start (55 + 11004 or a queue match) and the level select, if any.
**Gives:** the client launches the local match, starts its P2P session, match report, spectate upload and (with flag 2) the rejoin rebuild.
**Bifrost's server:** `bh_server/lobby.py` `send_start` (to the room's players, then 2401); `bh_server/rejoin.py` `rebuild` sends it to a rejoining player with the rejoin flag.

### 11006 · Spectate or join room
Bifrost · client → server · TCP lobby · Bifrost: `lobby.SPECTATE`

Asks to watch a room by number, or to play in it if there is space. Sent by the Discord presence mod when someone joins from Discord, and by the autopilot mod.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | room | uint | room number |
| 2 | play | bool | optional: true = join as a player if the room is waiting and has space |

**Needs:** logged in.
**Gives:** joins the room as a player, or as a spectator (2445 spectator room), or 2446 if the room can't be watched.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle`: `join_room` when `play` and there is space, else `spectate_room` (also sends 11049 of the members and, mid-match, a late spectate ticket).

### 11007 · Ask for Discord sign-in
Bifrost · client → server · TCP lobby · Bifrost: `protocol.LOGIN_START`

Sent when the player presses sign in (Options) or "Log in" on the sign-in prompt.

No payload.

**Needs:** connected and logged in.
**Gives:** an 11008 with the sign-in link or the reason there is none.
**Bifrost's server:** `bh_server/protocol.py` `on_packet`: starts a login state in `logins` and builds the Discord authorize URL.

### 11008 · Discord sign-in link
Bifrost · server → client · TCP lobby · Bifrost: `protocol.LOGIN_LINK`

The answer to 11007. The client opens the link in the browser and shows the code to type there; links that are not on discord.com are refused.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | ok | bool | true if a link follows |
| 2 | link or message | string | the Discord authorize URL, or why there is none |
| 3 | code | string | only if field 1 is true: the code the web page asks for |

**Needs:** 11007.
**Gives:** the browser opens Discord's sign-in; the result comes later as 11009.
**Bifrost's server:** `bh_server/protocol.py` `on_packet` (refuses before login or without a Discord client secret).

### 11009 · Discord sign-in result
Bifrost · server → client · TCP lobby · Bifrost: `protocol.LOGIN_RESULT`

Sent when the browser side of the sign-in finishes. On success the client saves the token (for 11001 next time) and shows "Logged in".

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | signed in | bool | true on success |
| 2 | token or message | string | the account token, or the failure message |
| 3 | account name | string | the account's name (empty on failure) |

**Needs:** 11008 and the Discord OAuth page completed.
**Gives:** the connection is now that account; may be followed by 2430 (name), 11090, 11099, 11023 and a whitelist notice.
**Bifrost's server:** `bh_server/protocol.py` `signed_in` / `sign_in_failed`, called from `bh_server/web.py`'s OAuth callback.

### 11010 · Sign-in needed
Bifrost · server → client · TCP lobby · Bifrost: `protocol.LOGIN_NEEDED`

Sent at login when this player has no account yet and the server offers Discord sign-in. Once the main menu shows, the client asks "This server needs your Discord sign-in once" with Log in / Later.

No payload.

**Needs:** 2431 login accepted for a player without an account.
**Gives:** a sign-in prompt on the main menu.
**Bifrost's server:** `bh_server/protocol.py` `on_packet`, at the end of login.

### 11011 · P2P hello
Bifrost · client → server · TCP lobby · Bifrost: `lobby.P2P_HELLO`

The client opens its UDP socket for the P2P match and tells the server where it can be reached. Sent when a match is found or the map vote opens (to ping early) and again when the match begins.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | port | uint | the client's local UDP port |
| 2 | address count | uint | number of addresses (server keeps at most 8) |
| 2.1 | address | string | a local IPv4 address, repeated |

**Needs:** in a started room or a matched queue room.
**Gives:** the server records the player's UDP port and addresses (plus the public address it sees) and sends 11012 to every player.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` → `rooms.p2p_hello` and `send_peers`.

### 11012 · P2P peers
Bifrost · server → client · TCP lobby · Bifrost: `lobby.P2P_PEERS`

Tells each player how to reach the other players: the server's UDP relay with a personal relay token, and each other player's port and (when direct connection is allowed) addresses. It ends with the players the client should ask about a direct connection.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | relay port | uint | the server's UDP relay port |
| 2 | token high | uint | upper 32 bits of this player's relay token |
| 3 | token low | uint | lower 32 bits of the relay token |
| 4 | peer count | uint | number of other players |
| 4.1 | player id | uint | the other player |
| 4.2 | port | uint | its UDP port |
| 4.3 | address count | uint | 0 unless a direct connection is allowed |
| 4.3.1 | address | string | an IPv4 address, repeated |
| 5 | ask | uint | `0x100` marker plus the number of ids that follow (optional) |
| 5.1 | player id | uint | a non-friend to ask "Direct connection?" about, repeated |

**Needs:** 11011 from the players in a started or matched room.
**Gives:** the client pings peers directly and through the relay, registers with the relay, and may show the "Direct connection?" question (answered with 11025).
**Bifrost's server:** `bh_server/lobby.py` `Lobby.send_peers` (addresses shown per the `p2p_direct` setting: friends, everyone, or both players chose DIRECT); relay tokens from `bh_server/relay.py` `Tokens.issue`.

### 11013 · Server notice / room mods list
Bifrost · both · TCP lobby · Bifrost: `clans.POPUP`, `lobby.MODS_LIST`, `protocol.NOTICE`, `rejoin.NOTICE`, `social.NOTICE`

One number with two meanings, told apart by direction. Server → client it is a pop-up notice: the client shows the text in a "Bifrost" dialog. Client → server it is the player's list of gameplay content mods, sent whenever the client sends 41 (lock in a loadout), so the server can check that everyone in the room has the same mods.

Server → client (notice):

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | text | string | the message to show |

Client → server (mods list):

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | fingerprint | uint | FNV hash of all entries joined |
| 2 | count | uint | number of entries (at most 60) |
| 2.1 | entry | string | `id@version#hash` of one gameplay mod, repeated |

**Needs:** notice: logged in. Mods list: in a room.
**Gives:** notice: a dialog. Mods list: the server knows the player's gameplay mods.
**Bifrost's server:** notices come from `bh_server/protocol.py` `notice` (whitelist refusals, refused renames, "playing as a guest", at most one per 5 s), `bh_server/social.py` `_notice` (friend added or removed, chat muted or unmuted, couldn't follow or join, `!friends` output), `bh_server/clans.py` `_popup` (guild level reached, sign in to create or join a clan) and `bh_server/rejoin.py` ("The match you left has ended.", "Couldn't rejoin: ..."). The mods list is read in `bh_server/lobby.py` `Lobby.handle` and used by the start check (content mods differ → 11014 and 11015/11016) and by queues (gameplay mods refused).

### 11014 · Start refused
Bifrost · server → client · TCP lobby · Bifrost: `lobby.START_REFUSED`

Sent when the server won't start the match: the room's content mods differ, or a queue was readied with gameplay mods on. The client resets the lobby's start and shows "Can't start".

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | reason | string | the explanation shown to the player |

**Needs:** 55 start (custom room) or 31 Ready for Battle (queue), with 11013 mods lists that don't match.
**Gives:** a dialog; the room stays in character select.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` (START and READY_QUEUE branches).

### 11015 · Upload mods request
Bifrost · server → client · TCP lobby · Bifrost: `lobby.MODS_UPLOAD`, `modshare.UPLOAD_PACKET`

Asks the client to upload some of its mods to the server's web side, with a ticket. Sent to a room host whose start was refused because others lack its mods, and in reply to 11072. For mods the player chose to share the client uploads at once; otherwise it asks "Send your mods for this room?".

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | web port | uint | the server's HTTP port |
| 2 | ticket | string | upload ticket for the web side |
| 3 | count | uint | number of mods |
| 3.1 | entry | string | `id@version#hash` of a mod to upload, repeated |

**Needs:** a mods library on the server (`web_port` set); 11014 with differing mods, or 11072.
**Gives:** the client POSTs each mod to `/mods/upload` with the ticket.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.offer_mods`; `bh_server/modshare.py` `Desk._share`.

### 11016 · Mods offer
Bifrost · server → client · TCP lobby · Bifrost: `lobby.MODS_OFFER`, `modshare.OFFER_PACKET`

Offers a room member the host's mods. The client works out which mods to switch off, switch on or download and asks "Use (host)'s mods?"; accepting downloads them from the server and asks for a restart.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | web port | uint | the server's HTTP port |
| 2 | ticket | string | download ticket |
| 3 | host | string | the host's name |
| 4 | count | uint | number of mods |
| 4.1 | entry | string | `id@version#hash` of one of the host's mods, repeated |

**Needs:** a refused start because mods differ (11014).
**Gives:** a dialog; on accept the mods are fetched over HTTP.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.offer_mods` (to members whose fingerprint differs, at most every 30 s).

### 11017 · Room network settings
Bifrost · both · TCP lobby · Bifrost: `lobby.ROOM_NET`

The host's P2P network settings for the room (rollback, input delay, smoothing). The host's client sends them as it starts the match; the server passes them to the room's players, who use them when the match begins. When the host changes them, other players see a "NETWORK SETTINGS" dialog.

Client → server:

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | rollback | bool | rollback on |
| 2 | delay | uint | input delay in ms, 0 = automatic (server caps at 240) |
| 3 | smoothing | bool | smoothing on |
| 4 | smoothing ms | uint | smoothing time in ms (server caps at 1000) |

Server → client:

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | room | uint | room number |
| 2 | host | uint | the host who set them; 0 when these are the server's defaults sent at the start |
| 3 | rollback | bool | rollback on |
| 4 | delay | uint | input delay in ms, 0 = automatic |
| 5 | smoothing | bool | smoothing on |
| 6 | smoothing ms | uint | smoothing time in ms |

**Needs:** client → server: host of a waiting (not started) room.
**Gives:** every player's client applies the room's settings at match start (`RoomNet.apply`).
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` and `room_net_packet`; `send_start` sends the defaults (rollback on, delay auto, smoothing 100 ms) if the host sent none.

### 11018 · Player cosmetics
Bifrost · both · TCP lobby · Bifrost: `lobby.COSMETICS`

Shares the cosmetics the other PCs need to draw a player in a P2P match (avatar, border, theme, trail, taunts, title, emotes, podium). The client sends its own whenever it receives 2445 (room) or 11020; the server stores them and sends each member's to the others when the match starts.

Client → server:

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | count | uint | number of values (16; server keeps at most 32) |
| 1.1 | value | uint | in order: avatar, season border, player theme, trail effect, two taunt values, title, 8 emote slots, podium |

Server → client:

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | whose cosmetics |
| 2 | count | uint | number of values |
| 2.1 | value | uint | as above |

**Needs:** in a room.
**Gives:** other clients fill the player's loadout with them (falling back to game defaults when none came).
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle` (stores, sends 2437 when changed, updates social and clan avatars) and `cosmetics_packet` in `send_start`.

### 11019 · Legend levels
Bifrost · server → client · TCP lobby · Bifrost: `stats.LEGEND_LEVELS`

The account's level and XP for each legend, sent after login so the game shows server-kept legend levels.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | count | uint | number of legends |
| 1.1 | legend | uint | legend (hero) id |
| 1.2 | level | uint | legend level |
| 1.3 | xp | uint | total XP |
| 1.4 | level start | uint | XP where this level began |
| 1.5 | next level | uint | XP needed for the next level |

**Needs:** login with a signed-in account that has legend XP.
**Gives:** the game's inventory gets those legend levels.
**Bifrost's server:** `bh_server/stats.py` `legend_levels_packet`, sent from `Stats.after_login`.

### 11020 · Queue match found
Bifrost · server → client · TCP lobby · Bifrost: `lobby.MATCH_FOUND`

Sent with 2436 when matchmaking puts parties together (or a rematch is agreed). Carries the extra facts Bifrost shows: head-to-head record, series score, ratings before the match, rank cards and team leaders. The client marks the match as a queue match, sends 11004 and starts P2P pinging.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | room | uint | the match room |
| 2 | host | uint | the room's host |
| 3 | playlist | uint | playlist id |
| 4 | known | bool | head-to-head record found (1v1 only) |
| 5 | matches | uint | earlier matches between the two |
| 6 | wins | uint | of those, won by this player |
| 7 | series count | uint | players with series wins |
| 7.1 | player id | uint | repeated |
| 7.2 | wins | uint | wins in this series |
| 8 | rating count | uint | rated players (ranked only) |
| 8.1 | player id | uint | repeated |
| 8.2 | rating | uint | rating before the match |
| 8.3 | stars | uint | shown tier stars |
| 8.4 | games | uint | games played on the ladder |
| 9 | cards | string | JSON: player id → rank card (rating, peak, games, wins, streak, rank, last, placement, legend) |
| 10 | leader count | uint | team leaders (team playlists) |
| 10.1 | team | uint | team number |
| 10.2 | player id | uint | that team's leader |

**Needs:** a queue room readied (31) and matched.
**Gives:** the client's queue match state, rating tags and the start of 11004 / 11011.
**Bifrost's server:** `bh_server/lobby.py` matchmaker `send_found` (called from `found`).

### 11021 · Profile picks snapshot
Bifrost · client → server · TCP lobby · Bifrost: `picks.SNAPSHOT`

The client's full set of profile picks (avatar, title, emote and emoji slots, and which KO effects, sidekicks, frames, themes, podiums and victory/defeat poses are selected), sent once after login and whenever they change, so the server can give them back at the next login.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | avatar | uint | avatar id |
| 2 | title | uint | title id |
| 3–10 | taunts | uint × 8 | emote slots |
| 11–30 | emojis | uint × 20 | emoji slots |
| 31 | set words | uint | count, then that many uint words: bit mask of selected KO effects; the same follows for sidekick, frame, theme, podium, victory and defeat (7 sets, at most 64 words each) |

**Needs:** logged in (kept per account, or per player for guests).
**Gives:** the server stores the picks.
**Bifrost's server:** `bh_server/picks.py` `read_snapshot` / `save`; at login `after_login` returns them as the game's 2471 and 2901.

### 11022 · Server save upload
Bifrost · client → server · TCP lobby · Bifrost: `cloudsave.UPLOAD`

Uploads the client's settings, key binds and legend setups (compressed) so another PC can get them. Sent after comparing with 11023, and again whenever the local save changes.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | format | uint | save format (1) |
| 2 | client time | uint | when the save was made |
| 3 | what | string | list of included stores, e.g. "...,settings" |
| 4 | length | small | packed size (at most 60000) |
| 5 | data | raw n | zlib-compressed AMF object |

**Needs:** signed in (guests are ignored); not switched off by the player.
**Gives:** the server keeps one save per account and answers with 11023 (without data).
**Bifrost's server:** `bh_server/cloudsave.py` `Locker.handle`.

### 11023 · Server save held
Bifrost · server → client · TCP lobby · Bifrost: `cloudsave.HELD`

Tells the client what save the server holds. After login it includes the data: if the server's copy is newer the client keeps it and asks for a restart to load it; otherwise it uploads its own.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | held | bool | a save exists |
| 2 | client time | uint | only if held: when it was made |
| 3 | size | uint | only if held: packed size |
| 4 | saved | uint | only if held: server time it was stored (Unix seconds) |
| 5 | what | string | only if held: included stores |
| 6 | with data | bool | only if held: the data follows |
| 7 | length | small | only if field 6: packed size |
| 8 | data | raw n | only if field 6: the save |

**Needs:** login or sign-in with an account; or an 11022 / 11024.
**Gives:** the client's view of the server copy (and possibly a pending save loaded at next start).
**Bifrost's server:** `bh_server/cloudsave.py` `held_packet` from `after_login` (with data), after upload and after delete.

### 11024 · Delete server save
Bifrost · client → server · TCP lobby · Bifrost: `cloudsave.DELETE`

Deletes the account's save from the server and stops further uploads until the player keeps it on the server again.

No payload.

**Needs:** signed in.
**Gives:** an 11023 saying nothing is held.
**Bifrost's server:** `bh_server/cloudsave.py` `Locker.handle`.

### 11026 · Set profile bio
Bifrost · client → server · TCP lobby · Bifrost: `stats.BIO`

Sets the player's bio text, from Bifrost's profile window.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | bio | string | the text (server keeps at most 140 characters) |

**Needs:** signed in with an account.
**Gives:** the bio shows on the player's server profile.
**Bifrost's server:** `bh_server/stats.py` `Stats.handle` → `ledger.set_bio`.

### 11040 · Match report
Bifrost · client → server · TCP lobby · Bifrost: `stats.REPORT`

Each player's client reports the result of a P2P match when it reaches the end screen (not if the match was left or a player was lost). The server compares the reports from all players before counting it.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | version | uint | 1 or 2 (the client sends 2) |
| 2 | room | uint | room number |
| 3 | seed | uint | match seed from 11005 |
| 4 | level | uint | level id |
| 5 | map | string | level name |
| 6 | mode | string | game mode name key |
| 7 | duration | uint | match length in ms |
| 8 | player count | uint | 1 to 8 |
| 8.1 | player id | uint | 0 for a bot |
| 8.2 | legend | uint | legend id |
| 8.3 | legend name | string | |
| 8.4 | team | uint | |
| 8.5 | place | uint | finishing place |
| 8.6 | KOs | uint | |
| 8.7 | falls | uint | |
| 8.8 | self-KOs | uint | |
| 8.9 | damage dealt | uint | |
| 8.10 | damage taken | uint | |
| 9 | breakdown | bool | version 2 only, once per player: a breakdown follows |
| 9.1 | values | uint × 23 | damage and KOs by unarmed, thrown, each weapon and gadgets, weapon hold times, team KOs, and gadget and snowball damage/KOs |

**Needs:** a match started with 11005.
**Gives:** the result is recorded once all players report (or the window closes); the server answers with 11041, and refreshes 2401 participants and 2437.
**Bifrost's server:** `bh_server/stats.py` `read_report`, `Results.report`; ratings, XP and leaderboards follow.

### 11041 · Match recorded
Bifrost · server → client · TCP lobby · Bifrost: `stats.RECORDED`

The server's verdict on a reported match: counted or not, a message, and the rating change for the end screen. Also sent right away when a report can't be read or is refused.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | counted | bool | the match was counted |
| 2 | message | string | e.g. "Match recorded. 1v1 rating 1234 (+12)." |
| 3 | change | string | JSON rating change (ladder, before, after, tier progress, team and legend ratings, every player's before/after); missing on a refusal |

**Needs:** 11040.
**Gives:** the end screen shows rating changes, or a short message; cached server stats are cleared.
**Bifrost's server:** `bh_server/stats.py` `Results._notify` (with `message` and `rating_change`) and `Stats.handle` for refusals; ranked results also send ranks/ratings packets.

### 11042 · Stats question
Bifrost · client → server · TCP lobby · Bifrost: `stats.ASK`

Asks the server for JSON data for Bifrost's windows (leaderboards, profiles, recent matches, tournaments). Answered with 11043 under the same query.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | query | string | e.g. `me`, `leaderboard`, `leaderboard:...`, `legends[:n]`, `clans[:n]`, `seasons`, `ranked:LADDER[:n]`, `recent`, `player:...`, `profile:...`, `team:...`, `activity`, `tournaments`, `tournament:ID`, `tmatch:...`, `tpool:...` (at most 64 characters) |

**Needs:** logged in.
**Gives:** an 11043.
**Bifrost's server:** `bh_server/stats.py` `Stats.handle` → `answer`; tournament queries go to `bh_server/tournaments.py` `handle` → `read`.

### 11043 · Stats answer
Bifrost · server → client · TCP lobby · Bifrost: `stats.ANSWER`

The JSON answer to 11042 (also sent unasked by tournaments after 11047). Large answers come in several packets, each body starting with `\x01k n\x01` (part k of n); the client joins them.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | query | string | the query answered (also `tournament-act` for 11047 results) |
| 2 | body | string | JSON, or one part of it |

**Needs:** 11042 or 11047.
**Gives:** the client caches the answer and its window shows it.
**Bifrost's server:** `bh_server/stats.py` `Stats.handle`; `bh_server/tournaments.py` `_answer` (with `answer_parts`).

### 11044 · Profile for stats
Bifrost · client → server · TCP lobby · Bifrost: `stats.PROFILE`

The player's current look for the server's profile pages and leaderboards, sent when it changes.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | version | uint | 1 |
| 2 | avatar | uint | avatar id |
| 3 | title | uint | title id |
| 4 | podium | uint | first podium |
| 5 | legend | uint | locked-in legend |
| 6 | costume | uint | its costume |
| 7 | colour | uint | its colour scheme |

**Needs:** signed in with an account.
**Gives:** the server's profile shows them.
**Bifrost's server:** `bh_server/stats.py` `read_profile` → `ledger.set_profile`.

### 11045 · Legend names
Bifrost · client → server · TCP lobby · Bifrost: `stats.LEGEND_NAMES`

The game's legend names by id, sent once per session after the first 11044, so the server's pages can name legends.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | count | uint | number of legends (server reads at most 500) |
| 1.1 | legend | uint | legend id |
| 1.2 | name | string | its name (up to 40 characters) |

**Needs:** signed in.
**Gives:** the server stores the names.
**Bifrost's server:** `bh_server/stats.py` `Stats.handle` → `ledger.set_legend_names`.

### 11046 · Set region
Bifrost · client → server · TCP lobby · Bifrost: `stats.REGION`

Sets the player's region for regional leaderboards, from Bifrost's leaderboard window.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | region | uint | index into the server's region list |

**Needs:** signed in.
**Gives:** the account's region is stored.
**Bifrost's server:** `bh_server/stats.py` `Stats.handle` → `ledger.set_region`.

### 11047 · Tournament action
Bifrost · client → server · TCP lobby · Bifrost: `tournaments.ACT`

An action in Bifrost's tournaments window: create, join, leave, check in, start, score, seed, pause, cancel and so on.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | action | string | e.g. `create`, `join`, `leave`, `checkin`, `start`, `score`, `seed`, `swap_seeds`, `reseed`, `pause`, `resume`, `extend`, `award`, `disqualify`, `undo`, `cancel`, `settings`, `team_invite`, `team_answer`, `admin` (at most 16 characters) |
| 2 | args | string | JSON object of the action's arguments |

**Needs:** signed in; rights depend on the action.
**Gives:** 11043 answers for `tournament-act` (ok, message), `tournaments` and the tournament's own page.
**Bifrost's server:** `bh_server/tournaments.py` `handle` → `act`.

### 11049 · Mod items
Bifrost · both · TCP lobby · Bifrost: `lobby.MOD_ITEMS`

Maps the item ids that content mods added (costumes, weapon skins, colours, avatars, taunts and so on) so other players' PCs can draw them, or fall back to a stand-in. The client sends its list when it receives 2445 or 11020; the server passes it to the room.

Client → server:

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | items | string | JSON list of `[table, id, key, fallback id]` (at most 64 KB) |

Server → client:

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | whose items |
| 2 | items | string | that player's JSON list |

**Needs:** in a room (or spectating one).
**Gives:** each client knows every member's modded item ids.
**Bifrost's server:** `bh_server/lobby.py` `on_mod_items` and `send_members_items`; also sent to spectators (`spectate_room`) and rejoining players (`bh_server/rejoin.py`).

### 11050 · Add friend
Bifrost · client → server · TCP lobby · Bifrost: `social.FRIEND_ADD`

Sent by the game's "add friend" buttons (player card, room and guild screens), which Bifrost hooks.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the player to befriend |

**Needs:** both players signed in with Discord.
**Gives:** a friend request (or friendship), told with an 11013 notice.
**Bifrost's server:** `bh_server/social.py` `request_friend`.

### 11051 · Remove friend
Bifrost · client → server · TCP lobby · Bifrost: `social.FRIEND_REMOVE`

Ends a friendship, from Bifrost's friends UI.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | the friend to remove |

**Needs:** signed in.
**Gives:** an 11013 notice "You and X are no longer friends." (or "You weren't friends.").
**Bifrost's server:** `bh_server/social.py` `remove_friend`.

### 11052 · Friends window command
Bifrost · client → server · TCP lobby · Bifrost: `social.FRIENDS_CMD`

A command from Bifrost's friends window. Always answered with 11053.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | action | string | `list`, `add`, `accept`, `decline`, `cancel` or `remove` |
| 2 | who | string | for `add` a player name; otherwise a Discord id (empty for `list`) |

**Needs:** signed in with Discord for anything but `list`.
**Gives:** an 11053 with the result and the whole friends state.
**Bifrost's server:** `bh_server/social.py` `window_action` and `friends_state`.

### 11053 · Friends window state
Bifrost · server → client · TCP lobby · Bifrost: `social.FRIENDS_STATE`

The friends window's full contents plus the result of the last command.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | state | string | JSON: `signed_in`, `me`, `friends`, `incoming`, `outgoing`, `online`, `recent` (each entry: id, pid, name, status, online, ...), `ok`, `message` |

**Needs:** 11052.
**Gives:** the friends window redraws.
**Bifrost's server:** `bh_server/social.py` `friends_state`.

### 11060 · Replay upload part
Bifrost · client → server · TCP lobby · Bifrost: `replays.PART`

After a P2P match, when the game has saved the replay, the client uploads it in parts of up to 48 KB so others can watch it on the server's match page.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | version | uint | 2 (1 has no mods list) |
| 2 | room | uint | room number |
| 3 | seed | uint | match seed |
| 4 | total | uint | whole replay size in bytes (at most 8 MB) |
| 5 | offset | uint | where this part starts |
| 6 | length | uint | part size |
| 7 | data | raw n | the part |
| 8 | mod count | uint | version 2: number of mods (sent only in the first part) |
| 8.1 | entry | string | content mod entry, repeated |

**Needs:** a counted match the player played; sharing replays on.
**Gives:** after the last part, the server checks and keeps it and answers with 11061.
**Bifrost's server:** `bh_server/replays.py` `Replays.handle` (parts must arrive in order) → `Shelf.put`.

### 11061 · Replay stored
Bifrost · server → client · TCP lobby · Bifrost: `replays.STORED`

The result of an 11060 upload.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | ok | bool | the replay was kept |
| 2 | message | string | e.g. "Replay shared: it's on the server's match page." or why not |

**Needs:** 11060.
**Gives:** the client logs and shows the message.
**Bifrost's server:** `bh_server/replays.py` `_stored`.

### 11062 · Ask for replay
Bifrost · client → server · TCP lobby · Bifrost: `replays.GET`

Asks for the stored replay of a match, from a match page or link; the client may play it once saved.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | room | uint | room number |
| 2 | seed | uint | match seed |

**Needs:** logged in.
**Gives:** an 11064 if the replay used mods, then 11063 parts.
**Bifrost's server:** `bh_server/replays.py` `Replays.handle`.

### 11063 · Replay data
Bifrost · server → client · TCP lobby · Bifrost: `replays.DATA`

The replay in parts of up to 48 KB. A total of 0 means the server keeps none. The client puts the parts together and saves the replay in the Replays list.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | room | uint | room number |
| 2 | seed | uint | match seed |
| 3 | total | uint | whole size, 0 = no replay |
| 4 | offset | uint | where this part starts |
| 5 | length | uint | part size |
| 6 | data | raw n | the part |
| 7 | name | string | file name to save as |
| 8 | mod count | uint | content mods the recording used |
| 8.1 | entry | string | repeated |

**Needs:** 11062.
**Gives:** a saved replay (played at once if asked); a warning if it was recorded with other mods.
**Bifrost's server:** `bh_server/replays.py` `data_packets`.

### 11064 · Replay mods offer
Bifrost · server → client · TCP lobby · Bifrost: `replays.MODS`

Sent before 11063 when the replay was recorded with content mods: which of them the server can give, with a download ticket.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | room | uint | room number |
| 2 | seed | uint | match seed |
| 3 | web port | uint | the server's HTTP port |
| 4 | ticket | string | download ticket (empty if none held) |
| 5 | count | uint | mods the server holds |
| 5.1 | entry | string | repeated |

**Needs:** 11062 for a replay with mods.
**Gives:** when the replay arrives the client can offer to switch on or download those mods.
**Bifrost's server:** `bh_server/replays.py` `_offer_mods`.

### 11070 · Ask for mods library
Bifrost · client → server · TCP lobby · Bifrost: `modshare.LIBRARY_ASK`

Opens the server's public mods library in the Mods window.

No payload.

**Needs:** logged in.
**Gives:** an 11071.
**Bifrost's server:** `bh_server/modshare.py` `Desk.send_library`.

### 11071 · Mods library
Bifrost · server → client · TCP lobby · Bifrost: `modshare.LIBRARY`

The list of mods on the server, with a ticket to preview and download them over HTTP.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | web port | uint | the server's HTTP port (0 if the server doesn't share mods) |
| 2 | ticket | string | download ticket |
| 3 | body | string | JSON: `mods` (catalog rows), optional `message`, or `error` |

**Needs:** 11070, or a like/unlike via 11088.
**Gives:** the Mods window lists the server's mods.
**Bifrost's server:** `bh_server/modshare.py` `library_packet`.

### 11072 · Share mod
Bifrost · client → server · TCP lobby · Bifrost: `modshare.SHARE`

Offers one of the player's mods to the server, private or for public review.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | count | uint | number of entries (at most 20 read; the client sends 1) |
| 1.1 | entry | string | `id@version#hash` |
| 2 | public | uint | 1 = submit for public review, else private |

**Needs:** signed in with Discord; room in the account's quota.
**Gives:** an 11015 asking for the files, or 11087 with a message (refused, or an existing copy made public).
**Bifrost's server:** `bh_server/modshare.py` `Desk._share`.

### 11073 · Back from scoreboard
Bifrost · client → server · TCP lobby · Bifrost: `lobby.BACK_FROM_SCOREBOARD`

Sent when the client leaves the end-of-match screen.

No payload.

**Needs:** a finished match in the room.
**Gives:** the others get 2548 (this player is back), the player is un-readied, and the player gets the room again (2445).
**Bifrost's server:** `bh_server/lobby.py` `Lobby.back_from_scoreboard`.

### 11080 · Spectate feed start
Bifrost · client → server · TCP lobby · Bifrost: `spectate.UP_START`

When a P2P match starts, each player's client offers the match set-up for spectators, built from its replay recorder; the server uses the room host's. Its body has the same layout as the game's 10104 spectate match start, which the server forwards to spectators unchanged.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | seed | uint | match seed |
| 2 | level | uint | level id |
| 3 | team mode | uint | 0 |
| 4–5 | (unused) | uint × 2 | 0 |
| 6 | heroes | uint | legends per player |
| 7 | extras | bool | per-player extras follow |
| 8 | game settings | (game settings block) | written by the game's settings writer |
| 9 | player count | uint | then each player's entity, id, name and full loadout, as in 10104 |

**Needs:** host of a started room.
**Gives:** the server opens a spectate feed and sends 2420 tickets to the room's spectators.
**Bifrost's server:** `bh_server/spectate.py` `handle` (ignored from anyone but the started room's host).

### 11081 · Spectate feed inputs
Bifrost · client → server · TCP lobby · Bifrost: `spectate.UP_INPUTS`

Batches of confirmed inputs for the spectate feed, sent every frame with new inputs or a heartbeat every 250 ms. Same body as the game's 10106 spectate inputs.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | count | uint | number of rows |
| 1.1 | entity | 4 bits | player entity |
| 1.2 | time | uint | ms since match start |
| 1.3 | input | 14 bits | the input bits from then on |

**Needs:** 11080 accepted.
**Gives:** the host's batches are stored and streamed to spectators as 10106; other players' batches are kept as spares in case the host leaves.
**Bifrost's server:** `bh_server/spectate.py` `handle` and `_spare` / `_handoff`.

### 11082 · Spectate feed end
Bifrost · client → server · TCP lobby · Bifrost: `spectate.UP_END`

Ends the feed when the P2P session ends.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | abrupt | bool | the match ended early |
| 2 | at | uint | last uploaded time, ms |

**Needs:** 11080.
**Gives:** on an abrupt end spectators get 10102 (stop at that time); after 6 s spectators are sent back to the room.
**Bifrost's server:** `bh_server/spectate.py` `handle`.

### 11083 · Rejoin inputs
Bifrost · server → client · TCP lobby · Bifrost: `rejoin.REBUILD_INPUTS`

When a player rejoins a running match (53), the server restarts the match on its PC (11005 with the rejoin flag) and sends all recorded input batches, then the live ones, so it can fast-forward to the others.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | count | uint | number of rows |
| 1.1 | entity | 4 bits | player entity |
| 1.2 | time | uint | ms since match start |
| 1.3 | input | 14 bits | input bits |

**Needs:** 53 rejoin with the server's `rejoin` set to rebuild, and a running spectate feed.
**Gives:** the client rebuilds the match, then hands back to P2P (HELLO_BACK datagram).
**Bifrost's server:** `bh_server/rejoin.py` `rebuild` (copies of the feed's 11081 batches).

### 11084 · Ask for mod by key
Bifrost · client → server · TCP lobby · Bifrost: `modshare.KEY_ASK`

Looks up a shared mod by the key its owner gave out.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | key | string | the mod's key (at most 40 characters) |

**Needs:** logged in.
**Gives:** an 11085.
**Bifrost's server:** `bh_server/modshare.py` `Desk._by_key`.

### 11085 · Mod by key
Bifrost · server → client · TCP lobby · Bifrost: `modshare.KEY`

The mod found by 11084, with a ticket to download it.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | web port | uint | the server's HTTP port |
| 2 | ticket | string | download ticket |
| 3 | body | string | JSON: `mod` (with its key) or `error` |

**Needs:** 11084.
**Gives:** the Mods window can download that mod.
**Bifrost's server:** `bh_server/modshare.py` `library_packet`.

### 11086 · Ask for my mods
Bifrost · client → server · TCP lobby · Bifrost: `modshare.MINE_ASK`

Opens "my mods on the server".

No payload.

**Needs:** signed in with Discord.
**Gives:** an 11087.
**Bifrost's server:** `bh_server/modshare.py` `Desk.send_mine`.

### 11087 · My mods
Bifrost · server → client · TCP lobby · Bifrost: `modshare.MINE`

The account's mods on the server, its quota and, for reviewers, the review queue.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | web port | uint | the server's HTTP port |
| 2 | ticket | string | download ticket |
| 3 | body | string | JSON: `mods`, `used`, `bytes`, `max`, `max_bytes`, `reviewer`, `review`, `message`, or `error` |

**Needs:** 11086, 11072 or 11088.
**Gives:** the Mods window's own-mods page.
**Bifrost's server:** `bh_server/modshare.py` `Desk.send_mine`.

### 11088 · Mod setting
Bifrost · client → server · TCP lobby · Bifrost: `modshare.SET`

Changes a mod on the server: like it, make it public or private, delete it, or (reviewers) approve or reject it.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | mod | string | the mod's server id |
| 2 | action | string | `like`, `unlike`, `public`, `private`, `delete`, `approve` or `reject` |
| 3 | note | string | reason (for review actions), else empty |

**Needs:** signed in; owner or reviewer for most actions.
**Gives:** 11071 for like/unlike, 11087 with a message otherwise.
**Bifrost's server:** `bh_server/modshare.py` `Desk._set` → `ModLibrary.like` / `set`.

### 11090 · Perks
Bifrost · server → client · TCP lobby · Bifrost: `protocol.PERKS`

Tells the client the account may use the Developer title. Sent only when it may.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | developer | bool | Developer title allowed |

**Needs:** login or sign-in with an account in the server's `developer_discord_ids`.
**Gives:** the client's unlocks allow the Developer title.
**Bifrost's server:** `bh_server/protocol.py` `send_perks`.

### 11091 · Back out of level select
Bifrost · both · TCP lobby · Bifrost: `lobby.LEVEL_SELECT_BACK`

A player pressed Back during a custom room's map vote. The server calls the vote off and tells the room, whose clients leave the level select for the character select.

Client → server: no payload.

Server → client:

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | player id | uint | who went back |

**Needs:** a custom room (not a tournament's) with a vote in progress.
**Gives:** the vote ends, the room is no longer started, the player is un-readied; everyone gets 11091 and 2445.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.level_select_back`.

### 11092 · Guild chat history
Bifrost · server → client · TCP lobby · Bifrost: `clans.GUILD_HISTORY`

Recent guild chat lines, sent once per session when a guild member comes online, so the chat isn't empty.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | count | uint | number of lines (client shows at most 100) |
| 1.1 | ago | uint | seconds since the line was said |
| 1.2 | player id | uint | who said it |
| 1.3 | name | string | their name |
| 1.4 | text | string | the line |
| 1.5 | officers | bool | officers-only line |

**Needs:** login or sign-in as a guild member, with chat kept (last 14 days).
**Gives:** the guild chat shows the history.
**Bifrost's server:** `bh_server/clans.py` `history` / `send_history` from `online`.

### 11093 · Web scheme
Bifrost · server → client · TCP lobby · Bifrost: `protocol.WEB_SCHEME`

Tells the client to reach the server's web side over https.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | scheme | string | "https" |

**Needs:** sent after 2431 when the server's `web_scheme` is https.
**Gives:** the client builds web links with https and fetches the tournaments list.
**Bifrost's server:** `bh_server/protocol.py` `on_packet` (login).

### 11094 · Bug report
Bifrost · client → server · TCP lobby · Bifrost: `bugreports.SEND`

Sends a bug report with the client's logs, from the options screen (or the autopilot mod).

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | format | uint | report format |
| 2 | note | string | the player's description |
| 3 | files | string | names of the included log files |
| 4 | size | uint | unpacked text size |
| 5 | length | small | packed size (1 to 60000) |
| 6 | data | raw n | zlib-compressed log text |

**Needs:** connected; at most 5 per session.
**Gives:** the report is stored on the server; answered with 11095.
**Bifrost's server:** `bh_server/bugreports.py` `handle`.

### 11095 · Bug report answer
Bifrost · server → client · TCP lobby · Bifrost: `bugreports.ANSWER`

The result of an 11094.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | report id | uint | the report number, 0 if not kept |
| 2 | message | string | e.g. "Bug report #12 received by the server. Thank you!" |

**Needs:** 11094.
**Gives:** the options screen shows the status.
**Bifrost's server:** `bh_server/bugreports.py` `answer`.

### 11096 · News notice
Bifrost · server → client · TCP lobby · Bifrost: `tournaments.LIST_NOTICE`

Adds an entry to Bifrost's in-game notices list: tournament news (with an "OPEN TOURNAMENTS" button), admin announcements, or server news from the console.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | key | string | `tour:NAME` (tournament), `announce:ID` (announcement) or `server:TIME` (console `notify`) |
| 2 | title | string | e.g. the tournament name, "ANNOUNCEMENT", "SERVER NEWS" |
| 3 | text | string | the news |

**Needs:** logged in.
**Gives:** a notice in the client's list.
**Bifrost's server:** `bh_server/tournaments.py` `list_row` / `list_notice`; `bh_server/moderation.py` `notice_row` (every active announcement at login, and new ones); the console `notify` command in `bh_server/__main__.py`.

### 11097 · Moderation request
Bifrost · client → server · TCP lobby · Bifrost: `moderation.ASK`

An admin action from Bifrost's moderation window.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | action | string | `reports`, `dismiss`, `find`, `announcements`, `announce`, `unannounce`, `mute`, `unmute`, `ban`, `unban` or `resetrating` |
| 2 | args | string | JSON object: e.g. `key`, `minutes`, `reason`, `name`, `id`, `text`, `ladder` |

**Needs:** a server admin account (others are refused).
**Gives:** an 11098.
**Bifrost's server:** `bh_server/moderation.py` `handle` → `run`.

### 11098 · Moderation answer
Bifrost · server → client · TCP lobby · Bifrost: `moderation.ANSWER`

The result of an 11097.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | answer | string | JSON with `action`, `ok`, `message` and the action's data (`reports`, `players`, `announcements`, ...) |

**Needs:** 11097.
**Gives:** the moderation window updates.
**Bifrost's server:** `bh_server/moderation.py` `handle`.

### 11099 · Moderator state
Bifrost · server → client · TCP lobby · Bifrost: `moderation.STATE`

Tells the client its account is a server admin, which enables the moderation window. Sent only when true.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | admin | bool | the account is an admin |

**Needs:** login or sign-in with an account in the server's admin ids.
**Gives:** the client's moderation tools.
**Bifrost's server:** `bh_server/moderation.py` `after_login`.

### Other Bifrost messages

Bifrost messages outside the 11001–11099 numbers: one more lobby packet, and the datagrams Bifrost's P2P matches and relay use instead of the game's UDP match traffic.

#### 11025 · P2P direct choice
Bifrost · client → server · TCP lobby · Bifrost: `lobby.P2P_CHOICE`

The player's answer to the "Direct connection?" question shown for the non-friends listed at the end of 11012: DIRECT shows the two players each other's IP address and is used only if both choose it; Relay keeps the traffic on the server's relay.

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | room | uint | the match room |
| 2 | direct | uint | 1 = DIRECT, 0 = relay |

**Needs:** an 11012 that asked about these players, in that started room.
**Gives:** the choice is recorded for every non-friend in the room; when both players chose DIRECT the server sends a new 11012 with their addresses.
**Bifrost's server:** `bh_server/lobby.py` `Lobby.handle`.

#### P2P match datagrams
Bifrost · peer ↔ peer · UDP (direct, or wrapped in a relay datagram)

During a P2P match the players' clients talk over their own UDP socket (port sent in 11011), not the game's UDP connection. These datagrams are plain big-endian bytes, not bit-packed. Every one starts with this header:

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | magic | u16 | `0xBF02` |
| 2 | type | u8 | 1 PING, 2 PONG, 3 INPUT, 4 BYE, 5 MODS, 6 HELLO_BACK |
| 3 | room | u32 | room number (others are ignored) |
| 4 | sender | u32 | sender's player id |

Bodies by type:

| Type | Body | Meaning |
| --- | --- | --- |
| 1 PING | u32 clock; then, once the match runs, u16 input delay and u16 smoothing ms | measures the round trip on each address and on the relay; the two extra numbers let the peers agree on delay and smoothing |
| 2 PONG | u32 the ping's clock | sent back by the path the ping came (direct or relay); the first direct pong sets the peer's direct address |
| 3 INPUT | u32 confirmed-through time, u32 ack (newest of the receiver's inputs held), u32 sender's match time, u16 n, n × (u32 time ms, u16 input bits); u8 n, n × (u32 time, u32 state hash) for desync checks; u8 bot count, each u32 entity, u16 n, n × (u32 time, u16 bits); in online Training a block marked `0x75` (menu changes), optionally `0x76` (looks) and `0x77` (pause: u32 clock, u32 id, s32 value) | the match's input exchange, resent until acked; drives rollback |
| 4 BYE | none | the sender left the match; the receiver shows "Player left" |
| 5 MODS | u32 mods fingerprint, u16 n, n × (u16 length + UTF-8 entry) | sent up to 12 times, 250 ms apart, at the start; the receiver logs whether content mods differ |
| 6 HELLO_BACK | u32 hand-back time ms, u8 n, n × (u32 player id, u32 confirmed time) | sent by a rejoined player once it has rebuilt the match (11083); the others resume sending it inputs from those times |

#### Relay datagrams
Bifrost · client → server relay · UDP (relay port from 11012) · Bifrost: `relay.REGISTER`, `relay.RELAY`

When two players can't (or chose not to) talk directly, they send P2P datagrams through the server's UDP relay. Big-endian bytes:

| # | Field | Type | Meaning |
| --- | --- | --- | --- |
| 1 | magic | u16 | `0xBF02` |
| 2 | kind | u8 | `0x11` REGISTER or `0x10` RELAY |
| 3 | token high | u32 | from 11012 |
| 4 | token low | u32 | from 11012 |
| 5 | target | u32 | RELAY only: player id to deliver to |
| 6 | datagram | rest | RELAY only: a whole P2P match datagram |

REGISTER (and any datagram with a valid token) records the sender's address for its token. RELAY forwards field 6 alone to the target's registered address if both are in the same started room. Datagrams over 1400 bytes, unknown tokens, and traffic over 400 datagrams or 160 000 bytes a second per token are dropped.

**Bifrost's server:** `bh_server/relay.py` `handle` (in-process) or `bh_server/relayproc.py` `Grants.handle` (separate relay process). The separate process is told which tokens are valid over a local TCP control line (`AUTH secret`, then `GRANT token room player`, `REVOKE token`, `CLOSE room`); the game never sees that channel.
