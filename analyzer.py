import yfinance as yf
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
import datetime

try:
    from kis_api import fetch_supply_data
    KIS_AVAILABLE = True
except ImportError:
    KIS_AVAILABLE = False

_krx_df = None

def resolve_ticker(query):
    query = query.strip()
    if query.isdigit() and len(query) == 6:
        return query
    if query.isascii() and query.isalpha():
        return query
        
    global _krx_df
    if _krx_df is None:
        _krx_df = fdr.StockListing('KRX')
        
    match = _krx_df[_krx_df['Name'] == query]
    if not match.empty:
        return match.iloc[0]['Code']
        
    match = _krx_df[_krx_df['Name'].str.contains(query, na=False, case=False)]
    if not match.empty:
        return match.iloc[0]['Code']
        
    return query


class QuantAnalyzer:
    def __init__(self, ticker, mode='swing'):
        self.original_name = ticker.upper() if ticker.isascii() else ticker
        resolved           = resolve_ticker(ticker)
        self.ticker        = resolved.upper()
        self.mode          = mode
        self.micro_data    = pd.DataFrame()
        self.macro_data    = pd.DataFrame()
        self.supply_data   = {}
        self.analysis_result = {}

    # ────────────────────────────────────────────────
    # 데이터 수집
    # ────────────────────────────────────────────────
    def fetch_data(self):
        self._fetch_micro_data()
        self._fetch_macro_data()
        self._fetch_supply_data(days=5)

    def _fetch_micro_data(self):
        print(f"[{self.ticker}] 미시적 데이터 수집 시작 (2y)...")
        if self.ticker.isdigit() and len(self.ticker) == 6:
            data = yf.download(f"{self.ticker}.KS", period="2y", interval="1d", progress=False)
            if data.empty:
                self.ticker = f"{self.ticker}.KQ"
                data = yf.download(self.ticker, period="2y", interval="1d", progress=False)
            else:
                self.ticker = f"{self.ticker}.KS"
        else:
            data = yf.download(self.ticker, period="2y", interval="1d", progress=False)

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.droplevel(1)
        self.micro_data = data

    def _fetch_macro_data(self):
        pass

    def _fetch_supply_data(self, days=5):
        """KIS API로 외인/기관 수급 + 공매도 잔고 + 거래대금 수집"""
        try:
            code = self.ticker.replace('.KS', '').replace('.KQ', '')
            if not code.isdigit() or not KIS_AVAILABLE:
                self.supply_data = {
                    "investor_trend": [], "short_balance": {}, "trade_value_map": {}
                }
                return
            self.supply_data = fetch_supply_data(code, days=days)
        except Exception as e:
            print(f"수급 데이터 수집 실패: {e}")
            self.supply_data = {
                "investor_trend": [], "short_balance": {}, "trade_value_map": {}
            }

    # ────────────────────────────────────────────────
    # 수급 스코어
    # ────────────────────────────────────────────────
    def _score_supply(self):
        if not self.supply_data:
            return 0, []

        bonus   = 0
        signals = []

        # ── 투자자별 매매동향 ──────────────────────────────
        trend = self.supply_data.get("investor_trend", [])
        if trend:
            recent = trend[-3:]
            today  = trend[-1]

            foreign_consecutive = len(recent) == 3 and all(d['foreign_net'] > 0 for d in recent)
            institution_today   = today['institution_net'] > 0
            both_buying         = today['foreign_net'] > 0 and today['institution_net'] > 0

            if both_buying:
                bonus += 20
                signals.append(
                    f"🌍🏦 [쌍끌이 매수] 외국인({today['foreign_net']:+,}주)·기관({today['institution_net']:+,}주) "
                    f"동시 순매수! 가장 강력한 수급 신호입니다."
                )
            elif foreign_consecutive:
                bonus += 15
                signals.append(
                    f"🌍 [외인 연속 매수] 외국인 3일 연속 순매수. 오늘 {today['foreign_net']:+,}주."
                )
            elif today['foreign_net'] > 0:
                bonus += 8
                signals.append(f"🌍 [외인 매수] 오늘 외국인 {today['foreign_net']:+,}주 순매수.")

            if institution_today and not both_buying:
                bonus += 10
                signals.append(f"🏦 [기관 매수] 오늘 기관 {today['institution_net']:+,}주 순매수.")

            if today['foreign_net'] < 0 and today['institution_net'] < 0:
                bonus -= 20
                signals.append("⚠️ [쌍끌이 매도] 외국인·기관 동시 매도 중. 진입을 재고하세요.")
            elif today['foreign_net'] < 0:
                bonus -= 10
                signals.append(
                    f"⚠️ [외인 매도] 오늘 외국인 {today['foreign_net']:,}주 순매도. 기술적 타점과 역행 중."
                )

        # ── 공매도 잔고 ────────────────────────────────────
        short = self.supply_data.get("short_balance", {})
        if short:
            ratio      = short.get("balance_ratio", 0)
            change_qty = short.get("change_qty", 0)

            if ratio >= 5.0:
                bonus -= 15
                signals.append(f"🩳 [공매도 위험] 잔고 비율 {ratio:.2f}%. 매수세가 억눌릴 수 있습니다.")
            elif ratio >= 2.0:
                bonus -= 5
                signals.append(f"🩳 [공매도 주의] 잔고 비율 {ratio:.2f}%.")

            if change_qty > 0:
                signals.append(f"📌 공매도 잔고 전일 대비 {change_qty:+,}주 증가.")
            elif change_qty < 0:
                bonus += 5
                signals.append(f"✅ 공매도 잔고 전일 대비 {change_qty:,}주 감소. 숏커버링 가능성.")

        # ── 거래대금 (KIS 정확값 기반) ──────────────────────
        tv_map = self.supply_data.get("trade_value_map", {})
        if tv_map and len(tv_map) >= 5:
            values     = list(tv_map.values())
            avg_value  = sum(values[:-1]) / max(len(values) - 1, 1)  # 오늘 제외 평균
            today_value = values[-1]
            ratio_tv    = today_value / avg_value if avg_value > 0 else 1.0

            # 거래대금 포맷 (억 단위)
            def fmt_value(v):
                return f"{v / 1e8:.0f}억"

            if ratio_tv >= 3.0 and today_value > 0:
                bonus += 15
                signals.append(
                    f"💰 [거래대금 폭발] 오늘 거래대금 {fmt_value(today_value)} — "
                    f"평균 대비 {ratio_tv:.1f}배! 강한 세력 개입 신호입니다."
                )
            elif ratio_tv >= 2.0:
                bonus += 10
                signals.append(
                    f"💰 [거래대금 급증] 오늘 거래대금 {fmt_value(today_value)} — "
                    f"평균 대비 {ratio_tv:.1f}배. 긍정적 신호입니다."
                )
            elif ratio_tv >= 1.5:
                bonus += 5
                signals.append(
                    f"📊 [거래대금 증가] 오늘 거래대금 {fmt_value(today_value)} — "
                    f"평균 대비 {ratio_tv:.1f}배."
                )
            elif ratio_tv <= 0.5:
                # 거래대금 고갈 — VCP 눌림목 맥락에서는 긍정적
                signals.append(
                    f"💤 [거래대금 고갈] 오늘 거래대금 {fmt_value(today_value)} — "
                    f"평균의 {ratio_tv:.1f}배. 에너지 응축 구간일 수 있습니다."
                )

        return bonus, signals

    # ────────────────────────────────────────────────
    # 지표 계산
    # ────────────────────────────────────────────────
    def calculate_indicators(self):
        if self.micro_data.empty:
            return

        df = self.micro_data.copy()

        # 이동평균선
        df['EMA_21']  = df['Close'].ewm(span=21, adjust=False).mean()
        df['EMA_50']  = df['Close'].ewm(span=50, adjust=False).mean()
        df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
        df['SMA_50']  = df['Close'].rolling(window=50).mean()

        # RSI (Wilder's Smoothing — 트레이딩뷰와 일치)
        delta = df['Close'].diff()
        gain  = delta.where(delta > 0, 0).ewm(alpha=1/14, adjust=False).mean()
        loss  = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        df['RSI_14'] = 100 - (100 / (1 + gain / loss))

        # ATR
        df['TR'] = np.maximum(
            df['High'] - df['Low'],
            np.maximum(
                abs(df['High'] - df['Close'].shift(1)),
                abs(df['Low']  - df['Close'].shift(1))
            )
        )
        df['ATR_14'] = df['TR'].ewm(alpha=1/14, adjust=False).mean()

        # 거래량 평균
        df['Vol_SMA_20'] = df['Volume'].rolling(window=20).mean()

        # ── 거래대금: KIS 정확값 우선, 없으면 yfinance 근사값 ──
        tv_map = self.supply_data.get("trade_value_map", {})
        if tv_map:
            # KIS에서 가져온 날짜별 거래대금을 DataFrame 인덱스에 매핑
            df['trading_value'] = df.index.strftime('%Y-%m-%d').map(tv_map)
            # KIS 데이터가 없는 과거 날짜는 근사값으로 채움
            mask = df['trading_value'].isna()
            df.loc[mask, 'trading_value'] = df.loc[mask, 'Close'] * df.loc[mask, 'Volume']
            print(f"[거래대금] KIS 정확값 {(~mask).sum()}일, 근사값 보완 {mask.sum()}일")
        else:
            # KIS 없을 때 전구간 근사값
            df['trading_value'] = df['Close'] * df['Volume']

        df['value_sma_20'] = df['trading_value'].rolling(20).mean()
        df['value_ok']     = df['trading_value'] >= df['value_sma_20'] * 1.5   # 평균 1.5배 이상
        df['value_surge']  = df['trading_value'] >= df['value_sma_20'] * 3.0   # 폭발 (3배 이상)
        df['value_dry']    = df['trading_value'] <= df['value_sma_20'] * 0.5   # 고갈 (절반 이하)

        # MACD
        df['ema_12']      = df['Close'].ewm(span=12, adjust=False).mean()
        df['ema_26']      = df['Close'].ewm(span=26, adjust=False).mean()
        df['MACD']        = df['ema_12'] - df['ema_26']
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist']   = df['MACD'] - df['MACD_Signal']

        # VCP 조건
        df['recent_range'] = df['High'].rolling(5).max() - df['Low'].rolling(5).min()
        df['vcp_tight']    = df['recent_range'] < (df['ATR_14'] * 1.5)
        df['vol_min_20']   = df['Volume'].rolling(20).min()
        # VCP 거래량 고갈: 거래대금 고갈 조건으로 업그레이드
        df['vcp_dry_vol']  = df['value_dry']

        # extATR (ATR Matrix)
        df['extAtr'] = (df['Close'] - df['SMA_50']) / df['ATR_14'].replace(0, np.nan)

        # 추세 조건
        df['trend_short'] = (df['Close'] > df['EMA_21']) | (df['EMA_21'] > df['EMA_50'])
        df['trend_swing'] = (df['EMA_21'] > df['EMA_50']) & (df['EMA_50'] > df['EMA_200'])

        # 눌림목 조건
        df['near_ema21'] = (
            (df['Low'] <= df['EMA_21'] * 1.005) &
            (df['Low'] >= df['EMA_21'] * (1 - (df['ATR_14'] / df['Close']) * 1.5))
        )
        df['bullish_candle'] = df['Close'] > df['Open']
        df['bounce']         = df['Close'] > df['Close'].shift(1)
        df['rsi_ok']         = df['RSI_14'] >= 50
        df['vol_ok']         = df['Volume'] >= df['Vol_SMA_20'] * 1.0
        df['ema21_slope']    = df['EMA_21'] > df['EMA_21'].shift(2)

        # ── 매수 신호 ──────────────────────────────────────
        df['buy_short'] = (
            df['near_ema21'] & df['bullish_candle'] & df['bounce'] &
            df['rsi_ok'] & df['vol_ok'] & df['trend_short']
        )

        df['macd_improving'] = df['MACD_Hist'] > df['MACD_Hist'].shift(1)
        df['buy_swing_macd'] = (
            df['near_ema21'] & df['bullish_candle'] & df['bounce'] &
            df['value_ok'] &          # ← 거래대금 1.5배 이상 조건으로 업그레이드
            df['trend_swing'] & df['macd_improving']
        )

        df['buy_swing_vcp'] = (
            df['near_ema21'] & df['bullish_candle'] & df['bounce'] &
            df['trend_swing'] &
            df['vcp_tight'].shift(1) &
            df['vcp_dry_vol'].shift(1)  # ← 거래대금 고갈 조건으로 업그레이드
        )

        if self.mode == 'fibonacci':
            subset        = df.tail(150)
            self.fib_high = subset['High'].max()
            self.fib_low  = subset['Low'].min()

        self.micro_data = df

    # ────────────────────────────────────────────────
    # 분석
    # ────────────────────────────────────────────────
    def analyze(self):
        self.calculate_indicators()

        if len(self.micro_data) < 50:
            return {"error": "데이터 또는 상장 기간이 충분하지 않습니다."}

        current = self.micro_data.iloc[-1]
        prev    = self.micro_data.iloc[-2]
        score   = 0
        signals = []

        if self.mode == 'swing':
            base_score = 30
            technicals = []

            if current['EMA_21'] > current['EMA_50'] and current['EMA_50'] > current['EMA_200']:
                base_score += 15
                technicals.append("완벽한 정배열(+15)")
            elif current['EMA_21'] > current['EMA_50']:
                base_score += 5

            if current['Volume'] >= current.get('Vol_SMA_20', 0):
                base_score += 10
                technicals.append("긍정적 거래량(+10)")

            if 50 <= current['RSI_14'] <= 70:
                base_score += 10
                technicals.append("RSI 매수 우위(+10)")
            elif current['RSI_14'] > 70:
                base_score -= 15
                signals.append("⚠️ RSI 70 초과 과매수. 조정 후 재진입을 권장합니다.")

            if current['MACD_Hist'] > prev['MACD_Hist']:
                base_score += 10
                technicals.append("MACD 상승 모멘텀(+10)")

            if technicals:
                signals.append(f"🔎 [기술적 분석] {', '.join(technicals)} 확인.")

            score += base_score

            # 타점 탐색
            recent_signal_found = False
            for i in range(len(self.micro_data) - 1, -1, -1):
                row      = self.micro_data.iloc[i]
                days_ago = len(self.micro_data) - 1 - i
                if days_ago > 30:
                    break

                day_text  = "오늘" if days_ago == 0 else f"{days_ago}일 전"
                hit_vcp   = bool(row.get('buy_swing_vcp',  False))
                hit_macd  = bool(row.get('buy_swing_macd', False))
                hit_short = bool(row.get('buy_short',      False))

                if hit_vcp or hit_macd or hit_short:
                    combo_count = sum([hit_vcp, hit_macd, hit_short])
                    if days_ago == 0:
                        score += combo_count * 15

                    stop = round(min(row['Low'], row['EMA_21']) * 0.99, 2)

                    if combo_count > 1:
                        signals.append(
                            f"👑 [{combo_count}중첩 콤보 - {day_text}] 여러 스윙 패턴이 겹친 강력한 타점!"
                        )
                    if hit_vcp:
                        target1 = round(row['Close'] + row['ATR_14'] * 3.0, 2)
                        signals.append(
                            f"🟣 [VCP 스윙 - {day_text}] 거래대금 고갈 + 변동폭 수렴 후 반등!\n"
                            f"  - 진입: {round(row['Close'], 2)}  목표: {target1}  손절: {stop}"
                        )
                    if hit_macd:
                        target1 = round(row['Close'] + row['ATR_14'] * 2.0, 2)
                        signals.append(
                            f"🔵 [MACD 스윙 - {day_text}] 21EMA 눌림 + MACD 반전 + 거래대금 확인!\n"
                            f"  - 진입: {round(row['Close'], 2)}  목표: {target1}  손절: {stop}"
                        )
                    if hit_short and not (hit_vcp or hit_macd):
                        target1 = round(row['Close'] + row['ATR_14'] * 1.5, 2)
                        signals.append(
                            f"🟩 [단기 - {day_text}] 21EMA 단기 양봉 반등 타점.\n"
                            f"  - 진입: {round(row['Close'], 2)}  목표: {target1}  손절: {stop}"
                        )
                    recent_signal_found = True
                    break

            if not recent_signal_found:
                signals.append("💬 최근 30일 내 뚜렷한 매수 타점이 없습니다.")

            supply_score, supply_signals = self._score_supply()
            score   += supply_score
            signals.extend(supply_signals)

            extAtr = current['extAtr']
            if extAtr >= 7.0:
                score -= 30
                signals.append(f"🔥 [ATR Matrix 경고] {extAtr:.1f} ATR 극단 과열 구간!")
            elif extAtr <= -7.0:
                score += 15
                signals.append(f"💡 [ATR Matrix 낙주] {abs(extAtr):.1f} ATR 바닥 구간. V자 반등 주시.")

        elif self.mode == 'atr':
            score = 50
            signals.append("🔎 [ATR 판독] 50일선 대비 탄성을 측정합니다.")
            extAtr = current['extAtr']
            if pd.isna(extAtr):
                signals.append("데이터 부족으로 ATR 계산 불가.")
            else:
                signals.append(f"📏 현재 50일 SMA 기준 【 {extAtr:.2f} ATR 】 위치.")
                if extAtr >= 7:
                    score = 0
                    signals.append("🚨 [매수 금지] 7 ATR 이상 극단 과열!")
                elif extAtr <= -7:
                    score = 90
                    signals.append("🌈 [인생 반등] -7 ATR 투매 구간. 반등 준비.")
                elif extAtr > 3:
                    score = 30
                    signals.append("⚠️ 단기 과열. 이평선 눌림을 기다리세요.")
                elif extAtr < -3:
                    score = 70
                    signals.append("👍 과매도 구간. 분할 매수 고려.")
                else:
                    score = 50
                    signals.append("📊 50일선 정상 궤도 순항 중.")

        elif self.mode == 'fibonacci':
            diff    = self.fib_high - self.fib_low
            fib_236 = self.fib_high - diff * 0.236
            fib_382 = self.fib_high - diff * 0.382
            fib_500 = self.fib_high - diff * 0.500
            fib_618 = self.fib_high - diff * 0.618
            score   = 50
            signals.append(
                f"📐 [피보나치] 최근 150일 최고점({round(self.fib_high, 2)}) 기준 되돌림 분석."
            )
            c = current['Close']
            if c >= fib_236:
                score = 80
                signals.append("🚀 [0.236] 전고점 재돌파 시도 강한 상승 의지.")
            elif c >= fib_382:
                score = 70
                signals.append("📈 [0.382] 가장 이상적인 건강한 스윙 타점 구간.")
            elif c >= fib_500:
                score = 50
                signals.append("⏸️ [0.500] 추세 지속 여부 결정 갈림길.")
            elif c >= fib_618:
                score = 30
                signals.append("👀 [0.618] 마지막 마지노선. 이탈 시 대세 하락 전환.")
            else:
                score = 10
                signals.append("📉 0.618 붕괴. 상승 추세 종료 가능성.")

        display_ticker = (
            f"{self.original_name} ({self.ticker})"
            if self.original_name != self.ticker else self.ticker
        )
        self.analysis_result = {
            "ticker":       display_ticker,
            "last_price":   round(current['Close'], 2),
            "score":        min(100, max(0, score)),
            "signals":      signals,
            "macro_status": "거시적 분석 엔진 오프라인 (향후 연결 예정).",
            "mode":         self.mode,
            "supply_data":  self.supply_data,
        }
        if self.mode == 'fibonacci':
            self.analysis_result["fibonacci"] = {
                "high":    self.fib_high,
                "fib_236": self.fib_high - diff * 0.236,
                "fib_382": self.fib_high - diff * 0.382,
                "fib_500": self.fib_high - diff * 0.500,
                "fib_618": self.fib_high - diff * 0.618,
                "low":     self.fib_low,
            }
        return self.analysis_result

    # ────────────────────────────────────────────────
    # 차트 데이터
    # ────────────────────────────────────────────────
    def get_chart_data(self):
        df_clean   = self.micro_data.dropna(subset=['Close'])
        chart_data = []
        for index, row in df_clean.iterrows():
            chart_data.append({
                "time":           index.strftime('%Y-%m-%d'),
                "open":           row['Open'],
                "high":           row['High'],
                "low":            row['Low'],
                "close":          row['Close'],
                "ema_21":         row['EMA_21']  if not pd.isna(row['EMA_21'])  else None,
                "ema_50":         row['EMA_50']  if not pd.isna(row['EMA_50'])  else None,
                "ema_200":        row['EMA_200'] if not pd.isna(row['EMA_200']) else None,
                "sma_50":         row['SMA_50']  if not pd.isna(row['SMA_50'])  else None,
                "ext_atr":        row['extAtr']  if not pd.isna(row['extAtr'])  else None,
                "buy_short":      bool(row['buy_short']),
                "buy_swing_macd": bool(row.get('buy_swing_macd', False)),
                "buy_swing_vcp":  bool(row.get('buy_swing_vcp',  False)),
                "rsi":            row['RSI_14']  if not pd.isna(row['RSI_14'])  else None,
                "atr":            row['ATR_14']  if not pd.isna(row['ATR_14'])  else None,
                "trading_value":  int(row['trading_value']) if not pd.isna(row.get('trading_value', float('nan'))) else None,
                "stop_price":     round(min(row['Low'], row['EMA_21']) * 0.99, 2)
                                  if not pd.isna(row['EMA_21']) else None,
            })
        return chart_data
