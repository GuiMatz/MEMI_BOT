"""Carrega a Enciclopédia do Futebol: técnicos, lendas, estádios, ligas e mascotes.

Os dados ficam em `src/seeds/enciclopedia.json` (extraídos do PDF enviado pelo dono).
A carga é idempotente e roda na inicialização do bot:

- técnicos, estádios, ligas e mascotes entram nas tabelas do catálogo se ainda não
  existirem com o mesmo nome;
- cada lenda vira uma carta com `overall_source = 'Lenda'` (overall de 90 a 99). Se
  o jogador já existir no banco (mesmo nome e mesma nacionalidade), ele é promovido
  em vez de duplicado.

    python -m src.encyclopedia     # carga manual, mostra o resumo
"""

from __future__ import annotations

import argparse
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.db import DATABASE_PATH, connect, initialize_database, normalize_player_name
from src.ratings import CLUB_LEGEND_SOURCE, LEGEND_MIN_OVERALL, LEGEND_SOURCE

SEED_PATH = Path(__file__).with_name("seeds") / "enciclopedia.json"
SOURCE = "enciclopedia"
LEGEND_CLUB = "Lendas do Futebol"
# Nomes de uma palavra só ("Marcos", "Juan") só promovem um jogador existente se
# ele já for muito bom; abaixo disso a lenda ganha uma carta própria.
SINGLE_NAME_MIN_RAW = 80

# Overall das lendas mais marcantes; as demais ficam com DEFAULT_LEGEND_OVERALL.
LEGEND_OVERALLS = {
    99: ("Pelé", "Diego Maradona", "Lionel Messi", "Cristiano Ronaldo"),
    98: (
        "Alfredo Di Stéfano",
        "Franz Beckenbauer",
        "Zinedine Zidane",
        "Ronaldo Fenômeno",
        "Johan Cruyff",
        "Ferenc Puskás",
    ),
    97: (
        "Paolo Maldini",
        "Michel Platini",
        "Garrincha",
        "Eusébio",
        "Lev Yashin",
        "Ronaldinho Gaúcho",
        "Gerd Müller",
        "Marco van Basten",
        "Zico",
        "Franco Baresi",
        "Xavi Hernández",
        "Andrés Iniesta",
        "George Best",
    ),
    96: (
        "Thierry Henry",
        "Romário",
        "Roberto Baggio",
        "Bobby Charlton",
        "Kaká",
        "Cafu",
        "Roberto Carlos",
        "Gianluigi Buffon",
        "Lothar Matthäus",
        "Andrea Pirlo",
        "Luís Figo",
        "Rivaldo",
        "Karl-Heinz Rummenigge",
        "Zlatan Ibrahimović",
        "Robert Lewandowski",
        "Luka Modrić",
        "Iker Casillas",
        "Manuel Neuer",
        "Fabio Cannavaro",
        "Sócrates",
        "Bobby Moore",
        "Kevin De Bruyne",
        "Neymar",
    ),
    95: (
        "Raúl González",
        "Steven Gerrard",
        "Frank Lampard",
        "Wayne Rooney",
        "Andriy Shevchenko",
        "Gabriel Batistuta",
        "Alessandro Del Piero",
        "Francesco Totti",
        "Clarence Seedorf",
        "Ruud Gullit",
        "Frank Rijkaard",
        "Dennis Bergkamp",
        "Hristo Stoichkov",
        "Roberto Rivellino",
        "Jairzinho",
        "Didi",
        "Nilton Santos",
        "Carlos Alberto Torres",
        "Philipp Lahm",
        "Sergio Ramos",
        "Carles Puyol",
        "Alessandro Nesta",
        "Virgil van Dijk",
        "Oliver Kahn",
        "Dino Zoff",
        "Gordon Banks",
        "Peter Schmeichel",
        "Toni Kroos",
        "Paul Scholes",
        "Mohamed Salah",
        "Harry Kane",
        "Karim Benzema",
        "Luis Suárez",
        "Samuel Eto'o",
        "Didier Drogba",
        "Arjen Robben",
        "Franck Ribéry",
        "Eden Hazard",
        "Juan Román Riquelme",
        "Pavel Nedvěd",
        "Michael Laudrup",
        "Ryan Giggs",
        "Javier Zanetti",
        "Daniel Passarella",
        "Mario Kempes",
        "Elías Figueroa",
        "Gaetano Scirea",
        "Giacinto Facchetti",
        "Sandro Mazzola",
        "Gianni Rivera",
        "Fritz Walter",
        "Uwe Seeler",
        "Kenny Dalglish",
        "Eric Cantona",
        "Paolo Rossi",
    ),
}
DEFAULT_LEGEND_OVERALL = 92

