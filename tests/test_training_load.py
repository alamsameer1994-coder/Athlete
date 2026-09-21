from datetime import date, timedelta

from athlete_coach.coaching.training_load import compute_ctl_atl_series, estimate_tss


def test_estimate_tss_power():
    tss, source = estimate_tss(duration_s=3600, normalized_power=200, ftp_watts=200)
    assert source == "power"
    assert tss == 100.0


def test_estimate_tss_hr_fallback():
    tss, source = estimate_tss(duration_s=1800, avg_hr=150, threshold_hr=170)
    assert source == "hr"
    assert tss is not None and tss > 0


def test_estimate_tss_rpe_fallback():
    tss, source = estimate_tss(duration_s=3600, rpe=7)
    assert source == "rpe"
    assert tss == 70.0


def test_estimate_tss_duration_fallback():
    tss, source = estimate_tss(duration_s=3600)
    assert source == "duration"
    assert tss == 50.0


def test_estimate_tss_none_without_duration():
    tss, source = estimate_tss(duration_s=None)
    assert tss is None
    assert source == "none"


def test_ctl_atl_rises_with_sustained_load():
    start = date(2025, 1, 1)
    end = start + timedelta(days=60)
    daily = {start + timedelta(days=i): 80.0 for i in range(60)}
    series = compute_ctl_atl_series(daily, start, end)
    assert series[-1].ctl > series[10].ctl
    assert series[-1].ctl > series[-1].atl - 5  # steady load -> ATL converges near CTL


def test_tsb_negative_after_load_spike():
    start = date(2025, 1, 1)
    end = start + timedelta(days=50)
    daily = {start + timedelta(days=i): 40.0 for i in range(40)}
    for i in range(40, 45):
        daily[start + timedelta(days=i)] = 150.0  # sudden overload
    series = compute_ctl_atl_series(daily, start, end)
    spike_point = series[44]
    assert spike_point.tsb < 0
