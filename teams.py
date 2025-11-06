# teams.py — базовые цвета и удобные геттеры
from typing import Dict, Tuple, Optional

# минимальный набор (primary). Остальное вычислим в graphics по shade.
TEAMS: Dict[str, Dict[str, str]] = {
    "1610612737": {"name": "Atlanta Hawks",        "abbr":"ATL", "primary":"#E03A3E"},
    "1610612738": {"name": "Boston Celtics",       "abbr":"BOS", "primary":"#007A33"},
    "1610612751": {"name": "Brooklyn Nets",        "abbr":"BKN", "primary":"#000000"},
    "1610612766": {"name": "Charlotte Hornets",    "abbr":"CHA", "primary":"#1D1160"},
    "1610612741": {"name": "Chicago Bulls",        "abbr":"CHI", "primary":"#CE1141"},
    "1610612739": {"name": "Cleveland Cavaliers",  "abbr":"CLE", "primary":"#860038"},
    "1610612742": {"name": "Dallas Mavericks",     "abbr":"DAL", "primary":"#00538C"},
    "1610612743": {"name": "Denver Nuggets",       "abbr":"DEN", "primary":"#0E2240"},
    "1610612765": {"name": "Detroit Pistons",      "abbr":"DET", "primary":"#C8102E"},
    "1610612744": {"name": "Golden State Warriors","abbr":"GSW", "primary":"#1D428A"},
    "1610612745": {"name": "Houston Rockets",      "abbr":"HOU", "primary":"#CE1141"},
    "1610612754": {"name": "Indiana Pacers",       "abbr":"IND", "primary":"#002D62"},
    "1610612746": {"name": "LA Clippers",          "abbr":"LAC", "primary":"#C8102E"},
    "1610612747": {"name": "Los Angeles Lakers",   "abbr":"LAL", "primary":"#552583"},
    "1610612763": {"name": "Memphis Grizzlies",    "abbr":"MEM", "primary":"#5D76A9"},
    "1610612748": {"name": "Miami Heat",           "abbr":"MIA", "primary":"#98002E"},
    "1610612749": {"name": "Milwaukee Bucks",      "abbr":"MIL", "primary":"#00471B"},
    "1610612750": {"name": "Minnesota Timberwolves","abbr":"MIN","primary":"#0C2340"},
    "1610612740": {"name": "New Orleans Pelicans", "abbr":"NOP", "primary":"#0C2340"},
    "1610612752": {"name": "New York Knicks",      "abbr":"NYK", "primary":"#006BB6"},
    "1610612760": {"name": "Oklahoma City Thunder","abbr":"OKC", "primary":"#007AC1"},
    "1610612753": {"name": "Orlando Magic",        "abbr":"ORL", "primary":"#0077C0"},
    "1610612755": {"name": "Philadelphia 76ers",   "abbr":"PHI", "primary":"#006BB6"},
    "1610612756": {"name": "Phoenix Suns",         "abbr":"PHX", "primary":"#1D1160"},
    "1610612757": {"name": "Portland Trail Blazers","abbr":"POR","primary":"#E03A3E"},
    "1610612758": {"name": "Sacramento Kings",     "abbr":"SAC", "primary":"#5A2D81"},
    "1610612759": {"name": "San Antonio Spurs",    "abbr":"SAS", "primary":"#000000"},
    "1610612761": {"name": "Toronto Raptors",      "abbr":"TOR", "primary":"#CE1141"},
    "1610612762": {"name": "Utah Jazz",            "abbr":"UTA", "primary":"#002B5C"},
    "1610612764": {"name": "Washington Wizards",   "abbr":"WAS", "primary":"#002B5C"},
    # исторические/другие — по мере надобности
}

def team_name(team_id: str) -> str:
    t = TEAMS.get(str(team_id))
    return (t or {}).get("name", "Free Agent")

def team_primary_color(team_id: str) -> str:
    t = TEAMS.get(str(team_id))
    return (t or {}).get("primary", "#1a1a1a")
