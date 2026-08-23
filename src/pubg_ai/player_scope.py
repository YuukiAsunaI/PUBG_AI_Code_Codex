from __future__ import annotations


PLAYER_GUILD_SCOPE_CONDITION = """
EXISTS (
    SELECT 1
    FROM player_discord_registrations AS player_scope_registration
    WHERE player_scope_registration.registered_player_id = registered_players.id
      AND player_scope_registration.guild_id = %s
      AND player_scope_registration.active = 1
)
""".strip()
