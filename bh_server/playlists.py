from __future__ import annotations

from dataclasses import dataclass

from .bits import BitWriter
from .matchmaking import START_RATING


@dataclass(frozen=True)
class Playlist:
    name: str
    playlist_id: int
    game_type: str
    display_key: str
    character_select_key: str
    selected_art: str
    ready_art: str
    description_key: str
    game_modes: tuple[str, ...]
    order: int
    match_players: int
    teams: bool = False
    minimum_party: int = 1
    maximum_party: int = 1
    ranked: bool = False
    hero_rating: bool = False
    team_rating: bool = False
    elo_id: int = 0
    placement_games: int = 0
    rematch_key: str | None = None
    test_features: bool = False
    expires: int = 0
    icon: str = "a_PlaylistIcon_Generic"


CASUAL = (
    Playlist("FreeForAll", 1, "FFA", "UI_Casual_FFA", "PlaylistType_FreeForAll_CharSelectHeader",
             "a_GameModeArtSelected_FreeForAll", "a_GameModeArtReady_FreeForAll", "UI_Casual_FFA_Desc",
             ("4FFACasualModern",), 1, 4, maximum_party=2, icon="a_PlaylistIcon_ffa"),
    Playlist("1v1RelayUnranked", 2, "", "UI_Casual_Strikeout",
             "PlaylistType_1v1RelayUnranked_CharSelectHeader", "a_GameModeArtSelected_StrikeOut1v1",
             "a_GameModeArtReady_StrikeOut1v1", "UI_Casual_Strikeout_Desc", ("1v1RelayUnranked",), 2, 2,
             rematch_key="PlaylistType_1v1RelayUnranked_CharSelectHeaderRematch", icon="a_PlaylistIcon_1v1Strikeout"),
    Playlist("2v2Unranked", 3, "", "UI_Casual_Friendly_2",
             "PlaylistType_2v2Unranked_CharSelectHeader", "a_GameModeArtSelected_Unranked2v2",
             "a_GameModeArtReady_Unranked2v2", "UI_Casual_Friendly_2_Desc", ("Unranked2v2",), 3, 4,
             teams=True, maximum_party=2, rematch_key="PlaylistType_2v2Unranked_CharSelectHeaderRematch", icon="a_PlaylistIcon_2v2"),
    Playlist("1v1Experimental", 4, "", "UI_Casual_Experimental",
             "PlaylistType_1v1Experimental_CharSelectHeader", "a_GameModeArtSelected_Experimental1v1",
             "a_GameModeArtReady_Experimental1v1", "UI_Casual_Experimental_Desc",
             ("1v1StockExperimental",), 4, 2,
             rematch_key="PlaylistType_1v1Experimental_CharSelectHeaderRematch", test_features=True, icon="a_PlaylistIcon_Experimental1v1"),
)


RANKED = (
    Playlist("1v1Ranked", 5, "", "PlaylistType_1v1Ranked_DisplayName",
             "PlaylistType_1v1Ranked_CharSelectHeader", "a_GameModeArtSelected_Ranked1v1",
             "a_GameModeArtReady_Ranked1v1", "UI_Ranked_1_Desc", ("1v1StockRanked",), 1, 2,
             ranked=True, hero_rating=True, elo_id=1, placement_games=10,
             rematch_key="PlaylistType_1v1Ranked_CharSelectHeaderRematch", icon="a_PlaylistIcon_1v1"),
    Playlist("2v2Ranked", 6, "", "PlaylistType_2v2Ranked_DisplayName",
             "PlaylistType_2v2Ranked_CharSelectHeader", "a_GameModeArtSelected_Ranked2v2",
             "a_GameModeArtReady_Ranked2v2", "UI_Ranked_2_Desc", ("TeamStockRanked",), 2, 4,
             teams=True, maximum_party=2, ranked=True, team_rating=True, elo_id=2,
             placement_games=10, rematch_key="PlaylistType_2v2Ranked_CharSelectHeaderRematch", icon="a_PlaylistIcon_2v2Ranked"),
    Playlist("Ranked3v3", 7, "RotatingRanked", "PlaylistType_Ranked3v3_DisplayName",
             "PlaylistType_Ranked3v3_CharSelectHeader", "a_GameModeArtSelected_RotatingRanked",
             "a_GameModeArtReady_RotatingRanked", "UI_Rotating_Ranked_Desc", ("Ranked3v3",), 3, 6,
             teams=True, maximum_party=3, ranked=True, team_rating=True, elo_id=3, placement_games=10,
             rematch_key="PlaylistType_Ranked3v3_CharSelectHeaderRematch", icon="a_PlaylistIcon_3v3Ranked"),
)

ALL = CASUAL + RANKED
BY_ID = {p.playlist_id: p for p in ALL}


def write_playlist(w: BitWriter, p: Playlist) -> None:
    w.string(p.name).small(p.playlist_id).small(p.elo_id)
    w.string(p.game_type).string(p.display_key).string(p.character_select_key)
    w.bool(p.rematch_key is not None)
    if p.rematch_key is not None:
        w.string(p.rematch_key)
    w.small(p.order).string(p.icon).small(p.match_players).small(0)
    w.bool(p.teams).small(0).small(p.minimum_party).small(p.maximum_party)
    w.small(len(p.game_modes))
    for mode in p.game_modes:
        w.string(mode)
    w.bool(p.ranked).bool(p.hero_rating).bool(p.team_rating).bool(p.rematch_key is not None).small(p.placement_games)
    w.bool(False).bool(False).bool(p.test_features)
    w.string(p.selected_art).string(p.ready_art).string(p.description_key).string("").string("")
    w.small(0)


def write_list(w: BitWriter, rows: tuple[Playlist, ...]) -> None:
    for p in rows:
        w.bool(True)
        write_playlist(w, p)
        w.uint(p.expires)
    w.bool(False)


def write_catalog(w: BitWriter) -> None:
    write_list(w, CASUAL)
    write_list(w, RANKED)
    w.bool(False)


def write_initial_ratings(w: BitWriter, user_id: int) -> None:
    for p in RANKED:
        if p.elo_id == 0 or p.team_rating:
            continue
        w.bool(True).uint(user_id).small(p.elo_id)
        w.uint(0).uint(0).small(START_RATING).small(0).small(START_RATING)
    w.bool(False)
