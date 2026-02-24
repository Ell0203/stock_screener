import yfinance as yf
import pandas as pd
import numpy as np
import FinanceDataReader as fdr
import datetime
from pykrx import stock

_krx_df = None

def resolve_ticker(query):
    query = query.strip()
    if query.isdigit() and len(query) == 6:
        return query
    # 순수 알파벳인 경우 (미국 주식 티커로 간주)
    if query.isascii() and query.isalpha():
        return query
        
    global _krx_df
    if _krx_df is None:
        _krx_df = fdr.StockListing('KRX')
        
    # 1. 완전 일치
    match = _krx_df[_krx_df['Name'] == query]
    if not match.empty:
        return match.iloc[0]['Code']
        
    # 2. 부분 일치
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
        self.supply_data = []
        self.analysis_result = {}

    def fetch_data(self):
        """
        데이터 수집 로직. 
        향후 거시적(Macro) 데이터 수집 로직(예: 3~5년치)을 여기에 추가할 수 있도록 구조를 분리해둡니다.
        """
        self._fetch_micro_data()
        self._fetch_macro_data()
        self._fetch_supply_data(days=5)
        
    def _fetch_micro_data(self):
        # 스윙/단기 분석용 최소 1~2년치 일봉 데이터 (200EMA 계산을 위함)
        print(f"[{self.ticker}] 미시적 데이터 수집 시작 (2y)...")
        
        # 한국 주식 코드(6자리 숫자) 처리 로직
        if self.ticker.isdigit() and len(self.ticker) == 6:
            data = yf.download(f"{self.ticker}.KS", period="2y", interval="1d", progress=False)
            if data.empty:
                self.ticker = f"{self.ticker}.KQ"
                data = yf.download(self.ticker, period="2y", interval="1d", progress=False)
            else:
                self.ticker = f"{self.ticker}.KS"
        else:
            data = yf.download(self.ticker, period="2y", interval="1d", progress=False)
        
        # 다중 인덱스가 반환될 경우 최상위 열만 사용
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.droplevel(1)
            
        self.micro_data = data

    def _fetch_macro_data(self):
        # 거시적(장기) 분석용 데이터 수집 뼈대 (향후 구현 예정)
        pass

    def _fetch_supply_data(self, days=5):
        """최근 N일 외인/기관 수급 데이터 (국내 주식 전용)"""
        try:
            # 종목코드 추출 (005930.KS -> 005930)
            code = self.ticker.replace('.KS', '').replace('.KQ', '')
            
            if not code.isdigit():
                self.supply_data = []  # 미국 주식은 수급 데이터 없음
                return
            
            end = datetime.datetime.today().strftime('%Y%m%d')
            # 주말/공휴일 고려해서 넉넉하게 2배로 가져온 후 자름
            start = (datetime.datetime.today() - datetime.timedelta(days=days*3)).strftime('%Y%m%d')
            
            df = stock.get_market_trading_volume_by_date(start, end, code)
            
            if df.empty:
                self.supply_data = []
                return
                
            df = df.tail(days)  # 최근 N거래일만
            
            result = []
            for date, row in df.iterrows():
                result.append({
                    "date": date.strftime('%Y-%m-%d'),
                    "foreign_net":  int(row.get('외국인합계', row.get('외국인', 0))),
                    "institution_net": int(row.get('기관합계', 0)),
                    "individual_net":  int(row.get('개인', 0)),
                })
            self.supply_data = result
            
        except Exception as e:
            print(f"수급 데이터 수집 실패: {e}")
            self.supply_data = []

    def _score_supply(self):
        """최근 수급을 스코어로 변환"""
        if not self.supply_data:
            return 0, []
        
        bonus = 0
        signals = []
        
        # 최근 3일 외인 연속 순매수 체크
        recent = self.supply_data[-3:]
        if len(recent) > 0:
            foreign_consecutive = all(d['foreign_net'] > 0 for d in recent)
            institution_today   = self.supply_data[-1]['institution_net'] > 0
            
            if foreign_consecutive and len(recent) == 3:
                bonus += 15
                signals.append("🌍 [쌍끌이 수급] 외국인이 최근 3일 연속 순매수 중입니다. 세력이 들어오고 있습니다!")
            
            if institution_today:
                bonus += 10
                signals.append("🏦 [기관 수급] 오늘 기관 메이저 수급도 순매수에 가담했습니다.")
            
            if not foreign_consecutive and self.supply_data[-1]['foreign_net'] < 0:
                bonus -= 10
                signals.append("⚠️ [수급 경고] 외국인이 오늘 단기 차익을 실현하며 매도 중입니다. 기술적 타점이 나왔더라도 진입 재고를 권장합니다.")
                
        return bonus, signals

    def calculate_indicators(self):
        """
        수집한 데이터에 기술적 지표(수학 계산)를 적용합니다.
        """
        if self.micro_data.empty:
            return

        df = self.micro_data.copy()
        
        # 1. 이동평균선 상수 (Pine Script 설정 EMA 및 SMA50)
        df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
        df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
        df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
        df['SMA_50'] = df['Close'].rolling(window=50).mean()

        # 2. RSI 계산 (14일, Wilder's Smoothing / RMA 방식 - 트레이딩뷰와 일치)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
        rs = gain / loss
        df['RSI_14'] = 100 - (100 / (1 + rs))

        # 3. ATR 계산 (14일, RMA 방식)
        df['TR'] = np.maximum((df['High'] - df['Low']), 
                   np.maximum(abs(df['High'] - df['Close'].shift(1)), 
                              abs(df['Low'] - df['Close'].shift(1))))
        df['ATR_14'] = df['TR'].ewm(alpha=1/14, adjust=False).mean()
        
        # 4. 거래량 평균 (20일)
        df['Vol_SMA_20'] = df['Volume'].rolling(window=20).mean()

        # --- 1안: MACD 계산 ---
        df['ema_12'] = df['Close'].ewm(span=12, adjust=False).mean()
        df['ema_26'] = df['Close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = df['ema_12'] - df['ema_26']
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

        # --- 3안: VCP (변동성/거래량 고갈) 조건 계산 ---
        # 최근 5일간의 고점-저점 폭이 ATR의 1.5배 이내로 수렴 (Tightness)
        df['recent_range'] = df['High'].rolling(5).max() - df['Low'].rolling(5).min()
        df['vcp_tight'] = df['recent_range'] < (df['ATR_14'] * 1.5)
        # 거래량 고갈: 최근 3일 중 거래량이 20일 최저치에 근접
        df['vol_min_20'] = df['Volume'].rolling(20).min()
        df['vcp_dry_vol'] = df['Volume'].rolling(3).min() <= (df['vol_min_20'] * 1.2)

        # --- ATR Matrix 스펙 (extAtr) 계산 ---
        df['extAtr'] = (df['Close'] - df['SMA_50']) / df['ATR_14'].replace(0, np.nan)

        # --- 매수 로직 베이스 ---
        df['trend_short'] = (df['Close'] > df['EMA_21']) | (df['EMA_21'] > df['EMA_50'])
        df['trend_swing'] = (df['EMA_21'] > df['EMA_50']) & (df['EMA_50'] > df['EMA_200'])

        df['near_ema21'] = (df['Low'] <= df['EMA_21'] * 1.005) & (df['Low'] >= df['EMA_21'] * (1 - (df['ATR_14'] / df['Close']) * 1.5))
        df['bullish_candle'] = df['Close'] > df['Open']
        df['bounce'] = df['Close'] > df['Close'].shift(1)
        df['rsi_ok'] = df['RSI_14'] >= 50
        df['vol_ok'] = df['Volume'] >= df['Vol_SMA_20'] * 1.0
        df['ema21_slope'] = df['EMA_21'] > df['EMA_21'].shift(2)

        df['buy_short'] = df['near_ema21'] & df['bullish_candle'] & df['bounce'] & df['rsi_ok'] & df['vol_ok'] & df['trend_short']

        # [1안] MACD 스윙: 기존 정배열 눌림목 + MACD 모멘텀 상승 반전(히스토그램 증가)
        df['macd_improving'] = df['MACD_Hist'] > df['MACD_Hist'].shift(1)
        df['buy_swing_macd'] = df['near_ema21'] & df['bullish_candle'] & df['bounce'] & df['vol_ok'] & df['trend_swing'] & df['macd_improving']

        # [3안] VCP 스윙: 21EMA 부근 + 수렴(전날까지) + 거래량 고갈(전날까지) + 오늘 양봉 반등 돌파
        df['buy_swing_vcp'] = df['near_ema21'] & df['bullish_candle'] & df['bounce'] & df['trend_swing'] & df['vcp_tight'].shift(1) & df['vcp_dry_vol'].shift(1)

        if self.mode == 'fibonacci':
            latest_subset = df.tail(150)
            self.fib_high = latest_subset['High'].max()
            self.fib_low = latest_subset['Low'].min()

        self.micro_data = df

    def analyze(self):
        """
        계산된 지표를 바탕으로 현재 상황을 분석합니다.
        """
        self.calculate_indicators()
        
        # 데이터가 부족하면 에러 반환
        if len(self.micro_data) < 50:
            return {"error": "데이터 또는 상장 기간이 충분하지 않습니다."}

        # 가장 최신 거래일의 데이터를 가져옴 (마지막 행)
        current = self.micro_data.iloc[-1]
        prev = self.micro_data.iloc[-2]
        
        # 미시적(스윙) 패턴 스캔 기초 로직 -------------------
        score = 0 
        signals = []

        if self.mode == 'swing':
            # --- 1. 동적 스코어 로직 (현재 상태 기준 합산) ---
            base_score = 30
            technicals = []
            
            # 정배열 점수
            if current['EMA_21'] > current['EMA_50'] and current['EMA_50'] > current['EMA_200']:
                base_score += 15
                technicals.append("완벽한 정배열(+15)")
            elif current['EMA_21'] > current['EMA_50']:
                base_score += 5
                
            # 거래량 점수
            if current['Volume'] >= current.get('Vol_SMA_20', 0):
                base_score += 10
                technicals.append("긍정적 거래량(+10)")
                
            # RSI 점수 & 과열 경고
            if 50 <= current['RSI_14'] <= 70:
                base_score += 10
                technicals.append("RSI 매수 우위(+10)")
            elif current['RSI_14'] > 70:
                base_score -= 15
                signals.append("⚠️ RSI 70 초과 과매수 상태. 차익 실현 후 조정(풀백)을 기다리시길 권장합니다.")
                
            # MACD 모멘텀 점수
            if current['MACD_Hist'] > prev['MACD_Hist']:
                base_score += 10
                technicals.append("MACD 상승 모멘텀(+10)")
                
            if len(technicals) > 0:
                signals.append(f"🔎 [현재 캔들 기술적 분석] {', '.join(technicals)} 요소가 확인되었습니다.")

            score += base_score

            # --- 2. 다중 타점 탐색 로직 (최근 신호 중복 카운트) ---
            recent_signal_found = False
            for i in range(len(self.micro_data)-1, -1, -1):
                row = self.micro_data.iloc[i]
                days_ago = len(self.micro_data) - 1 - i
                
                if days_ago > 30: # 30일 이내의 타점만 브리핑에 표시
                    break
                    
                day_text = "오늘" if days_ago == 0 else f"{days_ago}일 전"
                
                hit_vcp = row.get('buy_swing_vcp', False)
                hit_macd = row.get('buy_swing_macd', False)
                hit_short = row.get('buy_short', False)
                
                if hit_vcp or hit_macd or hit_short:
                    combo_count = sum([bool(hit_vcp), bool(hit_macd), bool(hit_short)])
                    
                    if days_ago == 0:
                        score += (combo_count * 15) # 오늘 신호가 떴을 때 중복된 콤보 수만큼 대량 득점
                        
                    stop = round(min(row['Low'], row['EMA_21']) * 0.99, 2)
                    
                    if combo_count > 1:
                        signals.append(f"👑 [{combo_count}중첩 콤보 타점 포착! - {day_text}] 여러 스윙 패턴이 겹친 매우 강력하고 드문 타점입니다!")
                    
                    if hit_vcp:
                        target1 = round(row['Close'] + row['ATR_14'] * 3.0, 2)
                        signals.append(f"🟣 [VCP 스윙 포착 - {day_text}] 최근 변동폭이 잔잔하게 수렴하고 거래량이 고갈된 후 위로 에너지를 터뜨렸기 때문입니다!\n  - 진입가: {round(row['Close'], 2)}\n  - 목표가: {target1}\n  - 손절가: {stop}")
                    if hit_macd:
                        target1 = round(row['Close'] + row['ATR_14'] * 2.0, 2)
                        signals.append(f"🔵 [MACD 스윙 포착 - {day_text}] 21일선 부근까지 안정적으로 눌린 직후 MACD가 마이너스에서 다시 상승 반전(히스토그램 전환)하며 바닥을 다졌기 때문입니다!\n  - 진입가: {round(row['Close'], 2)}\n  - 목표가: {target1}\n  - 손절가: {stop}")
                    if hit_short and not (hit_vcp or hit_macd):
                        # 중복되지 않은 가장 단순한 기술적 반등일 때만 출력
                        target1 = round(row['Close'] + row['ATR_14'] * 1.5, 2)
                        signals.append(f"🟩 [단기 매수 포착 - {day_text}] 21EMA 부근에 맞고 튀어오르는 단순한 기술적 단기 양봉 반등 타점입니다. (짧게 먹고 빠지는 용도)\n  - 진입가: {round(row['Close'], 2)}\n  - 최소 목표가: {target1}\n  - 손절가: {stop}")
                        
                    recent_signal_found = True
                    break # 하루만 분석하고 종료

            if not recent_signal_found:
                signals.append("💬 최근 30일 내에 포착된 뚜렷한 매수 타점(화살표)이 차트에 없습니다.")
                
            # --- 4. 수급 데이터 코멘트 및 점수 ---
            supply_score, supply_signals = self._score_supply()
            score += supply_score
            signals.extend(supply_signals)

            # --- 5. ATR Matrix 시너지 등락 ---
            extAtr = current['extAtr']
            if extAtr >= 7.0:
                score -= 30
                signals.append(f"🔥 [ATR Matrix 긴급 경고] 50일선 대비 {extAtr:.1f} ATR 만큼 극단적으로 치솟은 최상단 과열 구간입니다. 언제 패닉락이 떨어져도 이상하지 않으니 매수를 보류하세요!")
            elif extAtr <= -7.0:
                score += 15
                signals.append(f"💡 [ATR Matrix 낙주 기회] 주가가 50일선 기준 {abs(extAtr):.1f} ATR 만큼 바닥으로 곤두박질쳤습니다. 여기서 상승 반전한다면 엄청난 V자 랠리가 일어날 수 있습니다.")
                
        elif self.mode == 'atr':
            score = 50
            signals.append("🔎 [ATR 과열/침체 판독 센터] 현재 모드는 스프링의 탄성을 측정합니다. 50일 생명선(파란색)에서 주가가 너무 벗어나서 튕겨져 나갈 위기인지 측정합니다.")
            
            extAtr = current['extAtr']
            if pd.isna(extAtr):
                signals.append("데이터가 부족하여 ATR 계산을 할 수 없습니다.")
            else:
                signals.append(f"📏 현재 이 주식은 50일 이동평균선(SMA50)을 기준으로 위/아래 방향으로 【 {extAtr:.2f} ATR 】 만큼 멀어져 있는 상태로 계산되었습니다.")
                
                if extAtr >= 7:
                    score = 0
                    signals.append("🚨 [절대 매수 금지 단계] 7 ATR을 돌파하며 과열 피날레를 찍고 있습니다! 작전주이거나 쏠림 현상의 끝자락이니, 가진 자의 영역이며 익절 후 도망쳐야 합니다.")
                elif extAtr <= -7:
                    score = 90
                    signals.append("🌈 [인생 반등 타점 단계] -7 ATR 아래로 떨어졌습니다! 모두가 주식을 버리고 도망가는 투매장입니다. 평균선(50일)으로 강력하게 다시 달라붙는 기술적 로켓 반등을 먹을 준비를 해야 합니다.")
                elif extAtr > 3:
                    score = 30
                    signals.append("⚠️ 진입 주의 단계입니다. 주가의 거품이 단기적으로 살짝 낀 상태로 보이니, 스윙 타점을 원한다면 다시 이평선 부근으로 눌릴 때(0 근처)를 기다리세요.")
                elif extAtr < -3:
                    score = 70
                    signals.append("👍 과매도 구간(침체기)으로 진입 중입니다. 저평가되어 있으니 분할로 지지 여부를 체크하며 매수를 계획해볼 수 있는 구간입니다.")
                else:
                    score = 50
                    signals.append("📊 정상 궤도행. 현재 50일 이평선에 찰싹 달라붙어 건강하고 안정적인 궤도를 순항하고 있습니다.")

        elif self.mode == 'fibonacci':
            diff = self.fib_high - self.fib_low
            fib_0 = self.fib_high
            fib_236 = self.fib_high - diff * 0.236
            fib_382 = self.fib_high - diff * 0.382
            fib_500 = self.fib_high - diff * 0.500
            fib_618 = self.fib_high - diff * 0.618
            fib_1 = self.fib_low
            
            score = 50
            signals.append(f"📐 [피보나치 되돌림 분석] 주식의 오르내림 파동에는 자연의 황금비율이 있습니다. 최근 150일간 최고점({round(self.fib_high,2)}) 대비 어디까지 '되돌림(눌림 조정)'을 겪고 있는지 계산합니다.")
            
            c = current['Close']
            if c >= fib_236:
                score = 80
                signals.append("🚀 현재 [0.236(23.6%)] 구간 위에서 아주 강하게 버티고 있습니다. 이건 살짝만 숨을 돌리고 이내 전고점을 한 번 더 돌파해버리려는 극강의 상승 의지입니다.")
            elif c >= fib_382:
                score = 70
                signals.append("📈 [0.382(38.2%)] 구간 근처의 지지를 테스트 중입니다. 가장 이상적이고 건강한 조정 템포를 가진 아주 평범한 스윙 타점 라인입니다.")
            elif c >= fib_500:
                score = 50
                signals.append("⏸️ 고점과 저점의 딱 절반인 [0.500(50%)] 구간입니다. 이 자리를 방어해내느냐 아니냐가 이번 추세가 끝난 건지 더 가려는 건지 판단하는 중대한 갈림길입니다.")
            elif c >= fib_618:
                score = 30
                signals.append("👀 마지막 마지노선 [0.618(61.8%)] 황금비율 라인에 턱걸이했습니다. 이 선이 깨지고 더 떨어진다면 그것은 '단순 조정'이 아니라 '대세 하락 파동의 시작'으로 인정해야 하니 칼손절을 준비해야 합니다.")
            else:
                score = 10
                signals.append("📉 0.618 방어선마저 완벽히 깨지고 추락했습니다. 상승의 수명이 다했으며 장기 시체산 구간이 기약 없이 펼쳐질 수 있습니다.")

        # 거시적 패턴 뼈대 ---------------------------------
        # 향후 200일선, 월봉 지지선 추세 분석 결과를 위 score와 signals에 융합할 구역입니다.
        macro_signal = "거시적(큰 그림) 분석 엔진은 현재 오프라인 상태(향후 연결 예정)입니다."
            
        display_ticker = f"{self.original_name} ({self.ticker})" if self.original_name != self.ticker else self.ticker
        self.analysis_result = {
            "ticker": display_ticker,
            "last_price": round(current['Close'], 2),
            "score": min(100, max(0, score)), # 0~100 사이
            "signals": signals,
            "macro_status": macro_signal,
            "mode": self.mode,
            "supply_data": self.supply_data
        }
        
        if self.mode == 'fibonacci':
            self.analysis_result["fibonacci"] = {
                "high": self.fib_high,
                "fib_236": self.fib_high - (self.fib_high - self.fib_low) * 0.236,
                "fib_382": self.fib_high - (self.fib_high - self.fib_low) * 0.382,
                "fib_500": self.fib_high - (self.fib_high - self.fib_low) * 0.500,
                "fib_618": self.fib_high - (self.fib_high - self.fib_low) * 0.618,
                "low": self.fib_low,
            }
        
        return self.analysis_result
        
    def get_chart_data(self):
        """프론트엔드 차트로 그리기 위한 JSON 데이터를 추출합니다."""
        df_clean = self.micro_data.dropna(subset=['Close'])
        chart_data = []
        for index, row in df_clean.iterrows():
            chart_data.append({
                "time": index.strftime('%Y-%m-%d'),
                "open": row['Open'],
                "high": row['High'],
                "low": row['Low'],
                "close": row['Close'],
                "ema_21": row['EMA_21'] if not pd.isna(row['EMA_21']) else None,
                "ema_50": row['EMA_50'] if not pd.isna(row['EMA_50']) else None,
                "ema_200": row['EMA_200'] if not pd.isna(row['EMA_200']) else None,
                "sma_50": row['SMA_50'] if not pd.isna(row['SMA_50']) else None,
                "ext_atr": row['extAtr'] if not pd.isna(row['extAtr']) else None,
                "buy_short": bool(row['buy_short']),
                "buy_swing_macd": bool(row.get('buy_swing_macd', False)),
                "buy_swing_vcp": bool(row.get('buy_swing_vcp', False)),
                "rsi": row['RSI_14'] if not pd.isna(row['RSI_14']) else None,
                "atr": row['ATR_14'] if not pd.isna(row['ATR_14']) else None,
                "stop_price": round(min(row['Low'], row['EMA_21']) * 0.99, 2) if not pd.isna(row['EMA_21']) else None,
            })
        return chart_data
