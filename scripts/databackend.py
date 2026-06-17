"""Multi-source data backend for the tailpicker skill.

Abstracts market-data access behind a single ``DataBackend`` interface so the
strategy layer never talks to a vendor SDK directly. Sources, in priority order:

1. ``IFindBackend``  – activated automatically when ``iFinDPy`` is importable
   (e.g. when the skill runs on Kimi Code). iFinD is preferred when available.
2. ``AkShareBackend`` – wraps multiple free public endpoints (Eastmoney / Sina /
   Tencent / Xueqiu behind the AKShare facade) with per-method fallback.
3. ``EastmoneyHttpBackend`` – direct Eastmoney push2 HTTP fallback for the spot
   snapshot, used when AKShare is rate-limited or structurally changed.

Every result is tagged with ``source`` and persisted to a disk cache keyed by
``(method, args, trade_date)`` so that backtests are reproducible and live scans
survive transient public-API outages.

This module imports AKShare / iFinD lazily so the fixture-only path (and the
unit tests) keep working without any vendor dependency installed.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

CACHE_TTL_SECONDS = 12 * 3600  # half trading day; enrichment rebuilds daily


class DataBackend:
    """Abstract interface. Concrete backends implement the methods they can."""

    name = "abstract"

    def minute_bars(self, code: str, start: str, end: str, period: int = 5) -> Optional[List[Dict[str, Any]]]:
        raise NotImplementedError

    def daily_prev_close(self, code: str, trade_date: str) -> Optional[float]:
        raise NotImplementedError

    def daily_history(self, code: str, end_date: str, n: int) -> Optional[List[Dict[str, Any]]]:
        raise NotImplementedError

    def spot_snapshot(self, trade_date: str) -> Optional[Dict[str, Dict[str, Any]]]:
        raise NotImplementedError

    def index_tail_return(self, trade_date: str, asof_time: str) -> Optional[float]:
        raise NotImplementedError

    def limit_counts(self, trade_date: str) -> Optional[Tuple[int, int]]:
        raise NotImplementedError

    def trade_calendar(self, end_day: date, lookback: int) -> Optional[List[str]]:
        raise NotImplementedError

    def industry_constituents(self) -> Optional[Dict[str, List[str]]]:
        raise NotImplementedError

    def industry_returns(self, trade_date: str) -> Optional[Dict[str, float]]:
        raise NotImplementedError

    def news_sentiment(self, code: str) -> Optional[int]:
        raise NotImplementedError

    def hot_rank(self) -> Optional[Dict[str, int]]:
        raise NotImplementedError

    def all_codes(self) -> Optional[List[Dict[str, str]]]:
        """Full A-share code+name list (e.g. ak.stock_info_a_code_name)."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

