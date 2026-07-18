from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
import re

from pubg_ai.config import DatabaseConfig


SCHEMA_VERSION = 14


class DatabaseError(RuntimeError):
    """Raised when the MySQL database cannot be initialized or used."""


@dataclass(frozen=True)
class SchemaInitializationResult:
    database: str
    schema_version: int
    applied_statements: int

    def to_record(self) -> dict[str, Any]:
        return {
            "database": self.database,
            "schema_version": self.schema_version,
            "applied_statements": self.applied_statements,
        }


def connect_mysql(config: DatabaseConfig, *, include_database: bool = True) -> Any:
    try:
        import pymysql
    except ImportError as exc:
        raise DatabaseError("pymysql is required for MySQL access.") from exc

    kwargs: dict[str, Any] = {
        "host": config.host,
        "port": config.port,
        "user": config.user,
        "password": config.password,
        "charset": config.charset,
        "autocommit": True,
        "cursorclass": pymysql.cursors.DictCursor,
        "connect_timeout": 10,
    }
    if include_database:
        kwargs["database"] = config.database
    return pymysql.connect(**kwargs)


def initialize_database(config: DatabaseConfig) -> SchemaInitializationResult:
    database = _validate_identifier(config.database, "database")

    connection = connect_mysql(config, include_database=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        connection.close()

    connection = connect_mysql(config, include_database=True)
    applied = 0
    try:
        with connection.cursor() as cursor:
            for statement in schema_statements():
                cursor.execute(statement)
                applied += 1
            cursor.execute(
                """
                INSERT INTO schema_migrations (version, description, applied_at_kst)
                VALUES (%s, %s, NOW(6))
                ON DUPLICATE KEY UPDATE description = VALUES(description)
                """,
                (SCHEMA_VERSION, "read-only deletion backup verification audit schema"),
            )
            applied += 1
    finally:
        connection.close()

    return SchemaInitializationResult(
        database=config.database,
        schema_version=SCHEMA_VERSION,
        applied_statements=applied,
    )


def schema_statements() -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INT NOT NULL PRIMARY KEY,
            description VARCHAR(255) NOT NULL,
            applied_at_kst DATETIME(6) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS registered_players (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            account_id VARCHAR(128) NOT NULL,
            shard VARCHAR(32) NOT NULL,
            current_name VARCHAR(191) NOT NULL,
            active TINYINT(1) NOT NULL DEFAULT 1,
            public_profile TINYINT(1) NOT NULL DEFAULT 1,
            registered_by_discord_user_id VARCHAR(32) NULL,
            registered_guild_id VARCHAR(32) NULL,
            registered_channel_id VARCHAR(32) NULL,
            created_at_kst DATETIME(6) NOT NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_registered_players_account_shard (account_id, shard),
            KEY idx_registered_players_name_shard (current_name, shard),
            KEY idx_registered_players_active (active)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS player_aliases (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            registered_player_id BIGINT UNSIGNED NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            shard VARCHAR(32) NOT NULL,
            name VARCHAR(191) NOT NULL,
            source VARCHAR(64) NOT NULL,
            first_seen_at_kst DATETIME(6) NOT NULL,
            last_seen_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_player_aliases_account_name (account_id, shard, name),
            KEY idx_player_aliases_registered_player (registered_player_id),
            CONSTRAINT fk_player_aliases_registered_player
                FOREIGN KEY (registered_player_id) REFERENCES registered_players(id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS discord_guilds (
            guild_id VARCHAR(32) NOT NULL PRIMARY KEY,
            name VARCHAR(191) NULL,
            ranking_scope ENUM('guild', 'global') NOT NULL DEFAULT 'guild',
            public_profile_default TINYINT(1) NOT NULL DEFAULT 1,
            created_at_kst DATETIME(6) NOT NULL,
            updated_at_kst DATETIME(6) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS discord_users (
            user_id VARCHAR(32) NOT NULL PRIMARY KEY,
            display_name VARCHAR(191) NULL,
            created_at_kst DATETIME(6) NOT NULL,
            updated_at_kst DATETIME(6) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS discord_permission_grants (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            guild_id VARCHAR(32) NULL,
            user_id VARCHAR(32) NOT NULL,
            command_group VARCHAR(64) NOT NULL,
            allowed TINYINT(1) NOT NULL DEFAULT 1,
            created_at_kst DATETIME(6) NOT NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_discord_permission_grants (guild_id, user_id, command_group),
            KEY idx_discord_permission_user (user_id, guild_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS global_admins (
            user_id VARCHAR(32) NOT NULL PRIMARY KEY,
            created_at_kst DATETIME(6) NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS api_fetch_jobs (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            job_type VARCHAR(32) NOT NULL,
            shard VARCHAR(32) NULL,
            target_id VARCHAR(191) NULL,
            status ENUM('queued', 'running', 'succeeded', 'failed') NOT NULL DEFAULT 'queued',
            attempts INT UNSIGNED NOT NULL DEFAULT 0,
            next_run_at_kst DATETIME(6) NULL,
            last_error TEXT NULL,
            rate_limit_limit INT NULL,
            rate_limit_remaining INT NULL,
            rate_limit_reset_epoch BIGINT NULL,
            created_at_kst DATETIME(6) NOT NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            KEY idx_api_fetch_jobs_status_next_run (status, next_run_at_kst),
            KEY idx_api_fetch_jobs_target (job_type, shard, target_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS worker_run_history (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            worker_name VARCHAR(64) NOT NULL,
            status ENUM('succeeded', 'failed') NOT NULL,
            started_at_kst DATETIME(6) NULL,
            finished_at_kst DATETIME(6) NULL,
            duration_seconds FLOAT NULL,
            error_count INT UNSIGNED NOT NULL DEFAULT 0,
            last_error TEXT NULL,
            summary_json JSON NOT NULL,
            created_at_kst DATETIME(6) NOT NULL,
            KEY idx_worker_run_history_worker_time (worker_name, created_at_kst),
            KEY idx_worker_run_history_status_time (status, created_at_kst)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS system_alert_history (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            alert_key_hash CHAR(64) NOT NULL,
            alert_key TEXT NOT NULL,
            source VARCHAR(32) NOT NULL,
            severity VARCHAR(32) NOT NULL,
            title VARCHAR(191) NOT NULL,
            message TEXT NOT NULL,
            metadata_json JSON NOT NULL,
            first_seen_at_kst DATETIME(6) NOT NULL,
            last_seen_at_kst DATETIME(6) NOT NULL,
            last_notified_at_kst DATETIME(6) NULL,
            acknowledged_at_kst DATETIME(6) NULL,
            snoozed_until_kst DATETIME(6) NULL,
            resolved_at_kst DATETIME(6) NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_system_alert_history_key_hash (alert_key_hash),
            KEY idx_system_alert_history_source_time (source, last_seen_at_kst),
            KEY idx_system_alert_history_resolved_time (resolved_at_kst, last_seen_at_kst),
            KEY idx_system_alert_history_snooze (snoozed_until_kst)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS system_alert_notes (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            alert_history_id BIGINT UNSIGNED NOT NULL,
            note_type ENUM('note', 'resolution') NOT NULL DEFAULT 'note',
            note_text TEXT NOT NULL,
            created_by VARCHAR(191) NULL,
            created_at_kst DATETIME(6) NOT NULL,
            KEY idx_system_alert_notes_alert_time (alert_history_id, created_at_kst),
            CONSTRAINT fk_system_alert_notes_alert_history
                FOREIGN KEY (alert_history_id) REFERENCES system_alert_history(id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS data_deletion_requests (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            registered_player_id BIGINT UNSIGNED NULL,
            account_id VARCHAR(128) NOT NULL,
            shard VARCHAR(32) NOT NULL,
            player_name VARCHAR(191) NOT NULL,
            deletion_scope ENUM('registration', 'normalized', 'raw', 'replay', 'all') NOT NULL,
            status ENUM('pending', 'approved', 'rejected', 'cancelled', 'expired', 'executed', 'failed')
                NOT NULL DEFAULT 'pending',
            reason VARCHAR(500) NULL,
            requested_by_discord_user_id VARCHAR(32) NOT NULL,
            requested_guild_id VARCHAR(32) NULL,
            requested_channel_id VARCHAR(32) NULL,
            requested_at_kst DATETIME(6) NOT NULL,
            expires_at_kst DATETIME(6) NOT NULL,
            reviewed_by VARCHAR(191) NULL,
            reviewed_at_kst DATETIME(6) NULL,
            review_note VARCHAR(1000) NULL,
            executed_at_kst DATETIME(6) NULL,
            execution_summary_json JSON NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            KEY idx_data_deletion_status_expiry (status, expires_at_kst),
            KEY idx_data_deletion_player_scope (account_id, shard, deletion_scope, status),
            KEY idx_data_deletion_requester (requested_by_discord_user_id, requested_at_kst),
            CONSTRAINT fk_data_deletion_registered_player
                FOREIGN KEY (registered_player_id) REFERENCES registered_players(id)
                ON DELETE SET NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS data_deletion_request_events (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            request_id BIGINT UNSIGNED NOT NULL,
            event_type ENUM('requested', 'approved', 'rejected', 'cancelled', 'expired', 'executed', 'failed')
                NOT NULL,
            actor_type ENUM('discord', 'local', 'system') NOT NULL,
            actor_id VARCHAR(191) NOT NULL,
            note VARCHAR(1000) NULL,
            details_json JSON NULL,
            created_at_kst DATETIME(6) NOT NULL,
            KEY idx_data_deletion_events_request_time (request_id, created_at_kst),
            CONSTRAINT fk_data_deletion_events_request
                FOREIGN KEY (request_id) REFERENCES data_deletion_requests(id)
                ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS data_deletion_preview_snapshots (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            request_id BIGINT UNSIGNED NOT NULL,
            contract_version VARCHAR(64) NOT NULL,
            fingerprint_sha256 CHAR(64) NOT NULL,
            preview_json JSON NOT NULL,
            manifest_json JSON NOT NULL,
            catalog_complete TINYINT(1) NOT NULL,
            filesystem_issue_count INT UNSIGNED NOT NULL,
            candidate_row_count BIGINT UNSIGNED NOT NULL,
            candidate_file_count BIGINT UNSIGNED NOT NULL,
            captured_by VARCHAR(191) NOT NULL,
            capture_note VARCHAR(1000) NULL,
            captured_at_kst DATETIME(6) NOT NULL,
            KEY idx_data_deletion_snapshots_request_time (request_id, captured_at_kst),
            KEY idx_data_deletion_snapshots_fingerprint (fingerprint_sha256),
            UNIQUE KEY uq_data_deletion_snapshot_contract (id, request_id, fingerprint_sha256),
            CONSTRAINT fk_data_deletion_snapshot_request
                FOREIGN KEY (request_id) REFERENCES data_deletion_requests(id)
                ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS data_deletion_confirmations (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            request_id BIGINT UNSIGNED NOT NULL,
            preview_snapshot_id BIGINT UNSIGNED NOT NULL,
            contract_version VARCHAR(64) NOT NULL,
            fingerprint_sha256 CHAR(64) NOT NULL,
            confirmed_by VARCHAR(191) NOT NULL,
            confirmation_text_sha256 CHAR(64) NOT NULL,
            confirmation_note VARCHAR(1000) NULL,
            confirmed_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_data_deletion_confirmation_snapshot (request_id, preview_snapshot_id),
            KEY idx_data_deletion_confirmations_request_time (request_id, confirmed_at_kst),
            KEY idx_data_deletion_confirmation_contract (
                preview_snapshot_id,
                request_id,
                fingerprint_sha256
            ),
            CONSTRAINT fk_data_deletion_confirmation_request
                FOREIGN KEY (request_id) REFERENCES data_deletion_requests(id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_data_deletion_confirmation_snapshot
                FOREIGN KEY (preview_snapshot_id, request_id, fingerprint_sha256)
                REFERENCES data_deletion_preview_snapshots (id, request_id, fingerprint_sha256)
                ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS data_deletion_dry_run_plans (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            request_id BIGINT UNSIGNED NOT NULL,
            preview_snapshot_id BIGINT UNSIGNED NOT NULL,
            confirmation_id BIGINT UNSIGNED NOT NULL,
            contract_version VARCHAR(64) NOT NULL,
            source_fingerprint_sha256 CHAR(64) NOT NULL,
            plan_fingerprint_sha256 CHAR(64) NOT NULL,
            plan_json JSON NOT NULL,
            operation_count INT UNSIGNED NOT NULL,
            candidate_row_count BIGINT UNSIGNED NOT NULL,
            candidate_file_count BIGINT UNSIGNED NOT NULL,
            candidate_file_bytes BIGINT UNSIGNED NOT NULL,
            excluded_row_count BIGINT UNSIGNED NOT NULL,
            excluded_file_count BIGINT UNSIGNED NOT NULL,
            generated_by VARCHAR(191) NOT NULL,
            generation_note VARCHAR(1000) NULL,
            generated_at_kst DATETIME(6) NOT NULL,
            KEY idx_data_deletion_dry_run_request_time (request_id, generated_at_kst),
            KEY idx_data_deletion_dry_run_source (preview_snapshot_id, source_fingerprint_sha256),
            KEY idx_data_deletion_dry_run_plan_fingerprint (plan_fingerprint_sha256),
            KEY idx_data_deletion_dry_run_confirmation (confirmation_id),
            CONSTRAINT fk_data_deletion_dry_run_request
                FOREIGN KEY (request_id) REFERENCES data_deletion_requests(id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_data_deletion_dry_run_snapshot
                FOREIGN KEY (preview_snapshot_id, request_id, source_fingerprint_sha256)
                REFERENCES data_deletion_preview_snapshots (id, request_id, fingerprint_sha256)
                ON DELETE RESTRICT,
            CONSTRAINT fk_data_deletion_dry_run_confirmation
                FOREIGN KEY (confirmation_id) REFERENCES data_deletion_confirmations(id)
                ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS data_deletion_backup_evidence (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            request_id BIGINT UNSIGNED NOT NULL,
            dry_run_plan_id BIGINT UNSIGNED NOT NULL,
            contract_version VARCHAR(64) NOT NULL,
            plan_fingerprint_sha256 CHAR(64) NOT NULL,
            prerequisite_key ENUM(
                'mysql_target_backup',
                'replay_artifact_backup',
                'quarantine_capacity_check',
                'backup_integrity_verification'
            ) NOT NULL,
            evidence_fingerprint_sha256 CHAR(64) NOT NULL,
            evidence_json JSON NOT NULL,
            recorded_by VARCHAR(191) NOT NULL,
            evidence_note VARCHAR(1000) NULL,
            recorded_at_kst DATETIME(6) NOT NULL,
            KEY idx_data_deletion_backup_plan_key_time (
                dry_run_plan_id,
                prerequisite_key,
                recorded_at_kst
            ),
            KEY idx_data_deletion_backup_request_time (request_id, recorded_at_kst),
            KEY idx_data_deletion_backup_evidence_fingerprint (evidence_fingerprint_sha256),
            CONSTRAINT fk_data_deletion_backup_request
                FOREIGN KEY (request_id) REFERENCES data_deletion_requests(id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_data_deletion_backup_plan
                FOREIGN KEY (dry_run_plan_id) REFERENCES data_deletion_dry_run_plans(id)
                ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS data_deletion_rehearsal_runs (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            request_id BIGINT UNSIGNED NOT NULL,
            dry_run_plan_id BIGINT UNSIGNED NOT NULL,
            contract_version VARCHAR(64) NOT NULL,
            plan_fingerprint_sha256 CHAR(64) NOT NULL,
            evidence_set_fingerprint_sha256 CHAR(64) NOT NULL,
            result_fingerprint_sha256 CHAR(64) NOT NULL,
            result_status ENUM('passed', 'blocked') NOT NULL,
            result_json JSON NOT NULL,
            check_count INT UNSIGNED NOT NULL,
            passed_check_count INT UNSIGNED NOT NULL,
            blocker_count INT UNSIGNED NOT NULL,
            run_by VARCHAR(191) NOT NULL,
            rehearsal_note VARCHAR(1000) NULL,
            run_at_kst DATETIME(6) NOT NULL,
            KEY idx_data_deletion_rehearsal_plan_time (dry_run_plan_id, run_at_kst),
            KEY idx_data_deletion_rehearsal_request_time (request_id, run_at_kst),
            KEY idx_data_deletion_rehearsal_result (result_status, run_at_kst),
            KEY idx_data_deletion_rehearsal_fingerprint (result_fingerprint_sha256),
            CONSTRAINT fk_data_deletion_rehearsal_request
                FOREIGN KEY (request_id) REFERENCES data_deletion_requests(id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_data_deletion_rehearsal_plan
                FOREIGN KEY (dry_run_plan_id) REFERENCES data_deletion_dry_run_plans(id)
                ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS data_deletion_backup_verification_runs (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            request_id BIGINT UNSIGNED NOT NULL,
            dry_run_plan_id BIGINT UNSIGNED NOT NULL,
            contract_version VARCHAR(64) NOT NULL,
            plan_fingerprint_sha256 CHAR(64) NOT NULL,
            evidence_set_fingerprint_sha256 CHAR(64) NOT NULL,
            evidence_record_ids_json JSON NOT NULL,
            build_id VARCHAR(64) NULL,
            manifest_path VARCHAR(1000) NOT NULL,
            expected_manifest_sha256 CHAR(64) NOT NULL,
            observed_manifest_sha256 CHAR(64) NULL,
            manifest_fingerprint_sha256 CHAR(64) NULL,
            result_fingerprint_sha256 CHAR(64) NOT NULL,
            result_status ENUM('passed', 'blocked') NOT NULL,
            result_json JSON NOT NULL,
            artifact_count INT UNSIGNED NOT NULL,
            verified_artifact_count INT UNSIGNED NOT NULL,
            check_count INT UNSIGNED NOT NULL,
            passed_check_count INT UNSIGNED NOT NULL,
            blocker_count INT UNSIGNED NOT NULL,
            verified_by VARCHAR(191) NOT NULL,
            verification_note VARCHAR(1000) NULL,
            verified_at_kst DATETIME(6) NOT NULL,
            KEY idx_data_deletion_backup_verification_plan_time (
                dry_run_plan_id,
                verified_at_kst
            ),
            KEY idx_data_deletion_backup_verification_request_time (
                request_id,
                verified_at_kst
            ),
            KEY idx_data_deletion_backup_verification_status (
                result_status,
                verified_at_kst
            ),
            KEY idx_data_deletion_backup_verification_result (
                result_fingerprint_sha256
            ),
            KEY idx_data_deletion_backup_verification_evidence (
                evidence_set_fingerprint_sha256
            ),
            KEY idx_data_deletion_backup_verification_build (build_id),
            CONSTRAINT fk_data_deletion_backup_verification_request
                FOREIGN KEY (request_id) REFERENCES data_deletion_requests(id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_data_deletion_backup_verification_plan
                FOREIGN KEY (dry_run_plan_id) REFERENCES data_deletion_dry_run_plans(id)
                ON DELETE RESTRICT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS raw_player_snapshots (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            account_id VARCHAR(128) NOT NULL,
            shard VARCHAR(32) NOT NULL,
            fetched_at_kst DATETIME(6) NOT NULL,
            payload JSON NOT NULL,
            KEY idx_raw_player_snapshots_account (account_id, shard, fetched_at_kst)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS matches (
            match_id VARCHAR(191) NOT NULL PRIMARY KEY,
            shard VARCHAR(32) NOT NULL,
            map_name VARCHAR(64) NULL,
            game_mode VARCHAR(64) NULL,
            match_type VARCHAR(64) NULL,
            team_mode VARCHAR(32) NULL,
            perspective VARCHAR(16) NULL,
            is_custom_match TINYINT(1) NOT NULL DEFAULT 0,
            season_state VARCHAR(64) NULL,
            created_at_kst DATETIME(6) NULL,
            duration_seconds INT NULL,
            telemetry_url TEXT NULL,
            total_players INT NULL,
            human_players INT NULL,
            bot_players INT NULL,
            fetched_at_kst DATETIME(6) NOT NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            KEY idx_matches_created_map_mode (created_at_kst, map_name, game_mode)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS raw_match_payloads (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            shard VARCHAR(32) NOT NULL,
            storage_root VARCHAR(64) NOT NULL,
            relative_path VARCHAR(512) NOT NULL,
            compression VARCHAR(16) NOT NULL,
            size_bytes BIGINT UNSIGNED NOT NULL,
            sha256 CHAR(64) NOT NULL,
            source_url TEXT NULL,
            fetched_at_kst DATETIME(6) NOT NULL,
            parser_version VARCHAR(64) NULL,
            UNIQUE KEY uq_raw_match_payloads_match (match_id),
            CONSTRAINT fk_raw_match_payloads_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS raw_telemetry_payloads (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            shard VARCHAR(32) NOT NULL,
            asset_url TEXT NULL,
            storage_root VARCHAR(64) NOT NULL,
            relative_path VARCHAR(512) NOT NULL,
            compression VARCHAR(16) NOT NULL,
            size_bytes BIGINT UNSIGNED NOT NULL,
            sha256 CHAR(64) NOT NULL,
            fetched_at_kst DATETIME(6) NOT NULL,
            parser_version VARCHAR(64) NULL,
            UNIQUE KEY uq_raw_telemetry_payloads_match (match_id),
            CONSTRAINT fk_raw_telemetry_payloads_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS match_participants (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            name VARCHAR(191) NULL,
            roster_id VARCHAR(191) NULL,
            team_id INT NULL,
            win_place INT NULL,
            kills INT NULL,
            assists INT NULL,
            damage_dealt FLOAT NULL,
            death_type VARCHAR(64) NULL,
            is_ai_or_bot TINYINT(1) NOT NULL DEFAULT 0,
            ai_detection_source VARCHAR(64) NOT NULL DEFAULT 'human_default',
            raw_stats JSON NULL,
            UNIQUE KEY uq_match_participants_match_account (match_id, account_id),
            KEY idx_match_participants_account (account_id, match_id),
            CONSTRAINT fk_match_participants_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS player_collection_states (
            registered_player_id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
            last_polled_at_kst DATETIME(6) NULL,
            last_seen_match_id VARCHAR(191) NULL,
            last_error TEXT NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            CONSTRAINT fk_player_collection_states_registered_player
                FOREIGN KEY (registered_player_id) REFERENCES registered_players(id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS player_match_combat_summaries (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            shots_fired INT NOT NULL DEFAULT 0,
            shots_hit INT NOT NULL DEFAULT 0,
            hits_taken INT NOT NULL DEFAULT 0,
            damage_dealt FLOAT NOT NULL DEFAULT 0,
            damage_taken FLOAT NOT NULL DEFAULT 0,
            kills INT NOT NULL DEFAULT 0,
            assists INT NOT NULL DEFAULT 0,
            deaths INT NOT NULL DEFAULT 0,
            dbnos_caused INT NOT NULL DEFAULT 0,
            dbnos_taken INT NOT NULL DEFAULT 0,
            finishes INT NOT NULL DEFAULT 0,
            finishes_taken INT NOT NULL DEFAULT 0,
            headshot_hits INT NOT NULL DEFAULT 0,
            headshot_hits_taken INT NOT NULL DEFAULT 0,
            headshot_kills INT NOT NULL DEFAULT 0,
            headshot_deaths INT NOT NULL DEFAULT 0,
            headshot_dbnos_caused INT NOT NULL DEFAULT 0,
            headshot_dbnos_taken INT NOT NULL DEFAULT 0,
            hit_parts JSON NULL,
            taken_hit_parts JSON NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_player_match_combat_summaries (match_id, account_id),
            KEY idx_player_match_combat_account (account_id, match_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS player_weapon_match_stats (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            weapon_code VARCHAR(128) NOT NULL,
            shots_fired INT NOT NULL DEFAULT 0,
            shots_hit INT NOT NULL DEFAULT 0,
            hits_taken INT NOT NULL DEFAULT 0,
            damage_dealt FLOAT NOT NULL DEFAULT 0,
            damage_taken FLOAT NOT NULL DEFAULT 0,
            kills INT NOT NULL DEFAULT 0,
            assists INT NOT NULL DEFAULT 0,
            deaths INT NOT NULL DEFAULT 0,
            dbnos INT NOT NULL DEFAULT 0,
            dbnos_taken INT NOT NULL DEFAULT 0,
            finishes INT NOT NULL DEFAULT 0,
            finishes_taken INT NOT NULL DEFAULT 0,
            headshot_hits INT NOT NULL DEFAULT 0,
            headshot_hits_taken INT NOT NULL DEFAULT 0,
            headshot_kills INT NOT NULL DEFAULT 0,
            headshot_deaths INT NOT NULL DEFAULT 0,
            headshot_dbnos INT NOT NULL DEFAULT 0,
            headshot_dbnos_taken INT NOT NULL DEFAULT 0,
            hit_parts JSON NULL,
            taken_hit_parts JSON NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_player_weapon_match_stats (match_id, account_id, weapon_code),
            KEY idx_player_weapon_stats_account_weapon (account_id, weapon_code, match_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS player_item_events (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            event_index INT NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            action VARCHAR(32) NOT NULL,
            event_at_kst DATETIME(6) NULL,
            common_is_game FLOAT NULL,
            item_code VARCHAR(191) NULL,
            item_name_ko VARCHAR(191) NULL,
            item_category VARCHAR(64) NULL,
            item_sub_category VARCHAR(64) NULL,
            stack_count INT NULL,
            parent_item_code VARCHAR(191) NULL,
            parent_item_name_ko VARCHAR(191) NULL,
            child_item_code VARCHAR(191) NULL,
            child_item_name_ko VARCHAR(191) NULL,
            location_x FLOAT NULL,
            location_y FLOAT NULL,
            location_z FLOAT NULL,
            raw_event JSON NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_player_item_events (match_id, account_id, event_index),
            KEY idx_player_item_events_account_action (account_id, action, match_id),
            KEY idx_player_item_events_item (item_code, action, match_id),
            CONSTRAINT fk_player_item_events_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS player_item_match_stats (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            item_code VARCHAR(191) NOT NULL,
            item_name_ko VARCHAR(191) NULL,
            item_category VARCHAR(64) NULL,
            item_sub_category VARCHAR(64) NULL,
            picked_up_events INT NOT NULL DEFAULT 0,
            picked_up_quantity INT NOT NULL DEFAULT 0,
            loot_box_pickup_events INT NOT NULL DEFAULT 0,
            carepackage_pickup_events INT NOT NULL DEFAULT 0,
            dropped_events INT NOT NULL DEFAULT 0,
            dropped_quantity INT NOT NULL DEFAULT 0,
            used_events INT NOT NULL DEFAULT 0,
            used_quantity INT NOT NULL DEFAULT 0,
            equipped_events INT NOT NULL DEFAULT 0,
            unequipped_events INT NOT NULL DEFAULT 0,
            attached_events INT NOT NULL DEFAULT 0,
            detached_events INT NOT NULL DEFAULT 0,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_player_item_match_stats (match_id, account_id, item_code),
            KEY idx_player_item_stats_account_item (account_id, item_code, match_id),
            CONSTRAINT fk_player_item_match_stats_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS player_position_samples (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            event_index INT NOT NULL,
            event_at_kst DATETIME(6) NULL,
            common_is_game FLOAT NULL,
            elapsed_time_seconds FLOAT NULL,
            num_alive_players INT NULL,
            x FLOAT NULL,
            y FLOAT NULL,
            z FLOAT NULL,
            is_in_vehicle TINYINT(1) NULL,
            is_in_blue_zone TINYINT(1) NULL,
            is_in_red_zone TINYINT(1) NULL,
            in_special_zone VARCHAR(64) NULL,
            is_dbno TINYINT(1) NULL,
            zone JSON NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_player_position_samples (match_id, account_id, event_index),
            KEY idx_player_position_samples_account_time (account_id, match_id, event_at_kst),
            CONSTRAINT fk_player_position_samples_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS player_landing_events (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            event_index INT NOT NULL,
            event_at_kst DATETIME(6) NULL,
            common_is_game FLOAT NULL,
            x FLOAT NULL,
            y FLOAT NULL,
            z FLOAT NULL,
            distance_m FLOAT NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_player_landing_events (match_id, account_id, event_index),
            KEY idx_player_landing_events_account_time (account_id, event_at_kst),
            CONSTRAINT fk_player_landing_events_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS player_movement_summaries (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            sample_count INT NOT NULL DEFAULT 0,
            first_event_at_kst DATETIME(6) NULL,
            last_event_at_kst DATETIME(6) NULL,
            first_x FLOAT NULL,
            first_y FLOAT NULL,
            first_z FLOAT NULL,
            last_x FLOAT NULL,
            last_y FLOAT NULL,
            last_z FLOAT NULL,
            landing_event_at_kst DATETIME(6) NULL,
            landing_x FLOAT NULL,
            landing_y FLOAT NULL,
            landing_z FLOAT NULL,
            landing_distance_m FLOAT NULL,
            total_sampled_distance_m FLOAT NOT NULL DEFAULT 0,
            in_game_sampled_distance_m FLOAT NOT NULL DEFAULT 0,
            vehicle_sample_count INT NOT NULL DEFAULT 0,
            dbno_sample_count INT NOT NULL DEFAULT 0,
            max_altitude_z FLOAT NULL,
            min_altitude_z FLOAT NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_player_movement_summaries (match_id, account_id),
            KEY idx_player_movement_summaries_account (account_id, match_id),
            CONSTRAINT fk_player_movement_summaries_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS player_combat_location_events (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            related_account_id VARCHAR(128) NULL,
            event_index INT NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            action VARCHAR(32) NOT NULL,
            event_at_kst DATETIME(6) NULL,
            common_is_game FLOAT NULL,
            damage_type_category VARCHAR(64) NULL,
            damage_causer_name VARCHAR(128) NULL,
            damage_reason VARCHAR(64) NULL,
            is_headshot TINYINT(1) NOT NULL DEFAULT 0,
            distance_m FLOAT NULL,
            x FLOAT NULL,
            y FLOAT NULL,
            z FLOAT NULL,
            related_x FLOAT NULL,
            related_y FLOAT NULL,
            related_z FLOAT NULL,
            raw_event JSON NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_player_combat_location_events (match_id, account_id, event_index, action),
            KEY idx_player_combat_location_account_action (account_id, action, match_id),
            KEY idx_player_combat_location_related (related_account_id, match_id),
            CONSTRAINT fk_player_combat_location_events_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS player_combat_loadout_snapshots (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            account_id VARCHAR(128) NOT NULL,
            combat_event_index INT NOT NULL,
            combat_action VARCHAR(32) NOT NULL,
            combat_event_at_kst DATETIME(6) NULL,
            weapon_code VARCHAR(128) NOT NULL,
            weapon_name_ko VARCHAR(191) NULL,
            attachment_codes JSON NULL,
            attachment_names_ko JSON NULL,
            attachment_count INT NOT NULL DEFAULT 0,
            distance_m FLOAT NULL,
            is_headshot TINYINT(1) NOT NULL DEFAULT 0,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_player_combat_loadout_snapshots (
                match_id,
                account_id,
                combat_event_index,
                combat_action
            ),
            KEY idx_player_combat_loadout_account_weapon (account_id, weapon_code, match_id),
            KEY idx_player_combat_loadout_weapon_count (weapon_code, attachment_count),
            CONSTRAINT fk_player_combat_loadout_snapshots_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS match_care_package_events (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            event_index INT NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            event_at_kst DATETIME(6) NULL,
            common_is_game FLOAT NULL,
            item_package_id VARCHAR(191) NULL,
            item_count INT NOT NULL DEFAULT 0,
            item_codes JSON NULL,
            x FLOAT NULL,
            y FLOAT NULL,
            z FLOAT NULL,
            raw_event JSON NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_match_care_package_events (match_id, event_index, event_type),
            KEY idx_match_care_package_events_match_time (match_id, event_at_kst),
            CONSTRAINT fk_match_care_package_events_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS match_plane_routes (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            source VARCHAR(64) NOT NULL,
            sample_count INT NOT NULL DEFAULT 0,
            start_event_index INT NOT NULL,
            end_event_index INT NOT NULL,
            start_event_at_kst DATETIME(6) NULL,
            end_event_at_kst DATETIME(6) NULL,
            start_x FLOAT NULL,
            start_y FLOAT NULL,
            start_z FLOAT NULL,
            end_x FLOAT NULL,
            end_y FLOAT NULL,
            end_z FLOAT NULL,
            sample_account_id VARCHAR(128) NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_match_plane_routes (match_id),
            CONSTRAINT fk_match_plane_routes_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS match_phase_events (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            event_index INT NOT NULL,
            event_at_kst DATETIME(6) NULL,
            common_is_game FLOAT NULL,
            elapsed_time_seconds FLOAT NULL,
            num_alive_players INT NULL,
            num_alive_teams INT NULL,
            safety_zone_x FLOAT NULL,
            safety_zone_y FLOAT NULL,
            safety_zone_z FLOAT NULL,
            safety_zone_radius FLOAT NULL,
            poison_gas_warning_x FLOAT NULL,
            poison_gas_warning_y FLOAT NULL,
            poison_gas_warning_z FLOAT NULL,
            poison_gas_warning_radius FLOAT NULL,
            red_zone_x FLOAT NULL,
            red_zone_y FLOAT NULL,
            red_zone_z FLOAT NULL,
            red_zone_radius FLOAT NULL,
            black_zone_x FLOAT NULL,
            black_zone_y FLOAT NULL,
            black_zone_z FLOAT NULL,
            black_zone_radius FLOAT NULL,
            raw_event JSON NULL,
            updated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_match_phase_events (match_id, event_index),
            KEY idx_match_phase_events_match_time (match_id, event_at_kst),
            CONSTRAINT fk_match_phase_events_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS replay_artifacts (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_id VARCHAR(191) NOT NULL,
            shard VARCHAR(32) NOT NULL,
            artifact_type VARCHAR(32) NOT NULL,
            artifact_name VARCHAR(191) NOT NULL,
            account_id VARCHAR(128) NOT NULL DEFAULT '',
            storage_backend VARCHAR(32) NOT NULL,
            storage_root VARCHAR(64) NOT NULL,
            relative_path VARCHAR(512) NOT NULL,
            content_type VARCHAR(64) NOT NULL,
            size_bytes BIGINT UNSIGNED NOT NULL,
            sha256 CHAR(64) NOT NULL,
            renderer_version VARCHAR(64) NOT NULL,
            source_tables JSON NULL,
            generated_at_kst DATETIME(6) NOT NULL,
            UNIQUE KEY uq_replay_artifacts (match_id, artifact_type, artifact_name, account_id),
            KEY idx_replay_artifacts_account_type (account_id, artifact_type, generated_at_kst),
            KEY idx_replay_artifacts_match_type (match_id, artifact_type),
            CONSTRAINT fk_replay_artifacts_match
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
                ON DELETE CASCADE
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    ]


def count_tables(connection: Any) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        return len(cursor.fetchall())


def _validate_identifier(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_$]+", value):
        raise DatabaseError(f"invalid {label} identifier: {value!r}")
    return value


def execute_statements(connection: Any, statements: Iterable[str]) -> int:
    count = 0
    with connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
            count += 1
    return count
