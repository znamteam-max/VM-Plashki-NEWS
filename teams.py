# teams.py
from __future__ import annotations
from typing import Dict, Any

# Базовая карта команд NBA: teamId -> данные бренда
# Цвета: primary (официальный), dark/light будут вычислены в data.get_team_brand при необходимости
TEAMS: Dict[str, Dict[str, Any]] = {
    "1610612737": {"name": "Atlanta Hawks",         "abbr": "atl", "espn": "atl", "primary": "#E03A3E"},
    "1610612738": {"name": "Boston Celtics",        "abbr": "bos", "espn": "bos", "primary": "#007A33"},
    "1610612751": {"name": "Brooklyn Nets",         "abbr": "bkn", "espn": "bkn", "primary": "#000000"},
    "1610612766": {"name": "Charlotte Hornets",     "abbr": "cha", "espn": "cha", "primary": "#1D1160"},
    "1610612741": {"name": "Chicago Bulls",         "abbr": "chi", "espn": "chi", "primary": "#CE1141"},
    "1610612739": {"name": "Cleveland Cavaliers",   "abbr": "cle", "espn": "cle", "primary": "#860038"},
    "1610612742": {"name": "Dallas Mavericks",      "abbr": "dal", "espn": "dal", "primary": "#00538C"},
    "1610612743": {"name": "Denver Nuggets",        "abbr": "den", "espn": "den", "primary": "#0E2240"},
    "1610612765": {"name": "Detroit Pistons",       "abbr": "det", "espn": "det", "primary": "#C8102E"},
    "1610612744": {"name": "Golden State Warriors", "abbr": "gs",  "espn": "gs",  "primary": "#1D428A"},
    "1610612745": {"name": "Houston Rockets",       "abbr": "hou", "espn": "hou", "primary": "#CE1141"},
    "1610612754": {"name": "Indiana Pacers",        "abbr": "ind", "espn": "ind", "primary": "#002D62"},
    "1610612746": {"name": "LA Clippers",           "abbr": "lac", "espn": "lac", "primary": "#C8102E"},
    "1610612747": {"name": "Los Angeles Lakers",    "abbr": "lal", "espn": "lal", "primary": "#552583"},
    "1610612763": {"name": "Memphis Grizzlies",     "abbr": "mem", "espn": "mem", "primary": "#5D76A9"},
    "1610612748": {"name": "Miami Heat",            "abbr": "mia", "espn": "mia", "primary": "#98002E"},
    "1610612749": {"name": "Milwaukee Bucks",       "abbr": "mil", "espn": "mil", "primary": "#00471B"},
    "1610612750": {"name": "Minnesota Timberwolves","abbr": "min", "espn": "min", "primary": "#0C2340"},
    "1610612752": {"name": "New York Knicks",       "abbr": "ny",  "espn": "ny",  "primary": "#F58426"},
    "1610612753": {"name": "Orlando Magic",         "abbr": "orl", "espn": "orl", "primary": "#0077C0"},
    "1610612755": {"name": "Philadelphia 76ers",    "abbr": "phi", "espn": "phi", "primary": "#006BB6"},
    "1610612756": {"name": "Phoenix Suns",          "abbr": "phx", "espn": "phx", "primary": "#1D1160"},
    "1610612757": {"name": "Portland Trail Blazers","abbr": "por", "espn": "por", "primary": "#E03A3E"},
    "1610612758": {"name": "Sacramento Kings",      "abbr": "sac", "espn": "sac", "primary": "#5A2D81"},
    "1610612759": {"name": "San Antonio Spurs",     "abbr": "sa",  "espn": "sa",  "primary": "#000000"},
    "1610612760": {"name": "Oklahoma City Thunder", "abbr": "okc", "espn": "okc", "primary": "#007AC1"},
    "1610612761": {"name": "Toronto Raptors",       "abbr": "tor", "espn": "tor", "primary": "#CE1141"},
    "1610612762": {"name": "Utah Jazz",             "abbr": "uta", "espn": "uta", "primary": "#002B5C"},
    "1610612764": {"name": "Washington Wizards",    "abbr": "wsh", "espn": "wsh", "primary": "#002B5C"},
    "1610612740": {"name": "New Orleans Pelicans",  "abbr": "no",  "espn": "no",  "primary": "#0C2340"},
}

GENERIC_NBA_PRIMARY = "#1D428A"  # синий NBA по умолчанию
