# core/kiwoom_api.py

import requests
import json
import time
import traceback
import logging
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
from functools import wraps


# ======================================================
# 상수 정의
# ======================================================
class KiwoomApiConstants:
    """키움 API 상수"""
    
    # API Endpoints
    BASE_PROD = "https://api.kiwoom.com"
    BASE_MOCK = "https://mockapi.kiwoom.com"
    
    # API IDs
    API_CONDITION_LIST = "ka03001"
    API_STOCK_PRICE = "ka10006"
    API_HOGA = "ka10004"
    API_STOCK_INFO = "ka10100"
    API_BALANCE = "ka01690"
    API_BUY_ORDER = "kt10000"
    API_SELL_ORDER = "kt10001"
    
    # Response Codes (성공)
    SUCCESS_CODES = ("0", "00", "000")
    
    # Trade Types
    TRADE_TYPE_MARKET = "3"  # 시장가
    TRADE_TYPE_LIMIT = "1"   # 지정가
    
    # Exchange Type
    EXCHANGE_KRX = "KRX"
    
    # Timeouts
    TIMEOUT_LOGIN = 10.0
    TIMEOUT_DEFAULT = 5.0
    
    # Token
    TOKEN_REFRESH_MARGIN = 300  # 만료 5분 전 갱신


# ======================================================
# 데코레이터: 재시도 로직
# ======================================================
def retry_on_network_error(max_retries: int = 3, backoff: float = 1.0):
    """
    네트워크 오류 시 재시도 데코레이터
    
    Args:
        max_retries: 최대 재시도 횟수
        backoff: 초기 대기 시간 (지수 백오프)
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except requests.Timeout:
                    if attempt < max_retries - 1:
                        wait = backoff * (2 ** attempt)
                        logging.warning(
                            f"[재시도] {func.__name__} - "
                            f"{attempt + 1}/{max_retries} 시도 실패, "
                            f"{wait}초 후 재시도"
                        )
                        time.sleep(wait)
                    else:
                        logging.error(f"[재시도 실패] {func.__name__} - 최대 시도 횟수 초과")
                        raise
                except requests.RequestException as e:
                    if attempt < max_retries - 1:
                        wait = backoff * (2 ** attempt)
                        logging.warning(
                            f"[재시도] {func.__name__} - "
                            f"오류: {str(e)[:50]}, "
                            f"{wait}초 후 재시도"
                        )
                        time.sleep(wait)
                    else:
                        logging.error(f"[재시도 실패] {func.__name__} - {e}")
                        raise
            return None
        return wrapper
    return decorator


# ======================================================
# 메인 클래스
# ======================================================
class KiwoomApi:
    """
    키움증권 REST API 클라이언트
    
    주요 기능:
    - OAuth 토큰 발급 및 자동 갱신
    - 조건식 목록 조회
    - 현재가/호가 조회 (통합)
    - 계좌 잔고 조회
    - 매수/매도 주문 (시장가)
    
    개선 사항:
    - return_code 타입 안전성 강화
    - API 키 로깅 마스킹
    - 토큰 만료 시간 정확한 관리
    - 재시도 로직
    - 일관된 에러 처리
    """

    def __init__(self, app_key: str, app_secret: str, use_mock: bool = False):
        """
        초기화
        
        Args:
            app_key: 키움 앱 키
            app_secret: 키움 앱 시크릿
            use_mock: True면 모의투자 서버 사용
        """
        self.app_key = app_key
        self.app_secret = app_secret
        self.access_token: Optional[str] = None
        self.token_expires_at: float = 0
        
        # Base URL 설정
        self.BASE = (
            KiwoomApiConstants.BASE_MOCK if use_mock 
            else KiwoomApiConstants.BASE_PROD
        )
        
        # 로거 설정
        self.logger = logging.getLogger(self.__class__.__name__)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '[%(levelname)s][%(name)s] %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    # ======================================================
    # 유틸리티 메서드
    # ======================================================
    
    @staticmethod
    def _is_success(return_code: Any) -> bool:
        """
        return_code가 성공인지 안전하게 확인
        
        Args:
            return_code: API 응답의 return_code (정수 또는 문자열)
            
        Returns:
            성공 여부
        """
        return str(return_code) in KiwoomApiConstants.SUCCESS_CODES
    
    @staticmethod
    def _mask_sensitive(text: str, visible: int = 4) -> str:
        """
        민감 정보 마스킹 (로깅용)
        
        Args:
            text: 원본 텍스트
            visible: 앞에 보여줄 글자 수
            
        Returns:
            마스킹된 텍스트
        """
        if not text:
            return ""
        if len(text) <= visible:
            return "*" * len(text)
        return text[:visible] + "*" * (len(text) - visible)
    
    @staticmethod
    def _normalize_code(code: Optional[str]) -> str:
        """
        종목코드 정규화 (A 접두사 제거)
        
        Args:
            code: 원본 종목코드 (예: 'A005930' or '005930')
            
        Returns:
            정규화된 종목코드 (예: '005930')
        """
        if not code:
            return ""
        
        c = str(code).strip()
        if c.startswith("A"):
            c = c[1:]
        
        # 기본 검증 (6자리 숫자)
        if c and (not c.isdigit() or len(c) != 6):
            logging.warning(f"[종목코드 검증] 비정상 형식: {code}")
        
        return c
    
    @staticmethod
    def _validate_response(
        result: Optional[Dict[str, Any]], 
        operation: str
    ) -> Tuple[bool, str]:
        """
        API 응답 검증
        
        Args:
            result: API 응답 dict
            operation: 작업명 (로깅용)
            
        Returns:
            (성공 여부, 에러 메시지)
        """
        if not result:
            return False, f"{operation}: 응답 없음"
        
        return_code = result.get("return_code")
        if not KiwoomApi._is_success(return_code):
            msg = result.get("return_msg", "알 수 없는 오류")
            return False, f"{operation} 실패: {msg} (코드: {return_code})"
        
        return True, ""
    
    def _flatten_output(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """
        키움 REST 응답에서 output1[0] 등의 내용을 최상위로 풀어주는 헬퍼
        
        Args:
            body: API 응답 본문
            
        Returns:
            평탄화된 dict
        """
        if not isinstance(body, dict):
            return {}

        flat = {}

        # 1) output1, output2 내의 첫 번째 레코드를 평탄화
        for key in ("output1", "output2"):
            v = body.get(key)
            if isinstance(v, list) and v:
                first = v[0]
                if isinstance(first, dict):
                    flat.update(first)

        # 2) top-level 에 직접 들어있는 stck_* / acml_* 류가 있다면 포함
        for k, v in body.items():
            if k.startswith("stck_") or k.startswith("acml_") or k in (
                "stck_prpr", "stck_prdy_ctrt", "acml_vol", "flu_rt", "trde_qty",
            ):
                flat.setdefault(k, v)

        return flat

    # ======================================================
    # 인증 관련
    # ======================================================
    
    @retry_on_network_error(max_retries=3, backoff=1.0)
    def login(self) -> bool:
        """
        OAuth 토큰 발급
        
        Returns:
            성공 시 True, 실패 시 False
        """
        url = f"{self.BASE}/oauth2/token"
        headers = {"Content-Type": "application/json;charset=UTF-8"}

        data = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,
        }

        try:
            self.logger.info(f"토큰 발급 요청: {url}")
            self.logger.debug(f"appkey: {self._mask_sensitive(self.app_key)}")
            self.logger.debug(f"secretkey: {self._mask_sensitive(self.app_secret)}")

            response = requests.post(
                url, 
                headers=headers, 
                json=data, 
                timeout=KiwoomApiConstants.TIMEOUT_LOGIN
            )

            self.logger.info(f"HTTP {response.status_code}")

            if response.status_code != 200:
                try:
                    result = response.json()
                    self.logger.error(
                        f"로그인 실패: {result.get('return_msg', response.text[:200])}"
                    )
                except Exception:
                    self.logger.error(f"로그인 실패: {response.text[:500]}")
                return False

            result = response.json()

            # 디버그: 전체 응답
            try:
                self.logger.debug(
                    f"응답: {json.dumps(result, indent=2, ensure_ascii=False)}"
                )
            except Exception:
                self.logger.debug(f"응답: {result}")

            # 토큰 추출
            self.access_token = result.get("token")
            if not self.access_token:
                self.logger.error("로그인 실패: token 필드 없음")
                self.logger.error(f"return_code: {result.get('return_code')}")
                self.logger.error(f"return_msg: {result.get('return_msg')}")
                return False

            # ✅ 수정: return_code 안전 비교
            return_code = result.get("return_code")
            if not self._is_success(return_code):
                self.logger.error(f"로그인 실패: return_code={return_code}")
                self.logger.error(f"return_msg: {result.get('return_msg')}")
                return False

            # ✅ 추가: 토큰 만료 시간 파싱
            expires_dt = result.get("expires_dt", "")
            if expires_dt:
                try:
                    expires_time = datetime.strptime(expires_dt, "%Y%m%d%H%M%S")
                    self.token_expires_at = expires_time.timestamp()
                    self.logger.info(f"토큰 만료 시각: {expires_dt}")
                except Exception as e:
                    self.logger.warning(f"expires_dt 파싱 실패: {e}")
                    # fallback: 현재 + 1시간
                    self.token_expires_at = time.time() + 3600
            else:
                self.token_expires_at = time.time() + 3600

            self.logger.info("로그인 성공!")
            self.logger.debug(f"token_type: {result.get('token_type')}")
            self.logger.debug(
                f"토큰: {self._mask_sensitive(self.access_token, 10)}"
            )
            
            return True

        except Exception as e:
            self.logger.error(f"로그인 오류: {type(e).__name__}: {e}")
            traceback.print_exc()
            return False

    def ensure_token(self) -> None:
        """
        토큰 유효성 확인 및 필요 시 재발급
        - 만료 5분 전에 미리 갱신
        
        Raises:
            RuntimeError: 로그인 실패 시
        """
        # 만료 5분 전에 갱신
        margin = KiwoomApiConstants.TOKEN_REFRESH_MARGIN
        if self.access_token and time.time() < (self.token_expires_at - margin):
            return  # 토큰 유효

        self.logger.info("토큰 갱신 필요")
        if not self.login():
            raise RuntimeError("KiwoomApi 로그인 실패")

    # ======================================================
    # API 호출 헬퍼
    # ======================================================
    
    @retry_on_network_error(max_retries=2, backoff=0.5)
    def _call_mrkcond(self, api_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        /api/dostk/mrkcond 공통 호출 헬퍼
        
        Args:
            api_id: API ID (예: ka10006)
            params: 요청 파라미터
            
        Returns:
            응답 dict
        """
        self.ensure_token()

        url = self.BASE + "/api/dostk/mrkcond"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "cont-yn": "N",
            "next-key": "",
            "api-id": api_id,
        }

        try:
            resp = requests.post(
                url, 
                headers=headers, 
                json=params, 
                timeout=KiwoomApiConstants.TIMEOUT_DEFAULT
            )
        except requests.Timeout:
            self.logger.error(f"[{api_id}] 타임아웃")
            return {"return_code": "-1", "return_msg": "Timeout"}
        except Exception as e:
            self.logger.error(f"[{api_id}] 요청 오류: {e}")
            return {"return_code": "-1", "return_msg": str(e)}

        self.logger.debug(f"[{api_id}] HTTP {resp.status_code}")

        # ✅ 개선: JSON 파싱 실패 처리
        body = {}
        try:
            body = resp.json()
        except Exception as e:
            self.logger.error(f"[{api_id}] JSON 파싱 실패: {e}")
            self.logger.error(f"[{api_id}] 응답 텍스트: {resp.text[:200]}")
            return {
                "return_code": "-1",
                "return_msg": f"JSON 파싱 실패: {str(e)[:100]}",
                "raw_text": resp.text[:500],
            }

        if resp.status_code != 200:
            return {
                "return_code": "-1",
                "return_msg": f"HTTP {resp.status_code}",
                "raw": body,
            }

        # ✅ 수정: return_code 기본값 문자열로
        if "return_code" not in body:
            body["return_code"] = "0"

        return body

    # ======================================================
    # 조건식 목록 조회
    # ======================================================
    
    def get_condition_list(self) -> Dict[str, Any]:
        """
        조건식 목록 조회 (ka03001)
        
        Returns:
            조건식 목록 dict
        """
        self.ensure_token()

        url = self.BASE + "/api/dostk/mrkcond"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "api-id": KiwoomApiConstants.API_CONDITION_LIST,
        }

        try:
            self.logger.info("조건식 목록 요청 중...")
            resp = requests.post(
                url, 
                headers=headers, 
                json={}, 
                timeout=KiwoomApiConstants.TIMEOUT_DEFAULT
            )

            if resp.status_code != 200:
                self.logger.error(f"조건식 목록 조회 실패: HTTP {resp.status_code}")
                return {
                    "return_code": "-1", 
                    "return_msg": f"HTTP {resp.status_code}"
                }

            result = resp.json()

            # ✅ 수정: return_code 기본값
            if "return_code" not in result:
                result["return_code"] = "0"

            count = len(result.get('output1', []))
            self.logger.info(f"조건식 목록 조회 성공: {count}개")
            
            return result

        except Exception as e:
            self.logger.error(f"조건식 목록 조회 오류: {e}")
            traceback.print_exc()
            return {"return_code": "-1", "return_msg": str(e)}

    # ======================================================
    # 시세 조회
    # ======================================================
    
    def get_stock_price(self, stock_code: str) -> Dict[str, Any]:
        """
        종목 시세 통합 조회
        - ka10006: 현재가 / 등락률 / 거래량
        - ka10004: 매수/매도 1호가
        
        Args:
            stock_code: 종목코드 (예: '005930' or 'A005930')
            
        Returns:
            통합 시세 정보 dict
        """
        code = self._normalize_code(stock_code)
        params = {"stk_cd": code}

        # 1) ka10006 – 가격/등락률/거래량
        price_data = self._call_mrkcond(
            KiwoomApiConstants.API_STOCK_PRICE, 
            params
        )
        
        # ✅ 개선: 응답 검증
        success, error_msg = self._validate_response(price_data, "시세 조회")
        if not success:
            self.logger.error(error_msg)
            return {"return_code": "-1", "return_msg": error_msg}

        # 2) ka10004 – 1호가 정보 (실패해도 계속)
        hoga_data = self._call_mrkcond(KiwoomApiConstants.API_HOGA, params)
        if not self._is_success(hoga_data.get("return_code")):
            hoga_data = {}

        # 3) 평탄화
        flat_price = self._flatten_output(price_data)
        flat_hoga = self._flatten_output(hoga_data)

        # 4) 병합
        merged = {}
        merged.update(price_data)
        
        for k, v in hoga_data.items():
            if k not in ("return_code", "return_msg"):
                merged[k] = v

        for k, v in flat_price.items():
            merged[k] = v
            
        for k, v in flat_hoga.items():
            if k not in ("return_code", "return_msg"):
                merged.setdefault(k, v)

        merged["return_code"] = "0"
        merged.setdefault("return_msg", "OK")

        return merged

    # ======================================================
    # 계좌 잔고 조회
    # ======================================================
    
    def get_current_balance(self, qry_dt: Optional[str] = None) -> Dict[str, Any]:
        """
        계좌 잔고 조회 (ka01690)
        
        Args:
            qry_dt: 조회 일자 (YYYYMMDD), None이면 오늘
            
        Returns:
            잔고 정보 dict
        """
        self.ensure_token()

        url = self.BASE + "/api/dostk/acnt"

        if qry_dt is None:
            qry_dt = datetime.now().strftime("%Y%m%d")

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "api-id": KiwoomApiConstants.API_BALANCE,
        }

        params = {"qry_dt": qry_dt}

        try:
            self.logger.info(f"계좌 잔고 조회 중... (qry_dt={qry_dt})")
            resp = requests.post(
                url, 
                headers=headers, 
                json=params, 
                timeout=KiwoomApiConstants.TIMEOUT_DEFAULT
            )

            if resp.status_code != 200:
                self.logger.error(f"계좌 잔고 조회 실패: HTTP {resp.status_code}")
                return {
                    "return_code": "-1", 
                    "return_msg": f"HTTP {resp.status_code}"
                }

            result = resp.json()

            # ✅ 수정: return_code 기본값
            if "return_code" not in result:
                result["return_code"] = "0"

            if self._is_success(result.get("return_code")):
                dbst_bal_str = result.get("dbst_bal", "0")
                try:
                    dbst_bal = int(str(dbst_bal_str).replace(",", ""))
                except Exception:
                    dbst_bal = 0

                # 표준 필드명으로 통일
                result["ord_psbl_cash_amt"] = str(dbst_bal)
                result["can_order_amt"] = str(dbst_bal)
                result["d2_pymn_alow_amt"] = str(dbst_bal)

                self.logger.info(f"계좌 잔고 조회 성공: 매수가능금액 = {dbst_bal:,}원")
            else:
                self.logger.error(
                    f"계좌 잔고 조회 실패: {result.get('return_msg', 'N/A')}"
                )

            return result

        except Exception as e:
            self.logger.error(f"계좌 잔고 조회 오류: {e}")
            traceback.print_exc()
            return {"return_code": "-1", "return_msg": str(e)}

    # ======================================================
    # 종목 기본 정보 조회
    # ======================================================
    
    def get_stock_basic_info(self, stock_code: str) -> Dict[str, Any]:
        """
        종목 기본 정보 조회 (ka10100)
        
        Args:
            stock_code: 종목코드
            
        Returns:
            종목 정보 dict
        """
        code = self._normalize_code(stock_code)
        params = {"stk_cd": code}
        return self._call_mrkcond(KiwoomApiConstants.API_STOCK_INFO, params)

    # ======================================================
    # 매수 주문
    # ======================================================
    
    @retry_on_network_error(max_retries=2, backoff=0.3)
    def buy_market_order(
        self, 
        stock_code: str, 
        qty: int, 
        current_price: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        시장가 매수 주문 (kt10000)
        
        Args:
            stock_code: 종목코드
            qty: 주문 수량
            current_price: 참고 현재가 (사용 안 함, 호환성 유지)
            
        Returns:
            주문 결과 dict
        """
        self.ensure_token()

        code = self._normalize_code(stock_code)

        url = self.BASE + "/api/dostk/ordr"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "cont-yn": "N",
            "next-key": "",
            "api-id": KiwoomApiConstants.API_BUY_ORDER,
        }

        params = {
            "dmst_stex_tp": KiwoomApiConstants.EXCHANGE_KRX,
            "stk_cd": code,
            "ord_qty": str(qty),
            "ord_uv": "",  # 시장가이므로 공백
            "trde_tp": KiwoomApiConstants.TRADE_TYPE_MARKET,
            "cond_uv": "",
        }

        try:
            self.logger.info(f"매수 주문: {code} {qty}주 시장가")
            resp = requests.post(
                url, 
                headers=headers, 
                json=params, 
                timeout=KiwoomApiConstants.TIMEOUT_DEFAULT
            )

            self.logger.debug(f"매수 주문 HTTP {resp.status_code}")
            
            result = {}
            try:
                result = resp.json()
            except Exception as e:
                self.logger.error(f"매수 주문 JSON 파싱 실패: {e}")
                self.logger.error(f"응답 텍스트: {resp.text[:200]}")
                return {
                    "return_code": "-1", 
                    "return_msg": f"JSON 파싱 실패: {str(e)[:100]}"
                }

            # ✅ 수정: return_code 통일
            if "return_code" not in result:
                result["return_code"] = "0" if resp.status_code == 200 else "-1"

            # ✅ 수정: 안전한 성공 판정
            if self._is_success(result.get("return_code")):
                self.logger.info(f"매수 주문 성공: {result}")
            else:
                self.logger.error(f"매수 주문 실패: {result}")

            return result

        except Exception as e:
            self.logger.error(f"매수 주문 오류: {e}")
            traceback.print_exc()
            return {"return_code": "-1", "return_msg": str(e)}

    # ======================================================
    # 매도 주문
    # ======================================================
    
    @retry_on_network_error(max_retries=2, backoff=0.3)
    def sell_market_order(self, stock_code: str, qty: int) -> Dict[str, Any]:
        """
        시장가 매도 주문 (kt10001)
        
        Args:
            stock_code: 종목코드
            qty: 주문 수량
            
        Returns:
            주문 결과 dict
        """
        self.ensure_token()

        code = self._normalize_code(stock_code)

        url = self.BASE + "/api/dostk/ordr"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "cont-yn": "N",
            "next-key": "",
            "api-id": KiwoomApiConstants.API_SELL_ORDER,
        }

        params = {
            "dmst_stex_tp": KiwoomApiConstants.EXCHANGE_KRX,
            "stk_cd": code,
            "ord_qty": str(qty),
            "ord_uv": "",
            "trde_tp": KiwoomApiConstants.TRADE_TYPE_MARKET,
            "cond_uv": "",
        }

        try:
            self.logger.info(f"매도 주문: {code} {qty}주 시장가")
            resp = requests.post(
                url, 
                headers=headers, 
                json=params, 
                timeout=KiwoomApiConstants.TIMEOUT_DEFAULT
            )

            self.logger.debug(f"매도 주문 HTTP {resp.status_code}")
            
            result = {}
            try:
                result = resp.json()
            except Exception as e:
                self.logger.error(f"매도 주문 JSON 파싱 실패: {e}")
                self.logger.error(f"응답 텍스트: {resp.text[:200]}")
                return {
                    "return_code": "-1", 
                    "return_msg": f"JSON 파싱 실패: {str(e)[:100]}"
                }

            # ✅ 수정: return_code 통일
            if "return_code" not in result:
                result["return_code"] = "0" if resp.status_code == 200 else "-1"

            # ✅ 수정: 안전한 성공 판정
            if self._is_success(result.get("return_code")):
                self.logger.info(f"매도 주문 성공: {result}")
            else:
                self.logger.error(f"매도 주문 실패: {result}")

            return result

        except Exception as e:
            self.logger.error(f"매도 주문 오류: {e}")
            traceback.print_exc()
            return {"return_code": "-1", "return_msg": str(e)}


# ======================================================
# 편의 함수 (선택사항)
# ======================================================

def create_kiwoom_api(
    app_key: str, 
    app_secret: str, 
    use_mock: bool = False,
    log_level: int = logging.INFO
) -> KiwoomApi:
    """
    KiwoomApi 인스턴스 생성 헬퍼
    
    Args:
        app_key: 앱 키
        app_secret: 앱 시크릿
        use_mock: 모의투자 사용 여부
        log_level: 로그 레벨
        
    Returns:
        KiwoomApi 인스턴스
    """
    # 글로벌 로깅 설정
    logging.basicConfig(
        level=log_level,
        format='[%(levelname)s][%(asctime)s][%(name)s] %(message)s',
        datefmt='%H:%M:%S'
    )
    
    api = KiwoomApi(app_key, app_secret, use_mock)
    
    # 즉시 로그인
    if not api.login():
        raise RuntimeError("키움 API 로그인 실패")
    
    return api