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


def test_the_daily_quest_stays_all_day_fits_the_person_and_counts_their_reels():
    day = date(2026, 11, 12)
    noon = datetime(2026, 11, 12, 6, 30)  # 12:00 IST
    # Two weeks of 10 reels a day, with captions and Buffer posts.
    history = [
        _row(noon - timedelta(days=d, minutes=m), captioned=True, posted=1, preset=f"p{d % 3}")
        for d in range(1, 15)
        for m in range(10)
    ]
    quest = fun.quest_for(history, day, "u1")
    assert quest == fun.quest_for(history, day, "u1")  # the same all day
    kinds = {fun.quest_for(history, day + timedelta(days=i), "u1").kind for i in range(30)}
    assert kinds >= {"reels", "captions", "posts", "morning"}  # it varies
    reels = fun.Quest("reels", "Make 12 reels today", 12)
    assert fun.quest_for([], day, "u1") == fun.Quest("reels", "Make 3 reels today", 3)
    today = [_row(noon + timedelta(minutes=m)) for m in range(12)]
    progress, at = fun.quest_progress(reels, history + today, day)
    assert progress == 12 and at == today[11].made_at
    morning = fun.Quest("morning", "", 3)
    early = [_row(datetime(2026, 11, 12, 1, 0)), _row(datetime(2026, 11, 12, 2, 0))]
    late = [_row(datetime(2026, 11, 12, 10, 0))]  # 15:30 IST: not the morning
    assert fun.quest_progress(morning, early + late, day) == (2, None)


def test_wrapped_sums_up_a_month(tmp_path):
    sessions = make_sessionmaker(tmp_path / "app.db")
    with sessions() as s:
        s.add_all([User(id="u1", username="siddhant", role="admin", password_hash="x"),
                   User(id="u2", username="rishab", role="member", password_hash="x")])  # fmt: skip
        base = datetime(2026, 11, 3, 14, 0)  # 19:30 IST
        rows = [_row(base + timedelta(days=d), "u1", preset="vocal") for d in range(3)]
        rows[0].song, rows[0].artist = "Felicità", "Al Bano"
        rows[1].song, rows[1].artist = "Felicità", "Al Bano"
        rows += [_row(base, "u1", preset="shorts"), _row(base + timedelta(hours=1), "u2")]
        rows.append(_row(datetime(2026, 10, 30, 10, 0), "u1"))  # last month
        s.add_all(rows)
        s.commit()
        got = fun.wrapped(s, s.get(User, "u1"), "2026-11")
    assert got["label"] == "November 2026" and got["reels"] == 4 and got["active_days"] == 3
    assert got["best_day"] == {"date": "2026-11-03", "reels": 2} and got["best_run"] == 3
    assert got["top_preset"] == {"name": "vocal", "reels": 3}
    assert got["top_song"]["name"] == "Felicità" and got["top_artist"]["name"] == "Al Bano"
    assert got["busiest_hour"] == 19 and got["team_place"] == 1 and got["team_size"] == 2
    assert got["months"] == ["2026-11", "2026-10"]
