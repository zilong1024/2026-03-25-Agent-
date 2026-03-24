import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..base import BaseStep


class MarketBarsStep(BaseStep):
    async def execute(self, config: Dict[str, Any], context: Any) -> Dict[str, Any]:
        symbols = self._parse_symbols(config.get("symbols"))
        if not symbols:
            raise ValueError("market_bars requires a non-empty symbols list")

        lookback_days = self._parse_lookback(config.get("lookback_days", 5))
        return self._load_bars(symbols, lookback_days)

    def _load_bars(self, symbols: List[str], lookback_days: int) -> Dict[str, Any]:
        fallback = self._load_fallback_dataset()
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        errors: Dict[str, str] = {}

        code_map = {symbol: self._to_baostock_code(symbol) for symbol in symbols}
        symbols_with_codes = [symbol for symbol, code in code_map.items() if code is not None]

        session_error: Optional[str] = None
        bs = None
        if symbols_with_codes:
            try:
                import baostock as bs  # type: ignore
            except Exception as exc:
                session_error = "BaoStock import failed: {0}".format(exc)
            else:
                login_result = bs.login()
                if str(login_result.error_code) != "0":
                    session_error = "BaoStock login failed: {0} {1}".format(
                        login_result.error_code,
                        login_result.error_msg,
                    )

        try:
            for symbol in symbols:
                bars: Optional[List[Dict[str, Any]]] = None
                query_error: Optional[str] = None

                code = code_map[symbol]
                if code is not None and bs is not None and session_error is None:
                    bars, query_error = self._query_daily_bars(bs, code, lookback_days)
                elif code is not None and session_error is not None:
                    query_error = session_error
                elif code is None:
                    query_error = "Unsupported BaoStock symbol format: {0}".format(symbol)

                if bars:
                    grouped[symbol] = bars
                    continue

                fallback_bars = self._fallback_bars(fallback, symbol, lookback_days)
                if fallback_bars is not None:
                    grouped[symbol] = fallback_bars
                    continue

                errors[symbol] = query_error or "No bars returned for symbol"
        finally:
            if bs is not None and session_error is None:
                try:
                    bs.logout()
                except Exception:
                    pass

        if errors:
            details = ", ".join(
                "{0}: {1}".format(symbol, reason) for symbol, reason in sorted(errors.items())
            )
            raise RuntimeError("market_bars query failed for one or more symbols: {0}".format(details))
        return grouped

    def _parse_symbols(self, raw_symbols: Any) -> List[str]:
        if isinstance(raw_symbols, str):
            normalized = raw_symbols.strip()
            return [normalized] if normalized else []
        if not isinstance(raw_symbols, list):
            return []

        symbols: List[str] = []
        for item in raw_symbols:
            text = str(item).strip()
            if text:
                symbols.append(text)
        return symbols

    def _parse_lookback(self, raw_lookback: Any) -> int:
        try:
            parsed = int(raw_lookback)
        except Exception:
            raise ValueError("market_bars lookback_days must be an integer")
        if parsed <= 0:
            raise ValueError("market_bars lookback_days must be positive")
        return parsed

    def _query_daily_bars(
        self,
        bs: Any,
        code: str,
        lookback_days: int,
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
        end = date.today()
        start = end - timedelta(days=max(lookback_days * 4, 30))
        response = bs.query_history_k_data_plus(
            code,
            "date,close",
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            frequency="d",
            adjustflag="3",
        )
        if str(response.error_code) != "0":
            return None, "BaoStock query failed for {0}: {1} {2}".format(
                code,
                response.error_code,
                response.error_msg,
            )

        rows: List[Dict[str, Any]] = []
        while response.next():
            row = response.get_row_data()
            if len(row) < 2:
                continue
            close_value = self._safe_float(row[1])
            if close_value is None:
                continue
            rows.append({"date": row[0], "close": close_value})

        if not rows:
            return None, "BaoStock returned empty rows for {0}".format(code)
        return rows[-lookback_days:], None

    def _safe_float(self, value: Any) -> Optional[float]:
        try:
            return float(value)
        except Exception:
            return None

    def _to_baostock_code(self, symbol: str) -> Optional[str]:
        lowered = symbol.lower()
        if re.match(r"^(sh|sz)\.\d{6}$", lowered):
            return lowered
        if re.match(r"^\d{6}$", lowered):
            prefix = "sh" if lowered[0] in {"5", "6", "9"} else "sz"
            return "{0}.{1}".format(prefix, lowered)
        return None

    def _load_fallback_dataset(self) -> Dict[str, Any]:
        dataset_path = Path(__file__).resolve().parents[3] / "datasets" / "daily_bars.json"
        if not dataset_path.exists():
            return {}
        try:
            with open(dataset_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _fallback_bars(
        self,
        dataset: Dict[str, Any],
        symbol: str,
        lookback_days: int,
    ) -> Optional[List[Dict[str, Any]]]:
        series = dataset.get(symbol)
        if not isinstance(series, list):
            return None

        normalized: List[Dict[str, Any]] = []
        for item in series:
            if not isinstance(item, dict):
                continue
            if "date" not in item or "close" not in item:
                continue
            close_value = self._safe_float(item.get("close"))
            if close_value is None:
                continue
            normalized.append({"date": str(item.get("date")), "close": close_value})

        if not normalized:
            return None
        return normalized[-lookback_days:]