COUNTRIES_EN = {
    "Alemanha": "Germany",
    "Argentina": "Argentina",
    "Argélia": "Algeria",
    "Austrália": "Australia",
    "Brasil": "Brazil",
    "Bulgária": "Bulgaria",
    "Bélgica": "Belgium",
    "Bósnia": "Bosnia-Herzegovina",
    "Camarões": "Cameroon",
    "Chile": "Chile",
    "Colômbia": "Colombia",
    "Coreia do Sul": "Korea, South",
    "Costa Rica": "Costa Rica",
    "Costa do Marfim": "Cote d'Ivoire",
    "Croácia": "Croatia",
    "Dinamarca": "Denmark",
    "Egito": "Egypt",
    "Escócia": "Scotland",
    "Eslovênia": "Slovenia",
    "Espanha": "Spain",
    "Finlândia": "Finland",
    "França": "France",
    "Gana": "Ghana",
    "Grécia": "Greece",
    "Holanda": "Netherlands",
    "Hungria": "Hungary",
    "Inglaterra": "England",
    "Irlanda": "Ireland",
    "Irlanda do Norte": "Northern Ireland",
    "Irã": "Iran",
    "Itália": "Italy",
    "Japão": "Japan",
    "Libéria": "Liberia",
    "Montenegro": "Montenegro",
    "México": "Mexico",
    "Nigéria": "Nigeria",
    "Noruega": "Norway",
    "Paraguai": "Paraguay",
    "País de Gales": "Wales",
    "Peru": "Peru",
    "Polônia": "Poland",
    "Portugal": "Portugal",
    "República Tcheca": "Czech Republic",
    "Romênia": "Romania",
    "Rússia": "Russia",
    "Senegal": "Senegal",
    "Suécia": "Sweden",
    "Suíça": "Switzerland",
    "Sérvia": "Serbia",
    "Turquia": "Türkiye",
    "Ucrânia": "Ukraine",
    "Uruguai": "Uruguay",
    "Áustria": "Austria",
}
# Grafias alternativas que as fontes usam para o mesmo país.
COUNTRY_ALIASES = {
    "Korea, South": {"south korea", "korea republic", "korea south"},
    "Cote d'Ivoire": {"ivory coast", "cote d ivoire"},
    "Türkiye": {"turkey", "turkiye"},
    "Bosnia-Herzegovina": {"bosnia and herzegovina", "bosnia herzegovina"},
    "Czech Republic": {"czechia", "czech republic"},
    "Ireland": {"republic of ireland", "ireland"},
}


@lru_cache(maxsize=1)
def load_encyclopedia() -> dict[str, list[dict[str, str]]]:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


def legend_overall(name: str) -> int:
    for overall, names in LEGEND_OVERALLS.items():
        if name in names:
            return overall
    return DEFAULT_LEGEND_OVERALL


def _country_keys(country_pt: str) -> set[str]:
    english = COUNTRIES_EN.get(country_pt, country_pt)
    keys = {normalize_player_name(english), normalize_player_name(country_pt)}
    keys |= COUNTRY_ALIASES.get(english, set())
    return keys


def _slug(name: str) -> str:
    return normalize_player_name(name).replace(" ", "-")


def _seed_simple(
    connection, table: str, items: list[dict[str, str]], columns: dict[str, str]
) -> int:
    """Insere os itens cujo nome (e dono, nos mascotes) ainda não existe na tabela.

    `columns` liga cada chave do JSON à coluna do banco.
    """
    if table not in {"coaches", "stadiums", "leagues", "mascots"}:
        raise ValueError(f"Tabela de catálogo desconhecida: {table}")
    with_owner = table == "mascots"

    def key(name: str, owner: str | None) -> str:
        base = normalize_player_name(name)
        return f"{base}|{normalize_player_name(owner)}" if with_owner else base

    owner_column = ", owner" if with_owner else ", NULL AS owner"
    existing = {
        key(row["name"], row["owner"])
        for row in connection.execute(f"SELECT name{owner_column} FROM {table}")
    }
    names = ", ".join(columns.values())
    marks = ", ".join("?" for _ in columns)
    added = 0
    for item in items:
        item_key = key(item["name"], item.get("owner"))
        if item_key in existing:
            continue
        connection.execute(
            f"INSERT INTO {table} ({names}) VALUES ({marks})",
            tuple(item[field] for field in columns),
        )
        existing.add(item_key)
        added += 1
    return added


