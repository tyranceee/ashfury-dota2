import unittest

from deploy.historical_profile import (
    _display_risk,
    _nonlinear_result_risk,
    build_snapshot,
    coarse_roles_by_slot,
    strict_history,
)


def match(match_id, start_time, *, win=True, deaths=4, gpm=500, hero_id=1):
    return {
        "match_id": match_id,
        "start_time": start_time,
        "duration": 2400,
        "lobby_type": 7,
        "hero_id": hero_id,
        "player_slot": 0,
        "radiant_win": win,
        "kills": 8,
        "deaths": deaths,
        "assists": 12,
        "leaver_status": 0,
        "gold_per_min": gpm,
        "xp_per_min": 700,
        "last_hits": 240,
        "denies": 8,
        "hero_damage": 24000,
        "tower_damage": 2500,
        "hero_healing": 0,
        "level": 25,
    }


class HistoricalProfileV01Tests(unittest.TestCase):
    def test_coarse_role_uses_each_teams_top_three_last_hitters(self):
        players = []
        for base_slot in (0, 128):
            for offset, last_hits in enumerate((50, 300, 10, 200, 100)):
                players.append({
                    "player_slot": base_slot + offset,
                    "last_hits": last_hits,
                })
        roles = coarse_roles_by_slot(players)
        for base_slot in (0, 128):
            self.assertEqual(roles[base_slot + 0], "support")
            self.assertEqual(roles[base_slot + 1], "core")
            self.assertEqual(roles[base_slot + 2], "support")
            self.assertEqual(roles[base_slot + 3], "core")
            self.assertEqual(roles[base_slot + 4], "core")

    def test_strict_cutoff_excludes_target_future_unranked_and_leaver(self):
        rows = [
            match(99, 1000),
            match(100, 1100),
            match(101, 1200),
            {**match(98, 900), "lobby_type": 0},
            {**match(97, 800), "leaver_status": 1},
        ]
        selected = strict_history(rows, cutoff_match_id=100, cutoff_start_time=4000)
        self.assertEqual([row["match_id"] for row in selected], [99])

    def test_build_snapshot_scores_complete_public_histories(self):
        histories = {
            1: [match(900 - index, 200000 - index * 3000, deaths=3 + index % 3) for index in range(30)],
            2: [match(800 - index, 200000 - index * 3000, win=index % 3 == 0, deaths=8 + index % 4, gpm=650) for index in range(30)],
        }
        snapshot = build_snapshot(
            histories,
            cutoff_match_id=1000,
            cutoff_start_time=300000,
            participant_meta={
                1: {"relation": "self", "position_est": 1},
                2: {"relation": "enemy", "position_est": 5},
            },
            generated_at=123,
        )
        self.assertTrue(snapshot["cutoff"]["target_match_excluded"])
        self.assertFalse(snapshot["data_scope"]["replay_parse_used_for_history"])
        self.assertFalse(snapshot["data_scope"]["role_normalized"])
        self.assertEqual(snapshot["benchmark"]["eligible_players"], 2)
        for profile in snapshot["profiles"]:
            self.assertEqual(profile["sample_size"], 20)
            self.assertIsNotNone(profile["scores"])
            self.assertIsNotNone(profile["scores"]["S"])
            self.assertIsNotNone(profile["scores"]["L"])
            self.assertIsNotNone(profile["scores"]["F"])
            self.assertIsNotNone(profile["scores"]["F5"])
            self.assertIsNotNone(profile["scores"]["C"])
            self.assertNotIn("M", profile["scores"])
            self.assertNotIn("current_match", profile)
            self.assertEqual(profile["role"]["status"], "unavailable")
            self.assertTrue(all(match_id < 1000 for match_id in profile["sample_match_ids"]))
        self.assertFalse(snapshot["data_scope"]["current_match_performance_used"])
        self.assertFalse(snapshot["data_scope"]["current_match_position_used"])
        self.assertEqual(snapshot["composition_weights"]["historical_risk_L20"], 0.30)
        self.assertEqual(snapshot["composition_weights"]["recent_risk_from_F10"], 0.50)
        self.assertEqual(snapshot["composition_weights"]["short_term_risk_from_F5"], 0.20)
        self.assertEqual(snapshot["historical_risk_weights"]["nonlinear_result_risk"], 0.50)
        self.assertEqual(snapshot["historical_risk_weights"]["death_risk"], 0.05)
        self.assertEqual(snapshot["historical_risk_weights"]["volatility"], 0.00)

    def test_public_risk_scale_expands_raw_30_to_70(self):
        self.assertEqual(_display_risk(30), 0)
        self.assertEqual(_display_risk(50), 50)
        self.assertEqual(_display_risk(70), 100)
        self.assertEqual(_display_risk(10), 0)
        self.assertEqual(_display_risk(90), 100)

    def test_h20_result_risk_rewards_each_extra_win_more(self):
        self.assertEqual(_nonlinear_result_risk(50), 50)
        self.assertEqual(_nonlinear_result_risk(55), 43)
        self.assertEqual(_nonlinear_result_risk(60), 34)
        self.assertEqual(_nonlinear_result_risk(65), 23)
        self.assertEqual(_nonlinear_result_risk(70), 10)
        self.assertEqual(_nonlinear_result_risk(45), 57)

    def test_h20_is_equal_weight_while_f10_tracks_recent_wins(self):
        recent_bad = [
            match(900 - index, 200000 - index * 3000, win=index >= 10)
            for index in range(20)
        ]
        recent_good = [
            match(900 - index, 200000 - index * 3000, win=index < 10)
            for index in range(20)
        ]
        snapshot = build_snapshot(
            {1: recent_bad, 2: recent_good},
            cutoff_match_id=1000,
            cutoff_start_time=300000,
            generated_at=123,
        )
        by_id = {profile["account_id"]: profile for profile in snapshot["profiles"]}
        self.assertEqual(by_id[1]["scores"]["L"], by_id[2]["scores"]["L"])
        self.assertLess(by_id[1]["scores"]["F"], by_id[2]["scores"]["F"])
        self.assertEqual(snapshot["benchmark"]["history_weighting"], "equal")
        self.assertEqual(snapshot["benchmark"]["recent_weighting"], "equal")

    def test_recent_state_has_no_death_safety_weight(self):
        low_deaths = [
            match(900 - index, 200000 - index * 3000, deaths=2)
            for index in range(20)
        ]
        high_deaths = [
            match(800 - index, 200000 - index * 3000, deaths=12)
            for index in range(20)
        ]
        snapshot = build_snapshot(
            {1: low_deaths, 2: high_deaths},
            cutoff_match_id=1000,
            cutoff_start_time=300000,
            generated_at=123,
        )
        by_id = {profile["account_id"]: profile for profile in snapshot["profiles"]}
        self.assertEqual(by_id[1]["scores"]["F"], by_id[2]["scores"]["F"])
        self.assertEqual(by_id[1]["scores"]["F5"], by_id[2]["scores"]["F5"])
        self.assertEqual(snapshot["state_score_weights"]["win_rate"], 0.40)
        self.assertEqual(snapshot["state_score_weights"]["death_safety"], 0.0)

    def test_private_history_does_not_receive_fake_scores(self):
        snapshot = build_snapshot(
            {42: []},
            cutoff_match_id=1000,
            cutoff_start_time=20000,
            generated_at=123,
        )
        profile = snapshot["profiles"][0]
        self.assertEqual(profile["confidence"], "insufficient")
        self.assertIsNone(profile["scores"]["L"])
        self.assertIsNone(profile["scores"]["C"])
        self.assertEqual(profile["tags"], ["历史数据不足"])

    def test_profile_contract_contains_no_current_match_layer(self):
        histories = {
            1: [match(900 - index, 200000 - index * 3000) for index in range(30)],
        }
        snapshot = build_snapshot(
            histories,
            cutoff_match_id=1000,
            cutoff_start_time=300000,
            generated_at=123,
        )
        profile = snapshot["profiles"][0]
        self.assertEqual(set(profile["scores"]), {"S", "L", "F", "F5", "C"})
        self.assertNotIn("current_match", profile)
        self.assertNotIn("current_match_risk_from_M", snapshot["composition_weights"])

    def test_metrics_use_core_and_support_cohorts_when_available(self):
        histories = {}
        for account_id, role_group in ((1, "core"), (2, "core"), (3, "support"), (4, "support")):
            histories[account_id] = [
                {
                    **match(900 - index, 200000 - index * 3000),
                    "role_group": role_group,
                }
                for index in range(30)
            ]
        snapshot = build_snapshot(
            histories,
            cutoff_match_id=1000,
            cutoff_start_time=300000,
            generated_at=123,
        )
        self.assertTrue(snapshot["data_scope"]["role_normalized"])
        self.assertEqual(snapshot["benchmark"]["role_cohorts"], ["core", "support"])
        for profile in snapshot["profiles"]:
            self.assertTrue(profile["role"]["normalized"])
            self.assertIn(profile["role"]["primary_group"], {"core", "support"})


if __name__ == "__main__":
    unittest.main()
