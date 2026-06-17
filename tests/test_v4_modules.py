"""Tests for v4 modules: execution (conservative fill + cost), stats, risk, and
the new tailpicker behaviors (fundamentals cap, portfolio caps, standard 量比)."""

import importlib.util
import math
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exec = _load("execution_v4", "execution.py")

    def _bars(self, opens_closes):
        return [{"时间": f"2026-06-18 {h}:00:00", "开盘": o, "收盘": c,
                 "最高": max(o, c), "最低": min(o, c), "成交量": 100, "成交额": 100 * c}
                for h, (o, c) in opens_closes]

    def test_gap_up_open_exits_at_open(self):
        bars = self._bars([("09:30", (10.50, 10.50))])  # +5% gap
        r = self.exec.simulate_exit(bars, entry=10.0, open_return=0.05, cost=self.exec.CostModel())
        self.assertEqual(r["exit_reason"], "高开≥+0.5%，开盘获利了结")
        self.assertTrue(r["gap_exit"])
        # net must be below gross because of costs
        self.assertLess(r["actual_return_pct"], r["gross_return_pct"])

    def test_gap_down_open_stops_at_open(self):
        bars = self._bars([("09:30", (9.40, 9.40))])  # -6% gap
        r = self.exec.simulate_exit(bars, entry=10.0, open_return=-0.06, cost=self.exec.CostModel())
        self.assertTrue(r["gap_exit"])
        self.assertLess(r["actual_return_pct"], 0)

    def test_take_profit_requires_bar_close_confirm(self):
        # Bar spikes to +0.6% intraday but CLOSES back at +0.2% -> no TP fill.
        bars = self._bars([("09:30", (10.02, 10.02)), ("10:00", (10.06, 10.02))])
        r = self.exec.simulate_exit(bars, entry=10.0, open_return=0.002, cost=self.exec.CostModel())
        self.assertNotIn("盘中达到", r["exit_reason"])
        # Time exit instead (not a take-profit fill).
        self.assertIn("时间止损", r["exit_reason"])

    def test_take_profit_fills_when_bar_closes_above_target(self):
        bars = self._bars([("09:30", (10.02, 10.02)), ("10:00", (10.06, 10.06))])
        r = self.exec.simulate_exit(bars, entry=10.0, open_return=0.002, cost=self.exec.CostModel())
        self.assertIn("止盈", r["exit_reason"])
        self.assertAlmostEqual(r["exit_price"], 10.0 * 1.005, places=3)

    def test_cost_model_includes_stamp_duty_on_sell_only(self):
        cm = self.exec.CostModel()
        buy = cm.buy_cost(10.0)
        sell = cm.sell_cost(10.0)
        self.assertGreater(sell, buy)  # stamp duty only on sell


class StatsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stats = _load("stats_v4", "stats.py")

    def test_wilson_ci_bounds(self):
        ci = self.stats.wilson_ci(13, 17)
        self.assertAlmostEqual(ci["point"], 13 / 17, places=3)
        self.assertLess(ci["lower"], ci["point"])
        self.assertGreater(ci["upper"], ci["point"])
        self.assertEqual(ci["n"], 17)

    def test_summarize_reports_distribution_and_drawdown(self):
        s = self.stats.summarize([0.5, -1.0, 0.3, 0.8, -0.2])
        self.assertIn("max_drawdown_pct", s)
        self.assertIn("sharpe_annualized", s)
        self.assertIn("t_stat", s)
        self.assertEqual(s["trade_count"], 5)
        self.assertLessEqual(s["worst_return_pct"], -1.0)

    def test_attribution_groups_by_grade(self):
        attr = self.stats.attribution([
            {"grade": "A", "actual_return_pct": 0.5},
            {"grade": "A", "actual_return_pct": -0.3},
            {"grade": "B", "actual_return_pct": 0.2},
        ])
        self.assertEqual(attr["by_grade"]["A"]["n"], 2)
        self.assertEqual(attr["by_grade"]["B"]["n"], 1)


class RiskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.risk = _load("risk_v4", "risk.py")

    def test_sector_cap_prevents_same_industry_pileup(self):
        orders = [
            {"code": "600999", "sector": "证券", "position_pct": 10},
            {"code": "600958", "sector": "证券", "position_pct": 10},
            {"code": "600030", "sector": "证券", "position_pct": 10},
            {"code": "601318", "sector": "保险", "position_pct": 10},
        ]
        caps = self.risk.PortfolioCaps(max_total_position_pct=40, max_per_name_pct=12,
                                       max_per_sector_pct=24, max_names=5)
        admitted = self.risk.apply_portfolio_constraints(orders, "range", caps)
        sector_total = sum(o["position_pct"] for o in admitted if o["sector"] == "证券")
        self.assertLessEqual(sector_total, 24.0 + 0.01)
        self.assertGreaterEqual(len(admitted), 2)  # at least 2 证券 + the 保险

    def test_bear_state_tightens_caps(self):
        caps = self.risk.PortfolioCaps.for_state("bear")
        self.assertLessEqual(caps.max_total_position_pct, 20)
        self.assertLessEqual(caps.max_names, 3)

    def test_halt_state_admits_nothing(self):
        admitted = self.risk.apply_portfolio_constraints(
            [{"code": "600999", "sector": "证券", "position_pct": 10}], "halt")
        self.assertEqual(admitted, [])