def _seed_legends(connection, legends: list[dict[str, str]]) -> dict[str, int]:
    report = {"legends_created": 0, "legends_promoted": 0}
    linked = {
        row["source_id"]: int(row["player_id"])
        for row in connection.execute(
            "SELECT source_id, player_id FROM player_sources WHERE source = ?", (SOURCE,)
        )
    }
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in connection.execute("""
        SELECT id, name, nationality, overall, overall_source
        FROM players WHERE COALESCE(overall_source, '') NOT IN ('Cartola', 'Lenda')
        """):
        by_name.setdefault(normalize_player_name(row["name"]), []).append(dict(row))

    for legend in legends:
        slug = _slug(legend["name"])
        overall = max(LEGEND_MIN_OVERALL, legend_overall(legend["name"]))
        player_id = linked.get(slug)
        if player_id is None:
            key = normalize_player_name(legend["name"])
            countries = _country_keys(legend["country"])
            candidates = [
                player
                for player in by_name.get(key, [])
                if normalize_player_name(player["nationality"]) in countries
            ]
            if " " not in key:
                candidates = [
                    player
                    for player in candidates
                    if (player["overall"] or 0) >= SINGLE_NAME_MIN_RAW
                ]
            if len(candidates) == 1:
                player_id = int(candidates[0]["id"])
                report["legends_promoted"] += 1
            else:
                homonym = bool(by_name.get(key))
                player_id = connection.execute(
                    """
                    INSERT INTO players (
                        name, display_name, nationality, club_name, game_position,
                        overall, overall_source, activity_status, phase2_candidate
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 0)
                    """,
                    (
                        legend["name"],
                        f"{legend['name']} (Lenda)" if homonym else legend["name"],
                        COUNTRIES_EN.get(legend["country"], legend["country"]),
                        LEGEND_CLUB,
                        legend["position"],
                        overall,
                        LEGEND_SOURCE,
                    ),
                ).lastrowid
                report["legends_created"] += 1
            connection.execute(
                """
                INSERT OR IGNORE INTO player_sources (source, source_id, player_id, source_name)
                VALUES (?, ?, ?, ?)
                """,
                (SOURCE, slug, player_id, legend["name"]),
            )
        # Reaplicado a cada carga: reimportações do EA não rebaixam a lenda.
        connection.execute(
            """
            UPDATE players SET overall = MAX(COALESCE(overall, 0), ?),
                overall_source = ?, activity_status = 'active', phase2_candidate = 0,
                game_position = COALESCE(game_position, ?)
            WHERE id = ?
            """,
            (overall, LEGEND_SOURCE, legend["position"], player_id),
        )
    return report


def seed_encyclopedia(database_path: str | Path = DATABASE_PATH) -> dict[str, int]:
    data = load_encyclopedia()
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        report = {
            "coaches": _seed_simple(
                connection, "coaches", data["coaches"], {"name": "name", "country": "nationality"}
            ),
            "stadiums": _seed_simple(
                connection,
                "stadiums",
                data["stadiums"],
                {"name": "name", "city": "city", "country": "country"},
            ),
            "leagues": _seed_simple(
                connection, "leagues", data["leagues"], {"name": "name", "country": "country"}
            ),
            "mascots": _seed_simple(
                connection,
                "mascots",
                data["mascots"],
                {"name": "name", "owner": "owner", "country": "country"},
            ),
        }
        report.update(_seed_legends(connection, data["legends"]))
    return report


CLUB_LEGENDS_PATH = Path(__file__).with_name("seeds") / "lendas_brasileiras.json"
CLUB_LEGENDS_SOURCE = "lendas_br"
# Siglas do documento das lendas para as posições do jogo.
CLUB_LEGEND_POSITIONS = {"GL": "GOL", "ATA": "CA"}


@lru_cache(maxsize=1)
def load_club_legends() -> list[dict[str, Any]]:
    return json.loads(CLUB_LEGENDS_PATH.read_text(encoding="utf-8"))["clubs"]


