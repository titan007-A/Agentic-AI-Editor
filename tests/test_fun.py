from datetime import UTC, date, datetime, timedelta

from agentic_editor import fun
from agentic_editor.db import ReelLog, User, make_sessionmaker

_ids = iter(range(1, 10**6))


def _row(at, owner="u1", preset="p", captioned=False, posted=0):
    return ReelLog(output_id=next(_ids), owner_id=owner, preset=preset, made_at=at,
                   captioned=captioned, posted=posted)  # fmt: skip


def test_a_streak_counts_days_in_a_row_up_to_today_or_yesterday():
    d = date(2026, 11, 12)
    days = {d - timedelta(days=i) for i in (0, 1, 2)} | {d - timedelta(days=9)}
    assert fun.streaks(days, d) == (3, 3)
    # Today not made yet: yesterday's run still counts.
    assert fun.streaks(days - {d}, d) == (2, 2)
    assert fun.streaks({d - timedelta(days=3)}, d) == (0, 1)
    assert fun.streaks(set(), d) == (0, 0)


def test_days_are_indias_so_half_past_midnight_is_the_new_day_and_a_night_owl():
    # 19:00 UTC = 00:30 IST the next day.
    late = datetime(2026, 11, 11, 19, 0)
    assert fun.local(late).date() == date(2026, 11, 12) and fun.local(late).hour == 0
    won = {b["id"]: b for b in fun.badges([_row(late)])}
    assert won["night_owl"]["won_at"] and won["first_reel"]["won_at"]
    assert won["early_bird"]["won_at"] is None and won["reels_10"]["progress"] == 1


def test_badges_are_won_at_the_reel_that_made_them_true():
    start = datetime(2026, 11, 1, 6, 0)  # 11:30 IST
    rows = [_row(start + timedelta(days=d, minutes=m), preset=f"p{m % 5}", captioned=True)
            for d in range(7) for m in range(4)]  # fmt: skip
    won = {b["id"]: b for b in fun.badges(rows)}
    assert won["streak_7"]["won_at"].startswith("2026-11-07")
    assert won["streak_30"]["won_at"] is None and won["streak_30"]["progress"] == 7
    assert won["reels_10"]["won_at"].startswith("2026-11-03")
    assert won["captions_25"]["won_at"].startswith("2026-11-07")
    assert won["variety_5"]["won_at"] is None and won["variety_5"]["progress"] == 4
    assert won["day_10"]["progress"] == 4 and won["day_10"]["won_at"] is None


def test_stats_give_my_counts_and_the_teams_board(tmp_path):
    sessions = make_sessionmaker(tmp_path / "app.db")
    now = datetime(2026, 11, 12, 10, 0, tzinfo=UTC)  # Thursday, 15:30 IST
    with sessions() as s:
        s.add_all([User(id="u1", username="siddhant", role="admin", password_hash="x"),
                   User(id="u2", username="rishab", role="member", password_hash="x")])  # fmt: skip
        s.add_all([
            _row(now - timedelta(hours=1), "u1", posted=1),
            _row(now - timedelta(days=1), None),  # before accounts: the owner's
            _row(now - timedelta(days=5), "u1"),  # last week
            _row(now - timedelta(hours=2), "u2"), _row(now - timedelta(hours=3), "u2"),
        ])  # fmt: skip
        s.commit()
        me = s.get(User, "u1")
        got = fun.stats(s, me, now)
    assert got["me"]["today"] == 1 and got["me"]["week"] == 2 and got["me"]["total"] == 3
    assert got["me"]["streak"] == 2 and got["me"]["made_today"] and got["me"]["posts"] == 1
    assert got["leaderboard"]["today"] == [{"user": "rishab", "reels": 2},
                                           {"user": "siddhant", "reels": 1}]  # fmt: skip
    assert (
        got["leaderboard"]["week"][0] == {"user": "siddhant", "reels": 2}
        or got["leaderboard"]["week"][0]["reels"] == 2
    )
