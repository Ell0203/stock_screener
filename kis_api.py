"""
한국투자증권 Open API - 수급 데이터 모듈
- 투자자별 매매동향 (최근 N일)
- 공매도 잔고
- 일별 거래대금 (정확값)
"""

import requests
import datetime
import os

KIS_APP_KEY    = os.environ.get("KIS_APP_KEY", "")
KIS_APP_SECRET = os.environ.get("KIS_APP_SECRET", "")
KIS_BASE_URL   = "https://openapi.koreainvestment.com:9443"

_token_cache = {"token": None, "expires_at": None}

def get_access_token():
    now = datetime.datetime.now()
    if _token_cache["token"] and _token_cache["expires_at"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    res  = requests.post(
        f"{KIS_BASE_URL}/oauth2/tokenP",
        json={"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
        timeout=10
    )
    res.raise_for_status()
    token = res.json()["access_token"]
    _token_cache["token"]      = token
    _token_cache["expires_at"] = now + datetime.timedelta(hours=23)
    return token


def _get_headers(token, tr_id):
    return {
        "Content-Type":  "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        KIS_APP_KEY,
        "appsecret":     KIS_APP_SECRET,
        "tr_id":         tr_id,
        "custtype":      "P",
    }


def _safe_int(val):
    try:
        return int(str(val).replace(",", ""))
    except:
        return 0


def _safe_float(val):
    try:
        return float(str(val).replace(",", ""))
    except:
        return 0.0


def _fmt_date(d):
    if len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return d


# ============================================================
# 2. 투자자별 매매동향
# ============================================================
def get_investor_trend(stock_code, days=5):
    """
    TR: FHKST01010900
    URL: /uapi/domestic-stock/v1/quotations/inquire-investor  ← 공식 샘플 기준
    """
    try:
        token = get_access_token()
        end   = datetime.datetime.today()
        start = end - datetime.timedelta(days=days * 5 + 10)  # 공휴일/주말 넉넉히 포함

        res = requests.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers=_get_headers(token, "FHKST01010900"),
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd":         stock_code,
                "fid_div_cls_code":       "0",
                "fid_input_date_1":       start.strftime("%Y%m%d"),
                "fid_input_date_2":       end.strftime("%Y%m%d"),
            },
            timeout=10
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            print(f"[KIS] 투자자별 매매동향 오류: {data.get('msg1')}")
            return []

        result = []
        for row in data.get("output", [])[-days:]:
            result.append({
                "date":             _fmt_date(row.get("stck_bsop_date", "")),
                "foreign_net":      _safe_int(row.get("frgn_ntby_qty", 0)),
                "foreign_buy":      _safe_int(row.get("frgn_buy_qty",  0)),
                "foreign_sell":     _safe_int(row.get("frgn_sell_qty", 0)),
                "institution_net":  _safe_int(row.get("orgn_ntby_qty", 0)),
                "institution_buy":  _safe_int(row.get("orgn_buy_qty",  0)),
                "institution_sell": _safe_int(row.get("orgn_sell_qty", 0)),
                "individual_net":   _safe_int(row.get("indv_ntby_qty", 0)),
                "individual_buy":   _safe_int(row.get("indv_buy_qty",  0)),
                "individual_sell":  _safe_int(row.get("indv_sell_qty", 0)),
            })
        return result

    except Exception as e:
        print(f"[KIS] 투자자별 매매동향 수집 실패: {e}")
        return []


# ============================================================
# 3. 공매도 잔고
# ============================================================
def get_short_sale_balance(stock_code):
    """
    TR: FHPST04830000
    URL: /uapi/domestic-stock/v1/quotations/daily-short-sale
    ※ 404 발생 시 조용히 빈 dict 반환 (기능 자체를 막지 않음)
    """
    try:
        token = get_access_token()

        end   = datetime.datetime.today()
        start = end - datetime.timedelta(days=days * 5 + 10)

        res = requests.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/daily-short-sale",
            headers=_get_headers(token, "FHPST04830000"),
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd":         stock_code,
                "fid_input_date_1":       start.strftime("%Y%m%d"),
                "fid_input_date_2":       end.strftime("%Y%m%d"),
            },
            timeout=10
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            print(f"[KIS] 공매도 잔고 오류: {data.get('msg1')}")
            return {}

        output   = data.get("output", {})
        bal_qty  = _safe_int(output.get("ssts_resdl_qty",      0))
        prev_qty = _safe_int(output.get("bfdy_ssts_resdl_qty", 0))

        return {
            "balance_qty":      bal_qty,
            "balance_ratio":    _safe_float(output.get("ssts_resdl_rate", 0)),
            "balance_value":    _safe_int(output.get("ssts_resdl_amt",    0)),
            "prev_balance_qty": prev_qty,
            "change_qty":       bal_qty - prev_qty,
        }

    except Exception as e:
        print(f"[KIS] 공매도 잔고 수집 실패: {e}")
        return {}


# ============================================================
# 4. 일별 거래대금
# ============================================================
def get_daily_trade_value(stock_code, days=20):
    """
    TR: FHKST01010400
    URL: /uapi/domestic-stock/v1/quotations/inquire-daily-price
    Returns: { "YYYY-MM-DD": 거래대금(원), ... }
    """
    try:
        token = get_access_token()

        res = requests.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-price",
            headers=_get_headers(token, "FHKST01010400"),
            params={
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd":         stock_code,
                "fid_period_div_code":    "D",
                "fid_org_adj_prc":        "0",
            },
            timeout=10
        )
        res.raise_for_status()
        data = res.json()

        if data.get("rt_cd") != "0":
            print(f"[KIS] 거래대금 조회 오류: {data.get('msg1')}")
            return {}

        result = {}
        rows   = data.get("output2", [])
        for row in rows[-days:]:
            date  = row.get("stck_bsop_date", "")
            value = _safe_int(row.get("acml_tr_pbmn", 0))
            if date:
                result[_fmt_date(date)] = value

        return result

    except Exception as e:
        print(f"[KIS] 거래대금 조회 실패: {e}")
        return {}


# ============================================================
# 5. 통합 수급 조회
# ============================================================
def fetch_supply_data(stock_code, days=5):
    if not KIS_APP_KEY or not KIS_APP_SECRET:
        print("[KIS] API KEY가 설정되지 않았습니다.")
        return {"investor_trend": [], "short_balance": {}, "trade_value_map": {}}

    return {
        "investor_trend":  get_investor_trend(stock_code, days=days),
        "short_balance":   get_short_sale_balance(stock_code),
        "trade_value_map": get_daily_trade_value(stock_code, days=20),
    }