def seed_club_legends(database_path: str | Path = DATABASE_PATH) -> dict[str, int]:
    """Carrega as 11 lendas de cada um dos 12 grandes clubes brasileiros, sem duplicar.

    Antes de criar uma carta, procura a mesma pessoa no banco:
    - lenda da Enciclopédia com o mesmo nome (ou apelido listado) e o mesmo país: já
      existe, então fica como está (com o overall de lenda dela);
    - jogador aposentado (inativo) com o mesmo nome e país: vira a carta da lenda;
    - homônimo de outra pessoa: a carta nova ganha o clube no nome, ex. `Pepe (Santos)`.
    O ID de cada carta nova é o próximo livre da tabela (chave primária do SQLite).
    """
    report = {"club_legends_created": 0, "club_legends_promoted": 0, "club_legends_existing": 0}
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        linked = {
            row["source_id"]: int(row["player_id"])
            for row in connection.execute(
                "SELECT source_id, player_id FROM player_sources WHERE source = ?",
                (CLUB_LEGENDS_SOURCE,),
            )
        }
        by_name: dict[str, list[dict[str, Any]]] = {}
        for row in connection.execute(
            "SELECT id, name, nationality, overall, overall_source, activity_status FROM players"
        ):
            by_name.setdefault(normalize_player_name(row["name"]), []).append(dict(row))
        for club in load_club_legends():
            club_name = club["club"]
            for legend in club["players"]:
                slug = f"{_slug(club_name)}:{_slug(legend['name'])}"
                position = CLUB_LEGEND_POSITIONS.get(legend["position"], legend["position"])
                countries = _country_keys(legend.get("country", "Brasil"))
                keys = {
                    normalize_player_name(name)
                    for name in [legend["name"], *legend.get("aliases", [])]
                }
                same_person = [
                    player
                    for key in keys
                    for player in by_name.get(key, [])
                    if normalize_player_name(player["nationality"]) in countries
                    and int(player["id"]) != linked.get(slug)
                ]
                player_id = linked.get(slug)
                if player_id is None:
                    if any(player["overall_source"] == LEGEND_SOURCE for player in same_person):
                        report["club_legends_existing"] += 1
                        continue
                    retired = [
                        player
                        for player in same_person
                        if player["activity_status"] == "inactive"
                        and (
                            " " in legend["name"] or (player["overall"] or 0) >= SINGLE_NAME_MIN_RAW
                        )
                    ]
                    if len(retired) == 1:
                        player_id = int(retired[0]["id"])
                        report["club_legends_promoted"] += 1
                    else:
                        homonym = bool(by_name.get(normalize_player_name(legend["name"])))
                        country = legend.get("country", "Brasil")
                        player_id = connection.execute(
                            """
                            INSERT INTO players (name, display_name, nationality)
                            VALUES (?, ?, ?)
                            """,
                            (
                                legend["name"],
                                f"{legend['name']} ({club_name})" if homonym else legend["name"],
                                COUNTRIES_EN.get(country, country),
                            ),
                        ).lastrowid
                        by_name.setdefault(normalize_player_name(legend["name"]), []).append(
                            {
                                "id": player_id,
                                "name": legend["name"],
                                "nationality": COUNTRIES_EN.get(country, country),
                                "overall": legend["overall"],
                                "overall_source": CLUB_LEGEND_SOURCE,
                                "activity_status": "active",
                            }
                        )
                        report["club_legends_created"] += 1
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO player_sources
                            (source, source_id, player_id, source_name, source_club_name,
                             source_position)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            CLUB_LEGENDS_SOURCE,
                            slug,
                            player_id,
                            legend["name"],
                            club_name,
                            legend["position"],
                        ),
                    )
                connection.execute(
                    """
                    UPDATE players SET overall = ?, overall_source = ?, club_name = ?,
                        game_position = ?, activity_status = 'active', phase2_candidate = 0,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        legend["overall"],
                        CLUB_LEGEND_SOURCE,
                        f"Lendas do {club_name}",
                        position,
                        player_id,
                    ),
                )
    return report


CARTOLA_EXTRA_PATH = Path(__file__).with_name("seeds") / "cartola_extra.json"


def seed_cartola_extra(database_path: str | Path = DATABASE_PATH) -> int:
    """Cartas extras da categoria Cartola; quem já existe (mesmo nome, Cartola) não repete."""
    players = json.loads(CARTOLA_EXTRA_PATH.read_text(encoding="utf-8"))["players"]
    added = 0
    with connect(database_path) as connection:
        existing = {
            normalize_player_name(row["name"])
            for row in connection.execute(
                "SELECT name FROM players WHERE overall_source = 'Cartola'"
            )
        }
        for player in players:
            if normalize_player_name(player["name"]) in existing:
                continue
            connection.execute(
                """
                INSERT INTO players (
                    name, display_name, club_name, game_position, overall, overall_source,
                    activity_status, phase2_candidate
                ) VALUES (?, ?, ?, ?, 90, 'Cartola', 'active', 0)
                """,
                (player["name"], player["name"], player["club"], player["position"]),
            )
            existing.add(normalize_player_name(player["name"]))
            added += 1
    return added


def prepare_game_data(database_path: str | Path = DATABASE_PATH) -> dict[str, int]:
    """Tudo que o bot sincroniza ao iniciar: enciclopédia, lendas, escudos e fotos."""
    from src.cosmetics import grandfather_equipped
    from src.game import sync_club_logos
    from src.photos import sync_existing_photos

    report = seed_encyclopedia(database_path)
    report.update(seed_club_legends(database_path))
    report["cartola_extra"] = seed_cartola_extra(database_path)
    report["cosmetics_grandfathered"] = grandfather_equipped(database_path)
    logos = sync_club_logos(database_path)
    report["club_logos_linked"] = logos["linked"]
    report["clubs_created_from_logos"] = logos["created"]
    report["photos_linked"] = sync_existing_photos(database_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args()
    initialize_database(args.database)
    print(prepare_game_data(args.database))


if __name__ == "__main__":
    main()
