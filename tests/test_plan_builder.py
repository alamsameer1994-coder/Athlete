from datetime import date

from athlete_coach.coaching.plan_builder import compute_block_structure, default_sessions_for_week


def test_block_structure_covers_full_range_ending_in_taper():
    start = date(2025, 1, 6)  # Monday
    race = date(2025, 6, 30)  # Monday
    specs = compute_block_structure(start, race, "marathon", current_weekly_hours=5, peak_weekly_hours=9)

    assert specs[0].week_start == start
    assert specs[-1].block_type == "taper"
    assert specs[-2].block_type == "taper"  # marathon -> 3 week taper
    assert specs[-3].block_type == "taper"
    assert specs[-4].block_type != "taper"

    weeks_span = (race - start).days // 7 + 1
    assert len(specs) == weeks_span


def test_volume_ramps_up_then_down_in_taper():
    start = date(2025, 1, 6)
    race = date(2025, 6, 30)
    specs = compute_block_structure(start, race, "marathon", current_weekly_hours=5, peak_weekly_hours=10)

    base_build = [s for s in specs if s.block_type in ("base", "build")]
    assert base_build[-1].target_weekly_hours >= base_build[0].target_weekly_hours

    assert specs[-1].target_weekly_hours < max(s.target_weekly_hours for s in specs)


def test_deload_every_fourth_week_in_ramp():
    start = date(2025, 1, 6)
    race = date(2025, 9, 1)
    specs = compute_block_structure(start, race, "marathon", current_weekly_hours=5, peak_weekly_hours=10)
    ramp = [s.block_type for s in specs if s.block_type in ("base", "build", "recovery")]
    assert "recovery" in ramp


def test_default_sessions_have_valid_dates_within_week():
    start = date(2025, 1, 6)
    race = date(2025, 3, 3)
    specs = compute_block_structure(start, race, "10k", current_weekly_hours=4, peak_weekly_hours=6)
    for spec in specs:
        sessions = default_sessions_for_week(spec)
        assert len(sessions) > 0
        for s in sessions:
            d = date.fromisoformat(s["date"])
            assert 0 <= (d - spec.week_start).days < 7