class DiskCache:
    """Tiny JSON cache. Keys are stable strings; values are arbitrary JSON."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key)
        return self.root / f"{safe}.json"

    def get(self, key: str, ttl: float = CACHE_TTL_SECONDS) -> Optional[Any]:
        path = self._path(key)
        if not path.exists():
            return None
        if ttl and time.time() - path.stat().st_mtime > ttl:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def set(self, key: str, value: Any) -> None:
        path = self._path(key)
        try:
            path.write_text(json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# AKShare backend (multi-endpoint fallback via the AKShare facade)
# ---------------------------------------------------------------------------

def _import_akshare():
    try:
        import akshare as ak  # type: ignore
        import pandas as pd  # type: ignore
        return ak, pd
    except Exception:
        return None, None


class AkShareBackend(DataBackend):
    name = "akshare"

    def __init__(self, cache: DiskCache, timeout: int = 8, eastmoney_fallback: bool = True):
        self.cache = cache
        self.timeout = timeout
        self.eastmoney_fallback = eastmoney_fallback
        self._minute_cache: Dict[str, Any] = {}

    # -- minute bars: Sina cache first, Eastmoney fallback -----------------
    def minute_bars(self, code: str, start: str, end: str, period: int = 5) -> Optional[List[Dict[str, Any]]]:
        ak, pd = _import_akshare()
        if ak is None:
            return None
        code = _normalize(code)
        cache_key = f"{code}|{start}|{end}|{period}"
        cached = self._minute_cache.get(cache_key)
        if cached is not None:
            return cached

        bars = self._sina_minute(ak, code, period)
        if bars is None or len(bars) == 0:
            if self.eastmoney_fallback:
                bars = self._eastmoney_minute(ak, code, start, end, period)
        if bars is None or len(bars) == 0:
            return None

        # Normalize to a list of dicts with stable Chinese column names.
        rows = []
        for _, row in bars.iterrows():
            rows.append({
                "时间": str(row.get("时间", row.get("day", ""))),
                "开盘": float(row.get("开盘", row.get("open", 0)) or 0),
                "收盘": float(row.get("收盘", row.get("close", 0)) or 0),
                "最高": float(row.get("最高", row.get("high", 0)) or 0),
                "最低": float(row.get("最低", row.get("low", 0)) or 0),
                "成交量": float(row.get("成交量", row.get("volume", 0)) or 0),
                "成交额": float(row.get("成交额", row.get("amount", 0)) or 0),
            })
        # Filter to requested window.
        start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
        filtered = []
        for r in rows:
            try:
                dt = datetime.strptime(r["时间"], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            if start_dt <= dt <= end_dt:
                filtered.append(r)
        result = filtered or rows
        self._minute_cache[cache_key] = result
        return result

    def _sina_minute(self, ak, code: str, period: int):
        try:
            prefix = "sh" if code.startswith("6") else "sz"
            bars = _bounded(lambda: ak.stock_zh_a_minute(symbol=f"{prefix}{code}", period=str(period), adjust=""), self.timeout)
            if bars is None or len(bars) == 0:
                return None
            bars = bars.rename(columns={
                "day": "时间", "open": "开盘", "high": "最高", "low": "最低",
                "close": "收盘", "volume": "成交量", "amount": "成交额",
            })
            bars["时间"] = bars["时间"].astype(str)
            return bars
        except Exception:
            return None

    def _eastmoney_minute(self, ak, code: str, start: str, end: str, period: int):
        try:
            bars = _bounded(
                lambda: ak.stock_zh_a_hist_min_em(symbol=code, start_date=start, end_date=end, period=str(period), adjust=""),
                self.timeout,
            )
            return bars
        except Exception:
            return None

    # -- daily ------------------------------------------------------------
    def daily_prev_close(self, code: str, trade_date: str) -> Optional[float]:
        # Request a few extra days so we always have the previous close even if
        # the source excludes the decision date itself.
        hist = self.daily_history(code, trade_date, 5)
        if not hist:
            return None
        # Sources differ: Eastmoney returns the decision date's row; Sina stops
        # the day before during a live session. Pick the row immediately before
        # trade_date, else the last available row.
        prev_rows = [r for r in hist if str(r.get("date", "")) < trade_date]
        if prev_rows:
            return float(prev_rows[-1]["close"])
        if len(hist) >= 2:
            return float(hist[-2]["close"])
        return None

    def daily_history(self, code: str, end_date: str, n: int) -> Optional[List[Dict[str, Any]]]:
        ak, pd = _import_akshare()
        if ak is None:
            return None
        cache_key = f"daily|{code}|{end_date}|{n}"
        cached = self.cache.get(cache_key, ttl=24 * 3600)
        if cached is not None:
            return cached
        end = datetime.strptime(end_date, "%Y-%m-%d").date()
        start = end - timedelta(days=n * 2 + 10)

        # Primary: Eastmoney daily. Fallback: Sina daily (stock_zh_a_daily),
        # which uses a different host and is often reachable when Eastmoney
        # rate-limits / drops the connection (observed in live environments).
        df = None
        try:
            df = _bounded(
                lambda: ak.stock_zh_a_hist(symbol=_normalize(code), period="daily",
                                           start_date=start.strftime("%Y%m%d"),
                                           end_date=end.strftime("%Y%m%d"), adjust=""),
                self.timeout,
            )
        except Exception:
            df = None
        if df is None or len(df) == 0:
            try:
                prefix = "sh" if _normalize(code).startswith("6") else "sz"
                sina = _bounded(
                    lambda: ak.stock_zh_a_daily(symbol=f"{prefix}{_normalize(code)}",
                                                start_date=start.strftime("%Y%m%d"),
                                                end_date=end.strftime("%Y%m%d"), adjust=""),
                    self.timeout,
                )
                if sina is not None and len(sina) > 0:
                    df = sina.rename(columns={
                        "date": "日期", "open": "开盘", "high": "最高", "low": "最低",
                        "close": "收盘", "volume": "成交量", "amount": "成交额",
                        "turnover": "换手率",
                    })
                    df["日期"] = df["日期"].astype(str)
            except Exception:
                df = None
        if df is None or len(df) == 0:
            return None
        try:
            rows = []
            for _, row in df.iterrows():
                rows.append({
                    "date": str(row.get("日期", "")),
                    "open": float(row.get("开盘", 0) or 0),
                    "close": float(row.get("收盘", 0) or 0),
                    "high": float(row.get("最高", 0) or 0),
                    "low": float(row.get("最低", 0) or 0),
                    "volume": float(row.get("成交量", 0) or 0),
                    "amount": float(row.get("成交额", 0) or 0),
                    "turnover": float(row.get("换手率", 0) or 0),
                })
            rows = rows[-n:] if len(rows) > n else rows
            self.cache.set(cache_key, rows)
            return rows
        except Exception:
            return None

    # -- spot snapshot ----------------------------------------------------
    def spot_snapshot(self, trade_date: str) -> Optional[Dict[str, Dict[str, Any]]]:
        cache_key = f"spot|{trade_date}"
        cached = self.cache.get(cache_key, ttl=6 * 3600)
        if cached is not None:
            return cached
        ak, pd = _import_akshare()
        if ak is None:
            return None
        try:
            df = _bounded(lambda: ak.stock_zh_a_spot_em(), self.timeout)
            if df is None or len(df) == 0:
                return None
            snap: Dict[str, Dict[str, Any]] = {}
            for _, row in df.iterrows():
                code = _normalize(str(row.get("代码", "")))
                if not code:
                    continue
                name = str(row.get("名称", ""))
                snap[code] = {
                    "code": code,
                    "name": name,
                    "price": _f(row.get("最新价")),
                    "pct_change": _f(row.get("涨跌幅")),
                    "amount_mn": _f(row.get("成交额")) / 1_000_000,
                    "turnover_rate": _f(row.get("换手率")),
                    "pe": _f(row.get("市盈率-动态")) or None,
                    "market_cap_bn": _f(row.get("流通市值")) / 1_000_000_000,
                    "volume_ratio": _f(row.get("量比")) or None,
                    "is_st": "ST" in name.upper() or "*ST" in name.upper(),
                }
            self.cache.set(cache_key, snap)
            return snap
        except Exception:
            return None

    # -- market state -----------------------------------------------------
    def index_tail_return(self, trade_date: str, asof_time: str = "14:50") -> Optional[float]:
        # Use intraday index minute bars if available, else daily.
        ak, pd = _import_akshare()
        if ak is None:
            return None
        try:
            bars = _bounded(lambda: ak.stock_zh_a_minute(symbol="sh000001", period="5", adjust=""), self.timeout)
            if bars is None or len(bars) == 0:
                return None
            bars = bars.rename(columns={"day": "时间", "close": "收盘"})
            bars["时间"] = bars["时间"].astype(str)
            asof_dt = datetime.strptime(f"{trade_date} {asof_time}:00", "%Y-%m-%d %H:%M:%S")
            day_rows = []
            for _, r in bars.iterrows():
                try:
                    dt = datetime.strptime(str(r["时间"]), "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                if dt.date() == asof_dt.date() and dt <= asof_dt:
                    day_rows.append(r)
            if len(day_rows) < 2:
                return None
            first = float(day_rows[0]["收盘"])
            last = float(day_rows[-1]["收盘"])
            if not first:
                return None
            return (last / first - 1) * 100
        except Exception:
            return None

    def limit_counts(self, trade_date: str) -> Optional[Tuple[int, int]]:
        ak, pd = _import_akshare()
        if ak is None:
            return None
        try:
            snap = _bounded(lambda: ak.stock_zh_a_spot_em(), self.timeout)
            if snap is None or len(snap) == 0:
                return None
            up = int((snap.get("涨跌幅", pd.Series()) >= 9.9).sum()) if pd is not None else 0
            down = int((snap.get("涨跌幅", pd.Series()) <= -9.9).sum()) if pd is not None else 0
            return up, down
        except Exception:
            return None

    # -- calendar ---------------------------------------------------------
    def trade_calendar(self, end_day: date, lookback: int) -> Optional[List[str]]:
        ak, pd = _import_akshare()
        if ak is None:
            return None
        cache_key = f"cal|{end_day.isoformat()}|{lookback}"
        cached = self.cache.get(cache_key, ttl=7 * 24 * 3600)
        if cached is not None:
            return cached
        try:
            cal = _bounded(lambda: ak.tool_trade_date_hist_sina(), self.timeout)
            col = "trade_date" if "trade_date" in cal.columns else cal.columns[0]
            dates = [pd.to_datetime(x).date() for x in cal[col].tolist()]
            result = [d.isoformat() for d in dates if d <= end_day][-lookback:]
            self.cache.set(cache_key, result)
            return result
        except Exception:
            return None

    # -- industry ---------------------------------------------------------
    def industry_constituents(self) -> Optional[Dict[str, List[str]]]:
        cache_key = "industry_constituents"
        cached = self.cache.get(cache_key, ttl=24 * 3600)
        if cached is not None:
            return cached
        ak, pd = _import_akshare()
        if ak is None:
            return None
        try:
            boards = _bounded(lambda: ak.stock_board_industry_name_em(), self.timeout)
            if boards is None or len(boards) == 0:
                return None
            name_col = "板块名称" if "板块名称" in boards.columns else boards.columns[1]
            result: Dict[str, List[str]] = {}
            for board_name in boards[name_col].tolist()[:120]:  # cap for speed
                try:
                    cons = _bounded(lambda b=board_name: ak.stock_board_industry_cons_em(symbol=b), self.timeout)
                    if cons is None or len(cons) == 0:
                        continue
                    code_col = "代码" if "代码" in cons.columns else cons.columns[1]
                    result[str(board_name)] = [_normalize(str(c)) for c in cons[code_col].tolist()]
                except Exception:
                    continue
            self.cache.set(cache_key, result)
            return result
        except Exception:
            return None

    def industry_returns(self, trade_date: str) -> Optional[Dict[str, float]]:
        cache_key = f"industry_returns|{trade_date}"
        cached = self.cache.get(cache_key, ttl=6 * 3600)
        if cached is not None:
            return cached
        ak, pd = _import_akshare()
        if ak is None:
            return None
        try:
            boards = _bounded(lambda: ak.stock_board_industry_name_em(), self.timeout)
            if boards is None or len(boards) == 0:
                return None
            name_col = "板块名称" if "板块名称" in boards.columns else boards.columns[1]
            pct_col = "涨跌幅" if "涨跌幅" in boards.columns else None
            result: Dict[str, float] = {}
            for _, row in boards.iterrows():
                name = str(row[name_col])
                pct = _f(row.get(pct_col)) if pct_col else 0.0
                result[name] = pct
            self.cache.set(cache_key, result)
            return result
        except Exception:
            return None

    # -- news / sentiment -------------------------------------------------
    def news_sentiment(self, code: str) -> Optional[int]:
        """Return -1/0/+1 from a keyword scan of recent news, or None if unavailable."""
        cache_key = f"news|{code}"
        cached = self.cache.get(cache_key, ttl=6 * 3600)
        if cached is not None:
            return cached
        ak, pd = _import_akshare()
        if ak is None:
            return None
        try:
            df = _bounded(lambda: ak.stock_news_em(symbol=_normalize(code)), self.timeout)
            if df is None or len(df) == 0:
                return None
            negative_kw = ["减持", "预亏", "预减", "问询函", "监管", "立案", "处罚", "解禁", "退市", "风险提示", "业绩下滑", "诉讼", "冻结"]
            positive_kw = ["增持", "回购", "业绩预增", "中标", "合同", "突破", "获批", "利好"]
            text = " ".join(str(t) for t in df.get("新闻标题", df.iloc[:, 0]).tolist()[:20])
            score = 0
            if any(k in text for k in negative_kw):
                score = -1
            elif any(k in text for k in positive_kw):
                score = 1
            self.cache.set(cache_key, score)
            return score
        except Exception:
            return None

    def hot_rank(self) -> Optional[Dict[str, int]]:
        cache_key = "hot_rank"
        cached = self.cache.get(cache_key, ttl=3 * 3600)
        if cached is not None:
            return cached
        ak, pd = _import_akshare()
        if ak is None:
            return None
        try:
            df = _bounded(lambda: ak.stock_hot_rank_em(), self.timeout)
            if df is None or len(df) == 0:
                return None
            code_col = "股票代码" if "股票代码" in df.columns else df.columns[1]
            result = {_normalize(str(c)): i + 1 for i, c in enumerate(df[code_col].tolist())}
            self.cache.set(cache_key, result)
            return result
        except Exception:
            return None

    # -- full code list ---------------------------------------------------
    def all_codes(self) -> Optional[List[Dict[str, str]]]:
        """All A-share code+name via stock_info_a_code_name (lightweight endpoint,
        usually reachable even when Eastmoney spot is rate-limited)."""
        cache_key = "all_codes"
        cached = self.cache.get(cache_key, ttl=24 * 3600)
        if cached is not None:
            return cached
        ak, pd = _import_akshare()
        if ak is None:
            return None
        try:
            df = _bounded(lambda: ak.stock_info_a_code_name(), max(self.timeout, 30))
            if df is None or len(df) == 0:
                return None
            result = [{"code": _normalize(str(c)), "name": str(n)}
                      for c, n in zip(df["code"].tolist(), df["name"].tolist())]
            self.cache.set(cache_key, result)
            return result
        except Exception:
            return None


# ---------------------------------------------------------------------------
# iFinD backend (preferred on Kimi Code)
# ---------------------------------------------------------------------------

class IFindBackend(DataBackend):
    """Thin iFinD backend. Activated only when ``iFinDPy`` is importable.

    iFinD terminal objects are heavy; we implement only the methods that matter
    for live screening and fall back to AKShare for anything not covered.
    """

    name = "ifind"

    def __init__(self, cache: DiskCache, timeout: int = 8, fallback: DataBackend = None):
        self.cache = cache
        self.timeout = timeout
        self.fallback = fallback
        self._client = None

    @staticmethod
    def available() -> bool:
        try:
            import iFinDPy  # type: ignore  # noqa: F401
            return True
        except Exception:
            return False

    def _ensure(self):
        if self._client is not None:
            return self._client
        try:
            import iFinDPy  # type: ignore
            self._client = iFinDPy
            return self._client
        except Exception:
            return None

    def spot_snapshot(self, trade_date: str) -> Optional[Dict[str, Dict[str, Any]]]:
        # iFinD snapshot is preferred; fall back to AKShare if anything fails.
        snap = self._ifind_spot(trade_date)
        if snap is not None:
            return snap
        return self.fallback.spot_snapshot(trade_date) if self.fallback else None

    def _ifind_spot(self, trade_date: str) -> Optional[Dict[str, Dict[str, Any]]]:
        client = self._ensure()
        if client is None:
            return None
        try:
            # iFinD THS_DateSerial / THS_HighFrequenceSequence vary by version;
            # this is a best-effort hook. On any error we return None so the
            # caller falls back to AKShare.
            codes = client.THS_BasicData_basics if hasattr(client, "THS_BasicData_basics") else None
            return None  # placeholder: real deployments inject a configured snapshot
        except Exception:
            return None

    def daily_prev_close(self, code: str, trade_date: str) -> Optional[float]:
        client = self._ensure()
        if client is None:
            return self.fallback.daily_prev_close(code, trade_date) if self.fallback else None
        try:
            # Best-effort; delegate to AKShare which is reliable for daily bars.
            return self.fallback.daily_prev_close(code, trade_date) if self.fallback else None
        except Exception:
            return None

    # Delegate everything else to the AKShare fallback so iFinD only overrides
    # where it genuinely adds value (snapshot / Level-2 capital flow).
    def __getattr__(self, item):
        if self.fallback is not None and hasattr(self.fallback, item):
            return getattr(self.fallback, item)
        raise AttributeError(item)


# ---------------------------------------------------------------------------
# Eastmoney direct HTTP fallback (spot only)
# ---------------------------------------------------------------------------

class EastmoneyHttpBackend(DataBackend):
    name = "eastmoney_http"

    def __init__(self, cache: DiskCache, timeout: int = 8):
        self.cache = cache
        self.timeout = timeout

    def spot_snapshot(self, trade_date: str) -> Optional[Dict[str, Dict[str, Any]]]:
        cache_key = f"spot_http|{trade_date}"
        cached = self.cache.get(cache_key, ttl=6 * 3600)
        if cached is not None:
            return cached
        try:
            import urllib.request  # type: ignore
            import json as _json
            # Eastmoney clist push2 endpoint for Shanghai main board.
            url = ("https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=2000&po=1&np=1"
                   "&fltt=2&invt=2&fid=f3&fs=m:1+t:2&fields=f12,f14,f2,f3,f5,f6,f8,f9,f15,f16,f17")
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                data = _json.loads(resp.read().decode("utf-8"))
            rows = data.get("data", {}).get("diff", []) or []
            snap: Dict[str, Dict[str, Any]] = {}
            for r in rows:
                code = _normalize(str(r.get("f12", "")))
                name = str(r.get("f14", ""))
                snap[code] = {
                    "code": code,
                    "name": name,
                    "price": _f(r.get("f2")),
                    "pct_change": _f(r.get("f3")),
                    "amount_mn": _f(r.get("f6")) / 1_000_000,
                    "turnover_rate": _f(r.get("f8")),
                    "pe": _f(r.get("f9")) or None,
                    "market_cap_bn": None,
                    "volume_ratio": _f(r.get("f10")) or None,
                    "is_st": "ST" in name.upper(),
                }
            self.cache.set(cache_key, snap)
            return snap
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Composite: iFinD -> AKShare -> Eastmoney HTTP, with disk caching
# ---------------------------------------------------------------------------

class CompositeBackend(DataBackend):
    """Tries each source in priority order; returns first non-null, cached.

    Caching policy: only **raw fetch** methods are cached by ``_call`` (their
    result is the ground truth from a remote source). **Derived** methods
    (``daily_prev_close``) compose cached primitives and are recomputed every
    call — caching their scalar output would freeze a stale value when an
    upstream source's semantics change (observed in live: Sina vs Eastmoney
    differ on whether the decision date's row is returned).
    """

    name = "composite"

    # Methods whose result is a direct remote fetch -> safe to cache.
    _RAW_CACHEABLE = {
        "minute_bars", "daily_history", "spot_snapshot", "index_tail_return",
        "limit_counts", "trade_calendar", "industry_constituents",
        "industry_returns", "news_sentiment", "hot_rank", "all_codes",
    }

    def __init__(self, cache_dir: Path, timeout: int = 8, eastmoney_fallback: bool = True):
        self.cache = DiskCache(Path(cache_dir))
        self.timeout = timeout
        self.sources: List[DataBackend] = []
        if IFindBackend.available():
            ak_backend = AkShareBackend(self.cache, timeout, eastmoney_fallback)
            em_http = EastmoneyHttpBackend(self.cache, timeout)
            ak_backend._em_http = em_http  # type: ignore[attr-defined]
            self.sources.append(IFindBackend(self.cache, timeout, fallback=ak_backend))
            self.sources.append(ak_backend)
            self.sources.append(em_http)
        else:
            ak_backend = AkShareBackend(self.cache, timeout, eastmoney_fallback)
            ak_backend._em_http = EastmoneyHttpBackend(self.cache, timeout)  # type: ignore[attr-defined]
            self.sources.append(ak_backend)
            self.sources.append(ak_backend._em_http)  # type: ignore[attr-defined]

    def _call(self, method: str, *args, **kwargs):
        ttl = kwargs.pop("_ttl", CACHE_TTL_SECONDS)
        cacheable = method in self._RAW_CACHEABLE
        cache_key = f"{method}|{json.dumps(args, default=str)}|{json.dumps(kwargs, default=str)}"
        if cacheable:
            cached = self.cache.get(cache_key, ttl=ttl)
            if cached is not None:
                return cached, "cache"
        last_err = None
        for src in self.sources:
            fn = getattr(src, method, None)
            if fn is None:
                continue
            try:
                result = fn(*args, **kwargs)
            except Exception as exc:  # one source failing must not break the scan
                last_err = exc
                result = None
            if result is not None:
                if cacheable:
                    self.cache.set(cache_key, result)
                return result, src.name
        return None, last_err or "all-sources-empty"

    # Delegate each interface method through _call so caching + fallback apply.
    def minute_bars(self, code, start, end, period=5):
        return self._call("minute_bars", code, start, end, period)[0]

    def daily_prev_close(self, code, trade_date):
        # Derived: NOT cached by _call. Recomputes via cached daily_history.
        return self._call("daily_prev_close", code, trade_date)[0]

    def daily_history(self, code, end_date, n):
        return self._call("daily_history", code, end_date, n, _ttl=24 * 3600)[0]

    def spot_snapshot(self, trade_date):
        return self._call("spot_snapshot", trade_date, _ttl=6 * 3600)[0]

    def index_tail_return(self, trade_date, asof_time="14:50"):
        return self._call("index_tail_return", trade_date, asof_time)[0]

    def limit_counts(self, trade_date):
        return self._call("limit_counts", trade_date)[0]

    def trade_calendar(self, end_day, lookback):
        return self._call("trade_calendar", end_day, lookback, _ttl=7 * 24 * 3600)[0]

    def industry_constituents(self):
        return self._call("industry_constituents", _ttl=24 * 3600)[0]

    def industry_returns(self, trade_date):
        return self._call("industry_returns", trade_date, _ttl=6 * 3600)[0]

    def news_sentiment(self, code):
        return self._call("news_sentiment", code, _ttl=6 * 3600)[0]

    def hot_rank(self):
        return self._call("hot_rank", _ttl=3 * 3600)[0]

    def all_codes(self):
        return self._call("all_codes", _ttl=24 * 3600)[0]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _normalize(code: str) -> str:
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return digits.zfill(6)


def _f(value) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bounded(func, timeout: int):
    """Run func with a timeout. Uses signal on Unix; on other platforms runs bare."""
    if timeout <= 0:
        return func()
    try:
        import signal
        def handler(_s, _f):
            raise TimeoutError(f"data fetch exceeded {timeout}s")
        previous = signal.signal(signal.SIGALRM, handler)
        signal.alarm(int(timeout))
        try:
            return func()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)
    except Exception:
        # signal not available (e.g. non-main thread / Windows) – run bare.
        return func()


_default_backend: Optional[CompositeBackend] = None


def get_backend(cache_dir: Optional[Path] = None, timeout: int = 8, eastmoney_fallback: bool = True) -> CompositeBackend:
    global _default_backend
    if _default_backend is None or cache_dir is not None:
        root = Path(cache_dir or (Path.home() / ".claude" / "skills" / "a-share-tailpicker" / "cache"))
        _default_backend = CompositeBackend(root, timeout, eastmoney_fallback)
    return _default_backend
