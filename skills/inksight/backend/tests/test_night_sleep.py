import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.routes.render import _configured_refresh_minutes
from api.shared import night_refresh_minutes_for_config, resolve_refresh_minutes_for_device_state

TZ = ZoneInfo('Asia/Shanghai')
NIGHT = datetime(2026, 11, 1, 2, tzinfo=TZ)
DAY = datetime(2026, 11, 1, 14, tzinfo=TZ)


def _cfg(interval=0, start=0, end=6, tz='Asia/Shanghai', refresh=None):
    data = {
        'night_refresh_interval': interval,
        'night_start_hour': start,
        'night_end_hour': end,
        'timezone': tz,
    }
    if refresh is not None:
        data['refresh_interval'] = refresh
    return data


def test_disabled_never_applies():
    assert night_refresh_minutes_for_config(_cfg(0), now=NIGHT) == 0
    assert night_refresh_minutes_for_config(_cfg(0), now=DAY) == 0


def test_regular_window():
    cfg = _cfg(360, 0, 6)
    assert night_refresh_minutes_for_config(cfg, now=datetime(2026, 11, 1, 1, tzinfo=TZ)) == 360
    assert night_refresh_minutes_for_config(cfg, now=datetime(2026, 11, 1, 5, tzinfo=TZ)) == 360
    assert night_refresh_minutes_for_config(cfg, now=datetime(2026, 11, 1, 6, tzinfo=TZ)) == 0
    assert night_refresh_minutes_for_config(cfg, now=DAY) == 0


def test_window_across_midnight():
    cfg = _cfg(360, 23, 6)
    assert night_refresh_minutes_for_config(cfg, now=datetime(2026, 11, 1, 23, 30, tzinfo=TZ)) == 360
    assert night_refresh_minutes_for_config(cfg, now=NIGHT) == 360
    assert night_refresh_minutes_for_config(cfg, now=datetime(2026, 11, 1, 22, tzinfo=TZ)) == 0
    assert night_refresh_minutes_for_config(cfg, now=datetime(2026, 11, 1, 7, tzinfo=TZ)) == 0


def test_custom_window():
    cfg = _cfg(600, 22, 5)
    assert night_refresh_minutes_for_config(cfg, now=datetime(2026, 11, 1, 22, 30, tzinfo=TZ)) == 600
    assert night_refresh_minutes_for_config(cfg, now=datetime(2026, 11, 1, 4, 30, tzinfo=TZ)) == 600
    assert night_refresh_minutes_for_config(cfg, now=DAY) == 0


def test_edge_cases():
    assert night_refresh_minutes_for_config(_cfg(360, 3, 3), now=datetime(2026, 11, 1, 3, tzinfo=TZ)) == 0
    assert night_refresh_minutes_for_config(None) == 0
    assert night_refresh_minutes_for_config(_cfg(360, 0, 6, 'Not/AZone'), now=NIGHT) == 360


def test_night_overrides_device_reported_interval():
    state = {'expected_refresh_min': 20}
    assert resolve_refresh_minutes_for_device_state(_cfg(0), state, now=DAY) == 20
    assert resolve_refresh_minutes_for_device_state(_cfg(360, 0, 6), state, now=NIGHT) == 360
    assert resolve_refresh_minutes_for_device_state(_cfg(30, 0, 6), state, now=NIGHT) == 30


def test_configured_refresh_minutes_uses_night_value():
    in_night = 0 <= datetime.now().hour < 6
    assert _configured_refresh_minutes(_cfg(360, 0, 6, refresh=20)) == (360 if in_night else 20)
    assert _configured_refresh_minutes(_cfg(0, 0, 6, refresh=20)) == 20
    assert _configured_refresh_minutes(_cfg(0, 0, 6, refresh=5)) == 10