class TailpickerV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tp = _load("tailpicker_v4", "tailpicker.py")

    def _stock(self, **overrides):
        base = {
            "code": "600999", "name": "600999", "sector": "证券", "price": 10.0,
            "pre_close": 9.9, "tail_gain_pct": 1.2, "volume_ratio": 1.9,
            "tail_vol_ratio": 0.22, "turnover_rate": 3.0, "amount_mn": 1200,
            "market_cap_bn": 500, "pe": None, "ma_state": "range", "pattern": "breakout",
            "capital_flow_score": 72, "sector_score": 20, "news_sentiment": 0,
            "hot_rank": None, "day_position_pct": 70, "price_to_day_high_pct": -1.0,
            "last_bar_vol_share_tail_pct": 8,
        }
        base.update(overrides)
        return base

    def test_fundamentals_missing_caps_grade_to_b(self):
        market = {"state": "range"}
        # Same strong-A stock, but enrichment flagged fundamentals missing.
        decision = self.tp.score_stock(self._stock(fundamentals_status="missing"), market, "14:20", "akshare")
        self.assertIsNotNone(decision)
        self.assertEqual(decision.grade, "B")

    def test_fundamentals_available_keeps_grade_a(self):
        market = {"state": "range"}
        decision = self.tp.score_stock(self._stock(), market, "14:20", "akshare")
        self.assertEqual(decision.grade, "A")

    def test_portfolio_caps_applied_in_select_final_orders(self):
        market = {"state": "range"}
        decisions = []
        for i in range(6):
            d = self.tp.score_stock(self._stock(code=f"6009{i}9"), market, "14:50", "akshare")
            self.assertIsNotNone(d)
            decisions.append(d)
        orders = self.tp.select_final_orders(decisions, "14:50", "akshare", "range", apply_caps=True)
        total = sum(o["position_pct"] for o in orders)
        self.assertLessEqual(total, 40.0 + 0.01)  # range cap

    def test_standard_volume_ratio_helper_falls_back_to_proxy(self):
        class NoHistoryBackend:
            def daily_history(self, code, end_date, n):
                return None
        vr, source = self.tp._compute_standard_volume_ratio(
            NoHistoryBackend(), "600999", "2026-06-17", "14:20", 100000.0, 1.5)
        self.assertEqual(source, "proxy_tail_early")
        self.assertAlmostEqual(vr, 1.5, places=2)

    def test_trading_minutes_elapsed_excludes_lunch(self):
        # 09:30-11:30 = 120 min; 13:00-14:20 = 80 min -> 200
        self.assertEqual(self.tp.trading_minutes_elapsed("14:20"), 200)
        self.assertEqual(self.tp.trading_minutes_elapsed("11:30"), 120)

    def test_build_full_universe_excludes_688_st_banks_agriculture(self):
        class StubBackend:
            def all_codes(self):
                return [
                    {"code": "600000", "name": "浦发银行"},      # bank -> excluded
                    {"code": "600519", "name": "贵州茅台"},      # kept
                    {"code": "600703", "name": "三安光电"},      # kept
                    {"code": "600354", "name": "敦煌种业"},      # 种业 keyword -> excluded
                    {"code": "600259", "name": "ST某某"},        # ST -> excluded
                    {"code": "688981", "name": "中芯国际"},      # STAR -> excluded
                    {"code": "600999", "name": "招商证券"},      # kept
                    {"code": "600965", "name": "某某养殖"},      # 养殖 keyword -> excluded
                ]
        u = self.tp.build_full_universe(StubBackend(), enrichment=None)
        codes = set(u)
        self.assertIn("600519", codes)
        self.assertIn("600703", codes)
        self.assertIn("600999", codes)
        self.assertNotIn("600000", codes)   # bank
        self.assertNotIn("688981", codes)   # STAR
        self.assertNotIn("600259", codes)   # ST
        self.assertNotIn("600965", codes)   # 养殖 keyword
        self.assertEqual(len(codes), 3)

    def test_build_full_universe_uses_spot_sector_when_available(self):
        class StubBackend:
            def all_codes(self):
                return [{"code": "600000", "name": "浦发银行"}]
        enr = {"stock_map": {
            "600519": {"name": "贵州茅台", "sector": "白酒", "is_st": False, "market_cap_bn": 16400, "amount_mn": 5200},
            "600000": {"name": "浦发银行", "sector": "银行", "is_st": False, "market_cap_bn": 1000, "amount_mn": 2000},
            "600016": {"name": "民生银行", "sector": "银行", "is_st": False, "market_cap_bn": 1000, "amount_mn": 2000},
        }}
        u = self.tp.build_full_universe(StubBackend(), enr)
        self.assertEqual(u, ["600519"])  # banks excluded by sector


if __name__ == "__main__":
    unittest.main()
