from datetime import date, timedelta

import pytest

from athlete_coach.coaching.nutrition import intake_vs_target
from athlete_coach.db import get_conn, init_db
from athlete_coach.mfp_client import MyFitnessPalClient, parse_cookie_header
from athlete_coach.sync.myfitnesspal_sync import sync_myfitnesspal_nutrition


def test_parse_cookie_header_basic():
    parsed = parse_cookie_header("myfitnesspal_session=abc123; other_cookie=xyz; foo=bar=baz")
    assert parsed["myfitnesspal_session"] == "abc123"
    assert parsed["other_cookie"] == "xyz"


def test_parse_cookie_header_empty():
    assert parse_cookie_header("") == {}


def test_client_not_authorized_without_cookie(monkeypatch):
    monkeypatch.delenv("MFP_COOKIE", raising=False)
    client = MyFitnessPalClient()
    assert client.authorized is False


@pytest.fixture()
def tmp_db(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(db_path)
    return db_path


def test_sync_not_authorized_makes_no_db_changes(tmp_db, monkeypatch):
    monkeypatch.setattr(
        "athlete_coach.sync.myfitnesspal_sync.MyFitnessPalClient",
        lambda: type("C", (), {"authorized": False})(),
    )
    with get_conn(tmp_db) as conn:
        result = sync_myfitnesspal_nutrition(conn, days_back=5)
        assert result == {"error": "not_authorized", "days_synced": 0}
        count = conn.execute("SELECT COUNT(*) as c FROM nutrition_logs").fetchone()["c"]
        assert count == 0


def test_sync_upserts_and_skips_manual_entries(tmp_db, monkeypatch):
    today = date.today()
    manual_date = (today - timedelta(days=1)).isoformat()
    synced_date = today.isoformat()

    with get_conn(tmp_db) as conn:
        conn.execute(
            "INSERT INTO nutrition_logs (date, calories, source) VALUES (?, 1800, 'manual')",
            (manual_date,),
        )

    class FakeClient:
        authorized = True

        def get_range(self, start, end):
            return [
                {"date": manual_date, "calories": 2500, "protein_g": 150, "carbs_g": 250, "fat_g": 70, "complete": True},
                {"date": synced_date, "calories": 2100, "protein_g": 160, "carbs_g": 220, "fat_g": 60, "complete": False},
            ]

    monkeypatch.setattr("athlete_coach.sync.myfitnesspal_sync.MyFitnessPalClient", lambda: FakeClient())

    with get_conn(tmp_db) as conn:
        result = sync_myfitnesspal_nutrition(conn, days_back=2)
        assert result == {"days_synced": 1, "skipped_manual": 1}

        manual_row = conn.execute("SELECT * FROM nutrition_logs WHERE date = ?", (manual_date,)).fetchone()
        assert manual_row["calories"] == 1800  # untouched
        assert manual_row["source"] == "manual"

        synced_row = conn.execute("SELECT * FROM nutrition_logs WHERE date = ?", (synced_date,)).fetchone()
        assert synced_row["calories"] == 2100
        assert synced_row["source"] == "myfitnesspal"
        assert synced_row["complete"] == 0


def test_intake_vs_target_computes_average_and_diff(tmp_db):
    today = date.today()
    with get_conn(tmp_db) as conn:
        for i, cals in enumerate([2000, 2200, 1900]):
            d = (today - timedelta(days=i)).isoformat()
            conn.execute(
                "INSERT INTO nutrition_logs (date, calories, protein_g, source) VALUES (?, ?, 150, 'myfitnesspal')",
                (d, cals),
            )

    with get_conn(tmp_db) as conn:
        result = intake_vs_target(conn, target_calories=2000, target_protein_g=160, lookback_days=7)

    assert result["days_logged"] == 3
    assert result["avg_calories"] == 2033
    assert result["calorie_diff"] == 33
    assert result["avg_protein_g"] == 150
    assert result["protein_diff_g"] == -10


def test_intake_vs_target_no_data(tmp_db):
    with get_conn(tmp_db) as conn:
        result = intake_vs_target(conn, target_calories=2000, lookback_days=7)
    assert result["days_logged"] == 0
    assert "message" in result
