import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skill"
SCRIPT_PATH = SKILL_DIR / "scripts" / "tailpicker.py"


class TailpickerSkillTests(unittest.TestCase):
    def test_skill_metadata_and_data_sources_are_declared(self):
        skill_md = SKILL_DIR / "SKILL.md"
        self.assertTrue(skill_md.exists(), "SKILL.md should exist")
        text = skill_md.read_text(encoding="utf-8")
        for required in [
            "a-share-tailpicker",
            "C 版",
            "AKShare",
            "stock_zh_a_hist_min_em",
            "stock_news_em",
            "科创板",
            "14:20",
            "交叉验证",
        ]:
            self.assertIn(required, text)

    def test_script_filters_non_c_version_universe(self):
        module = self._load_script()
        self.assertTrue(module.is_c_version_universe("600519"))
        self.assertTrue(module.is_c_version_universe("601318"))
        self.assertFalse(module.is_c_version_universe("688981"), "科创板必须过滤")
        self.assertFalse(module.is_c_version_universe("300750"), "创业板不属于 C 版默认 60 主板")
        self.assertFalse(module.is_c_version_universe("000001"), "C 版默认只做 60 开头沪主板")

    def test_live_sector_resonance_is_computed_from_same_day_pool(self):
        module = self._load_script()
        stocks = [
            {"code": "600111", "sector": "有色金属", "tail_gain_pct": 1.2, "volume_ratio": 1.7, "pattern": "breakout"},
            {"code": "601899", "sector": "有色金属", "tail_gain_pct": 0.9, "volume_ratio": 1.4, "pattern": "pullback"},
            {"code": "600519", "sector": "白酒", "tail_gain_pct": 0.1, "volume_ratio": 0.8, "pattern": "none"},
        ]
        module.apply_live_sector_resonance(stocks)
        self.assertGreaterEqual(stocks[0]["sector_score"], 14)
        self.assertGreaterEqual(stocks[1]["sector_score"], 14)
        self.assertLess(stocks[2]["sector_score"], 8)

    def test_live_1420_rejects_non_s_grade_as_watchlist_only(self):
        module = self._load_script()
        market = {"state": "range", "breadth_up_ratio": None, "index_tail_return_pct": None}
        stock = {
            "code": "600958",
            "name": "600958",
            "sector": "证券",
            "price": 10.0,
            "pre_close": 9.9,
            "tail_gain_pct": 0.8,
            "volume_ratio": 2.8,
            "tail_vol_ratio": 0.24,
            "turnover_rate": 3.0,
            "amount_mn": 1200,
            "market_cap_bn": 500,
            "pe": None,
            "ma_state": "range",
            "pattern": "breakout",
            "capital_flow_score": 60,
            "sector_score": 20,
            "news_sentiment": 0,
            "hot_rank": None,
        }
        decision = module.score_stock(stock, market, "14:20", "akshare")
        self.assertIsNotNone(decision)
        self.assertEqual([], module.select_final_orders([decision], "14:20", "akshare"))

    def test_live_1420_accepts_strong_a_when_not_overheated(self):
        module = self._load_script()
        market = {"state": "range", "breadth_up_ratio": None, "index_tail_return_pct": None}
        stock = {
            "code": "600999",
            "name": "600999",
            "sector": "证券",
            "price": 10.0,
            "pre_close": 9.9,
            "tail_gain_pct": 0.7,
            "volume_ratio": 1.9,
            "tail_vol_ratio": 0.22,
            "turnover_rate": 3.0,
            "amount_mn": 1200,
            "market_cap_bn": 500,
            "pe": None,
            "ma_state": "range",
            "pattern": "breakout",
            "capital_flow_score": 72,
            "sector_score": 20,
            "news_sentiment": 0,
            "hot_rank": None,
            "day_position_pct": 82,
            "price_to_day_high_pct": -0.8,
            "last_bar_vol_share_tail_pct": 8,
        }
        decision = module.score_stock(stock, market, "14:20", "akshare")
        self.assertIsNotNone(decision)
        self.assertEqual(decision.grade, "A")

    def test_watchlist_captures_scored_non_final_candidates(self):
        module = self._load_script()
        market = {"state": "range", "breadth_up_ratio": None, "index_tail_return_pct": None}
        stock = {
            "code": "600958",
            "name": "600958",
            "sector": "证券",
            "price": 10.0,
            "pre_close": 9.9,
            "tail_gain_pct": 0.8,
            "volume_ratio": 2.8,
            "tail_vol_ratio": 0.24,
            "turnover_rate": 3.0,
            "amount_mn": 1200,
            "market_cap_bn": 500,
            "pe": None,
            "ma_state": "range",
            "pattern": "breakout",
            "capital_flow_score": 60,
            "sector_score": 20,
            "news_sentiment": 0,
            "hot_rank": None,
            "day_position_pct": 82,
            "price_to_day_high_pct": -0.8,
            "last_bar_vol_share_tail_pct": 8,
        }
        decision = module.score_stock(stock, market, "14:20", "akshare")
        watchlist = module.build_watchlist([decision], [stock], [], "14:20", "akshare")

        self.assertEqual("600958", watchlist[0]["code"])
        self.assertEqual("observe", watchlist[0]["status"])
        self.assertIn("blockers", watchlist[0])
        self.assertIn("upgrade_triggers", watchlist[0])

    def test_live_1450_rejects_near_intraday_high(self):
        module = self._load_script()
        market = {"state": "range", "breadth_up_ratio": None, "index_tail_return_pct": None}
        stock = {
            "code": "600999",
            "name": "600999",
            "sector": "证券",
            "price": 10.0,
            "pre_close": 9.9,
            "tail_gain_pct": 0.7,
            "volume_ratio": 1.9,
            "tail_vol_ratio": 0.22,
            "turnover_rate": 3.0,
            "amount_mn": 1200,
            "market_cap_bn": 500,
            "pe": None,
            "ma_state": "range",
            "pattern": "breakout",
            "capital_flow_score": 72,
            "sector_score": 20,
            "news_sentiment": 0,
            "hot_rank": None,
            "day_position_pct": 82,
            "price_to_day_high_pct": -0.6,
            "last_bar_vol_share_tail_pct": 8,
        }
        decision = module.score_stock(stock, market, "14:50", "akshare")
        self.assertIsNotNone(decision)
        orders = module.select_final_orders([decision], "14:50", "akshare")
        self.assertEqual([], orders)

    def test_live_1450_accepts_pullback_below_intraday_high(self):
        module = self._load_script()
        market = {"state": "range", "breadth_up_ratio": None, "index_tail_return_pct": None}
        stock = {
            "code": "600999",
            "name": "600999",
            "sector": "证券",
            "price": 10.0,
            "pre_close": 9.9,
            "tail_gain_pct": 0.7,
            "volume_ratio": 1.9,
            "tail_vol_ratio": 0.22,
            "turnover_rate": 3.0,
            "amount_mn": 1200,
            "market_cap_bn": 500,
            "pe": None,
            "ma_state": "range",
            "pattern": "breakout",
            "capital_flow_score": 72,
            "sector_score": 20,
            "news_sentiment": 0,
            "hot_rank": None,
            "day_position_pct": 82,
            "price_to_day_high_pct": -0.8,
            "last_bar_vol_share_tail_pct": 8,
        }
        decision = module.score_stock(stock, market, "14:50", "akshare")
        self.assertIsNotNone(decision)
        orders = module.select_final_orders([decision], "14:50", "akshare")
        self.assertEqual("600999", orders[0]["code"])
        self.assertEqual("A", orders[0]["grade"])

    def test_live_1450_final_gate_does_not_backfill_lower_ranked_candidates(self):
        module = self._load_script()
        market = {"state": "range", "breadth_up_ratio": None, "index_tail_return_pct": None}

        def stock(code, capital_flow_score, sector_score, price_to_day_high_pct):
            return {
                "code": code,
                "name": code,
                "sector": "证券",
                "price": 10.0,
                "pre_close": 9.9,
                "tail_gain_pct": 0.8,
                "volume_ratio": 2.4,
                "tail_vol_ratio": 0.22,
                "turnover_rate": 3.0,
                "amount_mn": 1200,
                "market_cap_bn": 500,
                "pe": None,
                "ma_state": "bull",
                "pattern": "breakout",
                "capital_flow_score": capital_flow_score,
                "sector_score": sector_score,
                "news_sentiment": 0,
                "hot_rank": None,
                "day_position_pct": 82,
                "price_to_day_high_pct": price_to_day_high_pct,
                "last_bar_vol_share_tail_pct": 8,
            }

        high_ranked_near_high = [
            stock(f"60099{i}", 90 - i, 20, -0.6)
            for i in range(5)
        ]
        lower_ranked_pullback = stock("600489", 64, 14, -1.2)
        decisions = [
            module.score_stock(item, market, "14:50", "akshare")
            for item in high_ranked_near_high + [lower_ranked_pullback]
        ]
        decisions = [decision for decision in decisions if decision]

        self.assertEqual(6, len(decisions))
        orders = module.select_final_orders(decisions, "14:50", "akshare")
        self.assertEqual([], orders)

    def test_fixture_screen_outputs_reasons_prices_and_exit_plan(self):
        report = self._run_fixture("screen")
        self.assertEqual(report["mode"], "screen")
        self.assertEqual(report["asof_time"], "14:20")
        self.assertIn(report["market_state"]["state"], {"bull", "range", "bear", "halt"})
        self.assertGreaterEqual(len(report["final_orders"]), 1)
        for order in report["final_orders"]:
            self.assertRegex(order["code"], r"^60")
            self.assertNotRegex(order["code"], r"^688")
            self.assertGreater(len(order["reasons"]), 2)
            self.assertGreater(order["suggested_price"], 0)
            self.assertIn("next_day_plan", order)
            self.assertIn("cross_validation", order)

    def test_fixture_screen_outputs_watchlist_and_market_notes(self):
        report = self._run_fixture("screen")
        self.assertIn("watchlist", report)
        self.assertIn("market_notes", report)
        self.assertIsInstance(report["watchlist"], list)
        self.assertIsInstance(report["market_notes"], list)

    def test_fixture_backtest_verifies_against_actual_results(self):
        report = self._run_fixture("backtest")
        self.assertEqual(report["mode"], "backtest")
        self.assertGreaterEqual(len(report["daily_results"]), 3)
        self.assertIn("retrospective", report)
        for row in report["daily_results"]:
            self.assertIn("trade_date", row)
            self.assertIn("actual_return_pct", row)
            self.assertIn("verification", row)

    def test_backtest_has_fetch_timeout_option(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "backtest", "--help"],
            check=True,
            text=True,
            capture_output=True,
        )
        self.assertIn("--fetch-timeout", completed.stdout)
        self.assertIn("--no-eastmoney-fallback", completed.stdout)

    def _load_script(self):
        self.assertTrue(SCRIPT_PATH.exists(), "tailpicker.py should exist")
        spec = importlib.util.spec_from_file_location("tailpicker", SCRIPT_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules["tailpicker"] = module
        spec.loader.exec_module(module)
        return module

    def _run_fixture(self, command):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / f"{command}.json"
            fixture = SKILL_DIR / "references" / "fixture_market_week.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_PATH),
                    command,
                    "--fixture",
                    str(fixture),
                    "--asof-time",
                    "14:20",
                    "--output",
                    str(output),
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            self.assertIn(str(output), completed.stdout)
            return json.loads(output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
