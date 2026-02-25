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
        resolved = resolve_ticker(ticker)
        self.ticker = resolved.upper()
        self.mode = mode
        self.micro_data = pd.DataFrame()
        self.macro_data = pd.DataFrame()
        self.supply_data = {}
        self.analysis_result = {}

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
        """한국투자증권 API로 외인/기관 수급 + 공매도 잔고 + 거래대금 수집"""
        try:
            code = self.ticker.replace('.KS', '').replace('.KQ', '')
            if not code.isdigit() or not KIS_AVAILABLE:
                self.supply_data = {"investor_trend": [], "short_balance": {}, "trade_value_map": {}}
                return
            self.supply_data = fetch_supply_data(code, days=days)
        except Exception as e:
            print(f"수급 데이터 수집 실패: {e}")
            self.supply_data = {"investor_trend": [], "short_balance": {}, "trade_value_map": {}}

    def _score_supply(self):
        """수급 + 거래대금 스코어 변환"""
        if not self.supply_data:
            return 0, []

        bonus = 0
        signals = []

        # ── 투자자별 매매동향 ────────────────────────────────────────
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
                    f"🌍🏦 [쌍끌이 매수] 오늘 외국인({today['foreign_net']:+,}주)·"
                    f"기관({today['institution_net']:+,}주) 동시 순매수! 가장 강력한 수급 신호입니다."
                )
            elif foreign_consecutive:
                bonus += 15
                signals.append(
                    f"🌍 [외인 연속 매수] 외국인 3일 연속 순매수 중. 오늘 {today['foreign_net']:+,}주."
                )
            elif today['foreign_net'] > 0:
                bonus += 8
                signals.append(f"🌍 [외인 매수] 오늘 외국인 {today['foreign_net']:+,}주 순매수.")

            if institution_today and not both_buying:
                bonus += 10
                signals.append(f"🏦 [기관 매수] 오늘 기관 {today['institution_net']:+,}주 순매수.")

            if today['foreign_net'] < 0 and today['institution_net'] < 0:
                bonus -= 20
                signals.append("⚠️ [쌍끌이 매도 경고] 외국인·기관 동시 매도 중. 진입을 재고하세요.")
            elif today['foreign_net'] < 0:
                bonus -= 10
                signals.append(f"⚠️ [외인 매도] 오늘 외국인 {today['foreign_net']:,}주 순매도.")

        # ── 공매도 일별추이 ────────────────────────────────────
        short = self.supply_data.get("short_balance", {})
        today_short = short.get("today", {})
        if today_short:
            vol_rlim  = today_short.get("ssts_vol_rlim", 0)       # 공매도 거래량 비중(%)
            pbmn_rlim = today_short.get("ssts_tr_pbmn_rlim", 0)   # 공매도 거래대금 비중(%)
            ssts_qty  = today_short.get("ssts_cntg_qty", 0)

            if vol_rlim >= 10.0:
                bonus -= 15
                signals.append(
                    f"🩳 [공매도 위험] 오늘 공매도 거래량 비중 {vol_rlim:.1f}%. "
                    f"체결 수량 {ssts_qty:,}주. 매수세가 크게 억눌릴 수 있습니다."
                )
            elif vol_rlim >= 5.0:
                bonus -= 8
                signals.append(f"🩳 [공매도 주의] 오늘 공매도 거래량 비중 {vol_rlim:.1f}%.")
            elif vol_rlim >= 2.0:
                bonus -= 3
                signals.append(f"🩳 공매도 거래량 비중 {vol_rlim:.1f}% (낮은 수준).")

            if pbmn_rlim > vol_rlim + 2:
                signals.append(
                    f"⚠️ 공매도 거래대금 비중({pbmn_rlim:.1f}%)이 거래량 비중({vol_rlim:.1f}%)보다 높습니다. "
                    f"고가 공매도 가능성 — 추가 하락 압력 주의."
                )

        # ── 거래대금 기반 수급 강도 ──────────────────────────────────
        # micro_data에 이미 계산된 value_ratio를 활용
        if not self.micro_data.empty and 'value_ratio' in self.micro_data.columns:
            today_value_ratio = self.micro_data['value_ratio'].iloc[-1]
            today_bullish     = self.micro_data['Close'].iloc[-1] > self.micro_data['Open'].iloc[-1]

            if today_value_ratio >= 2.0 and today_bullish:
                bonus += 15
                signals.append(
                    f"💰 [거래대금 폭발] 오늘 거래대금이 20일 평균 대비 {today_value_ratio:.1f}배! "
                    f"강한 매수세가 유입되었습니다."
                )
            elif today_value_ratio >= 1.5 and today_bullish:
                bonus += 8
                signals.append(
                    f"💰 [거래대금 증가] 거래대금 평균 대비 {today_value_ratio:.1f}배로 양봉 마감."
                )
            elif today_value_ratio >= 2.0 and not today_bullish:
                bonus -= 10
                signals.append(
                    f"⚠️ [고거래대금 음봉] 거래대금 {today_value_ratio:.1f}배인데 음봉. "
                    f"세력 분배 가능성이 있습니다."
                )

        return bonus, signals

    def calculate_indicators(self):
        if self.micro_data.empty:
            return

        df = self.micro_data.copy()

        # ── 이동평균선 ────────────────────────────────────────────────
        df['EMA_21']  = df['Close'].ewm(span=21, adjust=False).mean()
        df['EMA_50']  = df['Close'].ewm(span=50, adjust=False).mean()
        df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
        df['SMA_50']  = df['Close'].rolling(window=50).mean()

        # ── RSI (Wilder's Smoothing — 트레이딩뷰 일치) ────────────────
        delta = df['Close'].diff()
        gain  = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss  = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs    = gain / loss
        df['RSI_14'] = 100 - (100 / (1 + rs))

        # ── ATR (RMA 방식) ────────────────────────────────────────────
        df['TR'] = np.maximum(
            (df['High'] - df['Low']),
            np.maximum(
                abs(df['High'] - df['Close'].shift(1)),
                abs(df['Low']  - df['Close'].shift(1))
            )
        )
        df['ATR_14'] = df['TR'].ewm(alpha=1/14, adjust=False).mean()

        # ── 거래량 평균 ───────────────────────────────────────────────
        df['Vol_SMA_20'] = df['Volume'].rolling(window=20).mean()

        # ── 거래대금 (KIS 정확값 우선, 없으면 yfinance 근사값) ─────────
        trade_value_map = self.supply_data.get("trade_value_map", {})
        if trade_value_map:
            # KIS API 정확값으로 날짜 매핑
            df['trading_value'] = pd.to_datetime(df.index).strftime('%Y-%m-%d').map(
                lambda d: trade_value_map.get(d, None)
            )
            # KIS 데이터 없는 날짜는 근사값으로 보완
            mask = df['trading_value'].isna()
            df.loc[mask, 'trading_value'] = df.loc[mask, 'Close'] * df.loc[mask, 'Volume']
            df['trading_value'] = df['trading_value'].astype(float)
        else:
            # KIS 데이터 없으면 전체 근사값
            df['trading_value'] = df['Close'] * df['Volume']

        df['value_sma_20'] = df['trading_value'].rolling(20).mean()
        df['value_ratio']  = df['trading_value'] / df['value_sma_20'].replace(0, np.nan)
        df['value_surge']  = df['value_ratio'] >= 2.0   # 거래대금 2배 이상 폭발
        df['value_ok']     = df['value_ratio'] >= 1.5   # 거래대금 1.5배 이상
        df['value_dry']    = df['value_ratio'] <= 0.7   # 거래대금 고갈 (VCP용)

        # ── MACD ─────────────────────────────────────────────────────
        df['ema_12']      = df['Close'].ewm(span=12, adjust=False).mean()
        df['ema_26']      = df['Close'].ewm(span=26, adjust=False).mean()
        df['MACD']        = df['ema_12'] - df['ema_26']
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist']   = df['MACD'] - df['MACD_Signal']

        # ── VCP 조건 ──────────────────────────────────────────────────
        df['recent_range'] = df['High'].rolling(5).max() - df['Low'].rolling(5).min()
        df['vcp_tight']    = df['recent_range'] < (df['ATR_14'] * 1.5)
        df['value_dry_3']  = df['value_dry'].rolling(3).min().astype(bool)  # 3일 연속 거래대금 고갈

        # ── ATR Matrix ────────────────────────────────────────────────
        df['extAtr'] = (df['Close'] - df['SMA_50']) / df['ATR_14'].replace(0, np.nan)

        # ── 추세 조건 ─────────────────────────────────────────────────
        df['trend_short'] = (df['Close'] > df['EMA_21']) | (df['EMA_21'] > df['EMA_50'])
        df['trend_swing'] = (df['EMA_21'] > df['EMA_50']) & (df['EMA_50'] > df['EMA_200'])

        # ── 기본 캔들 조건 ────────────────────────────────────────────
        df['near_ema21']     = (
            (df['Low'] <= df['EMA_21'] * 1.005) &
            (df['Low'] >= df['EMA_21'] * (1 - (df['ATR_14'] / df['Close']) * 1.5))
        )
        df['bullish_candle'] = df['Close'] > df['Open']
        df['bounce']         = df['Close'] > df['Close'].shift(1)
        df['rsi_ok']         = df['RSI_14'] >= 50
        df['vol_ok']         = df['Volume'] >= df['Vol_SMA_20'] * 1.0
        df['ema21_slope']    = df['EMA_21'] > df['EMA_21'].shift(2)

        # ── 매수 신호 ─────────────────────────────────────────────────
        # 단기: 거래량 기준 유지 (거래대금 데이터 없는 미국주식 대응)
        df['buy_short'] = (
            df['near_ema21'] & df['bullish_candle'] & df['bounce'] &
            df['rsi_ok'] & df['vol_ok'] & df['trend_short']
        )

        # MACD 스윙: 거래대금 1.5배 이상 조건 추가
        df['macd_improving'] = df['MACD_Hist'] > df['MACD_Hist'].shift(1)
        df['buy_swing_macd'] = (
            df['near_ema21'] & df['bullish_candle'] & df['bounce'] &
            df['value_ok'] &           # ← 거래대금 기준으로 강화
            df['trend_swing'] & df['macd_improving']
        )

        # VCP 스윙: 거래대금 고갈 조건으로 교체 (더 정확)
        df['buy_swing_vcp'] = (
            df['near_ema21'] & df['bullish_candle'] & df['bounce'] &
            df['trend_swing'] &
            df['vcp_tight'].shift(1) &
            df['value_dry_3'].shift(1)  # ← 거래대금 3일 고갈로 교체
        )

        if self.mode == 'fibonacci':
            latest_subset = df.tail(150)
            self.fib_high = latest_subset['High'].max()
            self.fib_low  = latest_subset['Low'].min()

        self.micro_data = df

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
                base_score += 5
                technicals.append("거래량 평균 이상(+5)")

            # 거래대금 점수 (거래량보다 가중치 높게)
            if 'value_ratio' in self.micro_data.columns:
                vr = current['value_ratio']
                if vr >= 2.0:
                    base_score += 15
                    technicals.append(f"거래대금 {vr:.1f}배 폭발(+15)")
                elif vr >= 1.5:
                    base_score += 10
                    technicals.append(f"거래대금 {vr:.1f}배(+10)")

            if 50 <= current['RSI_14'] <= 70:
                base_score += 10
                technicals.append("RSI 매수 우위(+10)")
            elif current['RSI_14'] > 70:
                base_score -= 15
                signals.append("⚠️ RSI 70 초과 과매수 상태. 풀백 후 진입을 권장합니다.")

            if current['MACD_Hist'] > prev['MACD_Hist']:
                base_score += 10
                technicals.append("MACD 상승 모멘텀(+10)")

            if technicals:
                signals.append(f"🔎 [기술적 분석] {', '.join(technicals)} 확인.")

            score += base_score

            # ── 타점 탐색 ────────────────────────────────────────────
            recent_signal_found = False
            for i in range(len(self.micro_data)-1, -1, -1):
                row      = self.micro_data.iloc[i]
                days_ago = len(self.micro_data) - 1 - i
                if days_ago > 30:
                    break

                day_text  = "오늘" if days_ago == 0 else f"{days_ago}일 전"
                hit_vcp   = row.get('buy_swing_vcp', False)
                hit_macd  = row.get('buy_swing_macd', False)
                hit_short = row.get('buy_short', False)

                if hit_vcp or hit_macd or hit_short:
                    combo_count = sum([bool(hit_vcp), bool(hit_macd), bool(hit_short)])
                    if days_ago == 0:
                        score += combo_count * 15

                    stop    = round(min(row['Low'], row['EMA_21']) * 0.99, 2)
                    vr_text = f" (거래대금 {row['value_ratio']:.1f}배)" if 'value_ratio' in row and not pd.isna(row['value_ratio']) else ""

                    if combo_count > 1:
                        signals.append(f"👑 [{combo_count}중첩 콤보 - {day_text}] 여러 패턴이 겹친 강력한 타점!")

                    if hit_vcp:
                        target = round(row['Close'] + row['ATR_14'] * 3.0, 2)
                        signals.append(
                            f"🟣 [VCP 스윙 - {day_text}] 거래대금 고갈 수렴 후 에너지 폭발!{vr_text}\n"
                            f"  - 진입가: {round(row['Close'], 2)}  목표가: {target}  손절가: {stop}"
                        )
                    if hit_macd:
                        target = round(row['Close'] + row['ATR_14'] * 2.0, 2)
                        signals.append(
                            f"🔵 [MACD 스윙 - {day_text}] 21일선 눌림 + MACD 반전!{vr_text}\n"
                            f"  - 진입가: {round(row['Close'], 2)}  목표가: {target}  손절가: {stop}"
                        )
                    if hit_short and not (hit_vcp or hit_macd):
                        target = round(row['Close'] + row['ATR_14'] * 1.5, 2)
                        signals.append(
                            f"🟩 [단기 반등 - {day_text}] 21EMA 단기 양봉 반등 타점.\n"
                            f"  - 진입가: {round(row['Close'], 2)}  목표가: {target}  손절가: {stop}"
                        )

                    recent_signal_found = True
                    break

            if not recent_signal_found:
                signals.append("💬 최근 30일 내 뚜렷한 매수 타점이 없습니다.")

            # ── 수급 스코어 ──────────────────────────────────────────
            supply_score, supply_signals = self._score_supply()
            score   += supply_score
            signals.extend(supply_signals)

            # ── ATR Matrix ───────────────────────────────────────────
            extAtr = current['extAtr']
            if extAtr >= 7.0:
                score -= 30
                signals.append(f"🔥 [ATR Matrix 경고] 50일선 대비 {extAtr:.1f} ATR 극단 과열 구간!")
            elif extAtr <= -7.0:
                score += 15
                signals.append(f"💡 [ATR Matrix] {abs(extAtr):.1f} ATR 바닥 구간. V자 반등 가능성.")

        elif self.mode == 'atr':
            score = 50
            signals.append("🔎 [ATR 판독] 50일선 대비 탄성을 측정합니다.")
            extAtr = current['extAtr']
            if pd.isna(extAtr):
                signals.append("데이터 부족으로 ATR 계산 불가.")
            else:
                signals.append(f"📏 50일 이동평균선 기준 【 {extAtr:.2f} ATR 】 만큼 이격.")
                if extAtr >= 7:
                    score = 0
                    signals.append("🚨 [절대 매수 금지] 7 ATR 돌파 극단 과열!")
                elif extAtr <= -7:
                    score = 90
                    signals.append("🌈 [인생 반등 타점] -7 ATR 투매 구간. V자 반등 준비.")
                elif extAtr > 3:
                    score = 30
                    signals.append("⚠️ 단기 과열. 이평선 눌림 대기.")
                elif extAtr < -3:
                    score = 70
                    signals.append("👍 과매도 구간. 분할 매수 검토.")
                else:
                    score = 50
                    signals.append("📊 50일 이평선 정상 궤도 순항 중.")

        elif self.mode == 'fibonacci':
            diff    = self.fib_high - self.fib_low
            fib_236 = self.fib_high - diff * 0.236
            fib_382 = self.fib_high - diff * 0.382
            fib_500 = self.fib_high - diff * 0.500
            fib_618 = self.fib_high - diff * 0.618

            score = 50
            signals.append(f"📐 [피보나치] 최근 150일 최고점({round(self.fib_high,2)}) 기준 되돌림 분석.")
            c = current['Close']
            if   c >= fib_236: score = 80; signals.append("🚀 [0.236] 전고점 재돌파 시도 중.")
            elif c >= fib_382: score = 70; signals.append("📈 [0.382] 가장 이상적인 스윙 타점 구간.")
            elif c >= fib_500: score = 50; signals.append("⏸️ [0.500] 추세 지속 여부 판단 갈림길.")
            elif c >= fib_618: score = 30; signals.append("👀 [0.618] 마지막 마지노선. 이탈 시 대세 하락 전환.")
            else:              score = 10; signals.append("📉 0.618 붕괴. 상승 추세 종료 가능성.")

        macro_signal   = "거시적 분석 엔진은 현재 오프라인 상태(향후 연결 예정)입니다."
        display_ticker = (
            f"{self.original_name} ({self.ticker})"
            if self.original_name != self.ticker else self.ticker
        )

        self.analysis_result = {
            "ticker":       display_ticker,
            "last_price":   round(current['Close'], 2),
            "score":        min(100, max(0, score)),
            "signals":      signals,
            "macro_status": macro_signal,
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
                "value_ratio":    round(row['value_ratio'], 2) if 'value_ratio' in row and not pd.isna(row['value_ratio']) else None,
                "trading_value":  int(row['trading_value']) if 'trading_value' in row and not pd.isna(row['trading_value']) else None,
                "stop_price":     round(min(row['Low'], row['EMA_21']) * 0.99, 2)
                                  if not pd.isna(row['EMA_21']) else None,
            })
        return chart_data
