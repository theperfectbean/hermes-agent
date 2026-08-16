import json

from hermes_state import SessionDB
from hermes_state_common import SCHEMA_VERSION


def test_forward_assistant_safety_metadata_is_nullable_and_not_replayed(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("safety", source="cli")
        db.append_message("safety", "user", "check the service")
        db.append_message(
            "safety",
            "assistant",
            "observed healthy",
            model="model-a",
            billing_provider="provider-a",
            operational_status="verified",
            evidence_metadata={"policy_version": 1, "evidence": [{"kind": "read-back"}]},
        )
        record = db.get_latest_safety_record("safety")
        assert record["model"] == "model-a"
        assert record["billing_provider"] == "provider-a"
        assert record["operational_status"] == "verified"
        assert record["evidence_metadata"]["policy_version"] == 1

        replay = db.get_messages_as_conversation("safety")
        assert all("operational_status" not in row for row in replay)
        assert all("evidence_metadata" not in row for row in replay)
    finally:
        db.close()


def test_safety_metadata_rejects_oversized_json(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        db.create_session("bounded", source="cli")
        db.append_message(
            "bounded", "assistant", "answer", operational_status="unverified",
            evidence_metadata={"raw": "x" * 9000},
        )
        record = db.get_latest_safety_record("bounded")
        assert record["evidence_metadata"] is None
    finally:
        db.close()


def test_schema_version_advances_without_historic_backfill(tmp_path):
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        assert SCHEMA_VERSION == 27
        db.create_session("historic", source="cli")
        db.append_message("historic", "assistant", "historic answer")
        with db._read_ctx() as conn:
            row = conn.execute(
                "SELECT model, billing_provider, operational_status, evidence_json "
                "FROM messages WHERE session_id = ?",
                ("historic",),
            ).fetchone()
        assert tuple(row) == (None, None, None, None)
    finally:
        db.close()


def test_schema_26_upgrade_adds_nullable_safety_columns(tmp_path):
    path = tmp_path / "upgrade.db"
    db = SessionDB(db_path=path)
    try:
        db.create_session("upgrade", source="cli")
        for column in ("evidence_json", "operational_status", "billing_provider", "model"):
            db._conn.execute(f"ALTER TABLE messages DROP COLUMN {column}")
        db._conn.execute("UPDATE schema_version SET version = 26")
        db._conn.commit()
    finally:
        db.close()

    upgraded = SessionDB(db_path=path)
    try:
        with upgraded._read_ctx() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        assert {"model", "billing_provider", "operational_status", "evidence_json"} <= columns
    finally:
        upgraded.close()
