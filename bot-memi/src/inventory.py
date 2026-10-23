"""Inventário: caixas e pacotes ganhos (prêmios das caixas), para abrir depois.

O que se compra na loja abre na hora; o que se **ganha** vem para cá e abre com o botão do
`mm!inventario`, sem cobrar nada (`open_crate` e `open_pack` com `from_inventory=True`
tiram o item daqui na mesma transação em que abrem).
"""

from __future__ import annotations

from pathlib import Path

from src.db import DATABASE_PATH, connect

# Item -> (emoji, nome). A ordem é a do inventário.
INVENTORY_ITEMS = {
    "caixa": ("📦", "Caixa"),
    "caixa_epica": ("🟠", "Caixa épica"),
    "pacote": ("🃏", "Pacote de jogador"),
    "pacote_epico": ("🎴", "Pacote épico"),
}


class InventoryEmpty(Exception):
    """Não há esse item no inventário; a mensagem vai para a pessoa."""


def give(connection, user_id: str, item: str, amount: int = 1) -> None:
    """Põe `amount` de `item` no inventário (dentro da transação de quem chama)."""
    if item not in INVENTORY_ITEMS:
        raise ValueError(f"Item de inventário desconhecido: {item}")
    connection.execute(
        """
        INSERT INTO user_inventory (discord_user_id, item, amount) VALUES (?, ?, ?)
        ON CONFLICT(discord_user_id, item) DO UPDATE SET amount = amount + excluded.amount
        """,
        (str(user_id), item, amount),
    )


def take(connection, user_id: str, item: str) -> None:
    """Tira 1 `item` do inventário; erro se não houver (dentro da transação de quem chama)."""
    updated = connection.execute(
        "UPDATE user_inventory SET amount = amount - 1 "
        "WHERE discord_user_id = ? AND item = ? AND amount > 0",
        (str(user_id), item),
    ).rowcount
    if not updated:
        raise InventoryEmpty(f"Você não tem {INVENTORY_ITEMS[item][1].lower()} no inventário.")


def get_inventory(
    discord_user_id: int | str, database_path: str | Path = DATABASE_PATH
) -> dict[str, int]:
    """Quantidade de cada item (só os que a pessoa tem), na ordem de `INVENTORY_ITEMS`."""
    with connect(database_path) as connection:
        amounts = {
            row["item"]: int(row["amount"])
            for row in connection.execute(
                "SELECT item, amount FROM user_inventory WHERE discord_user_id = ? AND amount > 0",
                (str(discord_user_id),),
            )
        }
    return {item: amounts[item] for item in INVENTORY_ITEMS if item in amounts}
