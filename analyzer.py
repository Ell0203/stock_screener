import yfinance as yf
import pandas as pd
import numpy as np
import FinanceDataReader as fdr

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
    def __init__(self, ticker):
        self.original_name = ticker.upper() if ticker.isascii() else ticker
        resolved = resolve_ticker(ticker)
        self.ticker = resolved.upper()
        self.micro_data = pd.DataFrame()
        self.macro_data = pd.DataFrame()
        self.analysis_result = {}

    def fetch_data(self):
        """
        데이터 수집 로직. 
        향후 거시적(Macro) 데이터 수집 로직(예: 3~5년치)을 여기에 추가할 수 있도록 구조를 분리해둡니다.
        """
        self._fetch_micro_data()
        self._fetch_macro_data()
        
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
        # 예: self.macro_data = yf.download(self.ticker, period="3y", interval="1wk")
        pass

    def calculate_indicators(self):
        """
        수집한 데이터에 기술적 지표(수학 계산)를 적용합니다.
        """
        if self.micro_data.empty:
            return

        df = self.micro_data.copy()
        
        # 1. 이동평균선 상수 (Pine Script 설정 EMA)
        df['EMA_21'] = df['Close'].ewm(span=21, adjust=False).mean()
        df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
        df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()

        # 2. RSI 계산 (14일)
        delta = df['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
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
        score = 50 
        signals = []

        # 가장 최근에 발생한 타점을 찾기 위해 데이터를 역순으로 탐색
        recent_signal_found = False
        for i in range(len(self.micro_data)-1, -1, -1):
            row = self.micro_data.iloc[i]
            days_ago = len(self.micro_data) - 1 - i
            
            if days_ago > 30: # 30일 이내의 타점만 브리핑에 표시
                break
                
            day_text = "오늘" if days_ago == 0 else f"{days_ago}일 전"
            
            if row.get('buy_swing_vcp') and not recent_signal_found:
                score += 45
                stop = round(min(row['Low'], row['EMA_21']) * 0.99, 2)
                target1 = round(row['Close'] + row['ATR_14'] * 3.0, 2)
                signals.append(f"🟣 [VCP 스윙 포착 - {day_text}] 완벽한 거래량 고갈 & 수렴 후 반등!\n  - 진입가: {round(row['Close'], 2)}\n  - 목표가: {target1}\n  - 손절가: {stop}")
                recent_signal_found = True
                
            elif row.get('buy_swing_macd') and not recent_signal_found:
                score += 35
                stop = round(min(row['Low'], row['EMA_21']) * 0.99, 2)
                target1 = round(row['Close'] + row['ATR_14'] * 2.0, 2)
                signals.append(f"🔵 [MACD 스윙 포착 - {day_text}] 정배열 하에서 MACD 모멘텀 상승 반전!\n  - 진입가: {round(row['Close'], 2)}\n  - 목표가: {target1}\n  - 손절가: {stop}")
                recent_signal_found = True
                
            elif row.get('buy_short') and not row.get('buy_swing_vcp') and not row.get('buy_swing_macd') and not recent_signal_found:
                score += 25
                stop = round(min(row['Low'], row['EMA_21']) * 0.99, 2)
                target1 = round(row['Close'] + row['ATR_14'] * 1.5, 2)
                signals.append(f"🟩 [단기 매수 포착 - {day_text}] 21EMA 부근 기술적 양봉 반등 성공.\n  - 진입가: {round(row['Close'], 2)}\n  - 목표가: {target1}\n  - 손절가: {stop}")
                recent_signal_found = True

        if not recent_signal_found:
            signals.append("💬 최근 30일 내에 포착된 뚜렷한 매수 타점이 없습니다.")

        if current['EMA_21'] > current['EMA_50'] and current['EMA_50'] > current['EMA_200']:
            score += 10
            signals.append("✔ 현재 21일/50일/200일 이동평균선 완벽한 정배열 상승 추세입니다.")

        # RSI 과열 검사
        if current['RSI_14'] > 70:
            score -= 20
            signals.append("⚠️ RSI 70 초과 과매수 상태. 차익 실현 후 조정(풀백)을 기다리시길 권장합니다.")

        # 거시적 패턴 뼈대 ---------------------------------
        # 향후 200일선, 월봉 지지선 추세 분석 결과를 위 score와 signals에 융합할 구역입니다.
        macro_signal = "거시적(큰 그림) 분석 엔진은 현재 오프라인 상태(향후 연결 예정)입니다."
            
        display_ticker = f"{self.original_name} ({self.ticker})" if self.original_name != self.ticker else self.ticker
        self.analysis_result = {
            "ticker": display_ticker,
            "last_price": round(current['Close'], 2),
            "score": min(100, max(0, score)), # 0~100 사이
            "signals": signals,
            "macro_status": macro_signal
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
                "buy_short": bool(row['buy_short']),
                "buy_swing_macd": bool(row.get('buy_swing_macd', False)),
                "buy_swing_vcp": bool(row.get('buy_swing_vcp', False)),
                "rsi": row['RSI_14'] if not pd.isna(row['RSI_14']) else None,
                "atr": row['ATR_14'] if not pd.isna(row['ATR_14']) else None,
                "stop_price": round(min(row['Low'], row['EMA_21']) * 0.99, 2) if not pd.isna(row['EMA_21']) else None,
            })
        return chart_data
