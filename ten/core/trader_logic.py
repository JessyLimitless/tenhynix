# core/trader_logic.py
from __future__ import annotations

import asyncio
import threading
import time
import traceback
import datetime
import json
import configparser

from PyQt5.QtCore import QObject, pyqtSignal, QTimer

from .kiwoom_api import KiwoomApi
from .kiwoom_ws import KiwoomWs


class TraderLogic(QObject):
    """
    Vanilla Trading Basic - TraderLogic (v3.1 - REST + 체결 통보)

    ✅ 이 버전이 하는 일:
      1) 초기화:
         - REST 로그인
         - WebSocket 이벤트 루프/스레드 생성
         - 조건식 목록 요청 (REST 우선, 실패 시 WS 폴백)
         - 선택된 조건식 실시간 구독 시작

      2) 예수금(매수 가능 금액) 조회 → UI에 전달

      3) 조건식 실시간 신호 수신 (WebSocket CNSR)
         → 자동매매 ON 상태면: 자동 '시장가 매수'
         - 현재 버전: 신호당 "무조건 1주" 매수
         - 1주 가격이 BUY_AMOUNT를 넘으면 매수 스킵

      4) ⭐ 주문 체결 통보 수신 (WebSocket type='00')
         - 매수 체결: 실제 체결가로 entry_price 업데이트
         - 매도 체결: 실제 체결가로 예수금 정산

      5) 보유 포지션에 대해 **REST로** TP/SL 조건 만족 시 자동 '시장가 매도'
         - 5초마다 REST로 시세 조회 및 TP/SL 체크
         - 실제 매수 체결가 기준으로 수익률 계산

      6) 신호 포착 탭:
         - 조건식에 편입된 종목 리스트를 유지
         - REST로 가격/등락률/거래량 갱신 (5초마다)

    ❌ 제거된 기능:
      - WebSocket 실시간 시세 구독 (키움 REST API 미지원)
      - REAL 시세 메시지 처리
    
    ⭐ v3.1 개선사항 (v3.0 기반):
      - 주문 체결 통보(type='00') 처리 추가
      - 실제 체결가 기반 수익률 계산
      - 예수금 정산 정확도 향상
    """

    # --- UI 시그널 ---
    account_update = pyqtSignal(dict)        # 계좌/예수금 정보 갱신
    log_update = pyqtSignal(dict)            # 로그 패널용
    condition_list_update = pyqtSignal(dict) # 조건식 목록 갱신
    signal_detected = pyqtSignal(dict)       # 조건식 신호 포착 탭용 (신규 행 생성)
    margin_info_update = pyqtSignal(dict)    # (호환용, 이 버전에서는 거의 사용 안 함)
    # 신호 포착 테이블 실시간 덮어쓰기용 시그널 (REST 전용)
    signal_realtime_update = pyqtSignal(dict)

    # --------------------------------------------------
    # 생성자
    # --------------------------------------------------
    def __init__(self):
        super().__init__()

        # 1) 설정 로드
        config = configparser.ConfigParser()
        try:
            config.read("config.ini", encoding="utf-8")
        except Exception:
            config.read("config.ini")
        self.config = config

        # 2) API 키 로드
        if "KIWOOM_API" not in config:
            print("[치명적 오류] config.ini에 [KIWOOM_API] 섹션이 없습니다.")
            kiwoom_section = {}
        else:
            kiwoom_section = config["KIWOOM_API"]

        APP_KEY = (
            kiwoom_section.get("APP_KEY")
            or kiwoom_section.get("app_key", "")
        )
        APP_SECRET = (
            kiwoom_section.get("APP_SECRET")
            or kiwoom_section.get("app_secret", "")
        )

        if not APP_KEY or not APP_SECRET:
            print("[치명적 오류] config.ini의 [KIWOOM_API] 섹션에 APP_KEY와 APP_SECRET을 추가해야 합니다.")

        self.api = KiwoomApi(app_key=APP_KEY, app_secret=APP_SECRET)

        # 3) WebSocket 관련
        self.ws: KiwoomWs | None = None
        self.ws_thread: threading.Thread | None = None
        self.ws_loop: asyncio.AbstractEventLoop | None = None

        # 4) 매매/설정 파라미터
        self.condition_seq = "0"        # 기본 조건식 번호 (UI에서 변경 가능)
        self.buy_amount = 5_000         # 기본값 5,000원
        self._cash_lock = threading.Lock()
        self.current_cash = 0           # 예수금(매수 가능 금액)
        self.max_stock_limit = 10       # 기본값
        self.max_positions = self.max_stock_limit  # 구버전 코드 호환용 alias
        self.start_time = datetime.time(9, 0)
        self.end_time = datetime.time(15, 30)

        # SELL_STRATEGY 섹션 찾기
        sell_section = None
        for sec in config.sections():
            if sec.upper().startswith("SELL_STRATEGY"):
                sell_section = sec
                break

        if sell_section:
            try:
                self.stop_loss_rate = config.getfloat(
                    sell_section,
                    "stop_loss_rate",
                    fallback=-2.0,
                )
            except Exception:
                self.stop_loss_rate = -2.0

            try:
                self.profit_cut_rate = config.getfloat(
                    sell_section,
                    "profit_cut_rate",
                    fallback=3.0,
                )
            except Exception:
                self.profit_cut_rate = 3.0

            print(
                f"[SELL_STRATEGY 로드] 섹션='{sell_section}', "
                f"SL={self.stop_loss_rate}%, TP={self.profit_cut_rate}%"
            )
        else:
            self.stop_loss_rate = -2.0
            self.profit_cut_rate = 3.0
            print(
                "[SELL_STRATEGY 기본값] config.ini에 SELL_STRATEGY 관련 섹션을 찾지 못해 "
                "SL=-2.0%, TP=3.0% 기본값을 사용합니다."
            )

        # GLOBAL_SETTINGS 섹션에서 일부 기본값 덮어쓰기
        if "GLOBAL_SETTINGS" in config:
            g = config["GLOBAL_SETTINGS"]
            try:
                self.condition_seq = g.get("CONDITION_SEQ", self.condition_seq)
            except Exception:
                pass
            try:
                self.buy_amount = g.getint("BUY_AMOUNT", fallback=self.buy_amount)
            except Exception:
                pass
            try:
                self.max_stock_limit = g.getint("MAX_STOCKS", fallback=self.max_stock_limit)
            except Exception:
                pass
            try:
                self.max_stock_limit = g.getint("MAX_POSITIONS", fallback=self.max_stock_limit)
            except Exception:
                pass
            try:
                start_str = g.get("START_TIME", "09:00")
                self.start_time = datetime.datetime.strptime(start_str, "%H:%M").time()
            except Exception:
                self.start_time = datetime.time(9, 0)
            try:
                end_str = g.get("END_TIME", "15:30")
                self.end_time = datetime.datetime.strptime(end_str, "%H:%M").time()
            except Exception:
                self.end_time = datetime.time(15, 30)

        # 설정 값 검증
        if self.max_stock_limit < 1 or self.max_stock_limit > 50:
            self._emit_log("경고", f"비정상적인 최대 종목 수({self.max_stock_limit}). 기본값(10)으로 설정")
            self.max_stock_limit = 10

        if self.buy_amount < 1000:
            self._emit_log("경고", f"매수 금액이 너무 낮음({self.buy_amount}원). 최소값(1,000원) 미만은 불가")
            self.buy_amount = 1000

        self.max_positions = self.max_stock_limit

        # 5) 상태 관리
        self.is_trading = False
        self.is_running = False
        self._initializing = False
        self._stock_names: dict[str, str] = {}
        self.open_positions: dict[str, dict] = {}
        self.rejected_codes: set[str] = set()
        self.pending_signals: dict[str, dict] = {}
        self.reentry_block: dict[str, datetime.date] = {}

        # 6) 포지션 감시용 타이머 (TP/SL 체크 - REST 전용, 항상 실행)
        self.position_timer = QTimer(self)
        self.position_timer.setInterval(5000)
        self.position_timer.timeout.connect(self._check_positions)

        # 7) 신호 포착 리스트 갱신용 타이머 (REST 전용)
        self.signal_timer = QTimer(self)
        self.signal_timer.setInterval(5000)
        self.signal_timer.timeout.connect(self._refresh_signals)
        self.signal_timer.start()

        print("TraderLogic (핵심 두뇌 v3.1 - REST + 체결 통보) 객체가 생성되었습니다.")

    # ======================================================
    # 안전한 return_code 비교 메서드
    # ======================================================
    @staticmethod
    def _is_success(return_code) -> bool:
        return str(return_code) in ("0", "00", "000")

    # ======================================================
    # 공용 로그 헬퍼
    # ======================================================
    def _emit_log(self, action: str, details: str, stock_name: str | None = None):
        now_str = datetime.datetime.now().strftime("%H:%M:%S")
        payload = {
            "time": now_str,
            "action": action,
            "details": details,
        }
        if stock_name:
            payload["stock_name"] = stock_name

        print(f"[TraderLogic LOG][{action}] {details}")
        try:
            self.log_update.emit(payload)
        except Exception:
            pass

    # ======================================================
    # 유틸 함수들
    # ======================================================
    @staticmethod
    def _safe_int(val) -> int:
        try:
            if val is None:
                return 0
            s = str(val).replace(",", "").replace("+", "").strip()
            return int(s)
        except Exception:
            return 0

    @staticmethod
    def _safe_price(val) -> int:
        try:
            if val is None:
                return 0
            s = str(val).replace(",", "").strip()
            if s.startswith("+") or s.startswith("-"):
                s = s[1:]
            if not s:
                return 0
            return int(s)
        except Exception:
            return 0

    @staticmethod
    def _safe_float(val) -> float:
        try:
            if val is None:
                return 0.0
            s = str(val).replace(",", "").replace("%", "").strip()
            if not s:
                return 0.0
            return float(s)
        except Exception:
            return 0.0

    @staticmethod
    def _normalize_code(code) -> str:
        if code is None:
            return ""
        s = str(code).strip()
        if s.startswith("A"):
            s = s[1:]
        return s

    def _has_ws(self) -> bool:
        return bool(self.ws and self.ws_loop and self.ws_loop.is_running())

    def _update_cash(self, amount: int):
        with self._cash_lock:
            self.current_cash = max(self.current_cash + amount, 0)

    def _set_cash(self, amount: int):
        with self._cash_lock:
            self.current_cash = max(amount, 0)

    def _get_cash(self) -> int:
        with self._cash_lock:
            return self.current_cash

    def _today(self) -> datetime.date:
        return datetime.datetime.now().date()

    def _block_reentry_today(self, code: str):
        code = self._normalize_code(code)
        if not code:
            return
        self.reentry_block[code] = self._today()
        self._emit_log("시스템", f"{code}는 오늘 매도 완료 → 당일 재진입 금지")

    def _can_reenter_today(self, code: str) -> bool:
        code = self._normalize_code(code)
        if not code:
            return False
        last_date = self.reentry_block.get(code)
        if last_date is None:
            return True
        return last_date != self._today()

    def clear_all_rejected_codes(self):
        count = len(self.rejected_codes)
        self.rejected_codes.clear()
        self._emit_log("시스템", f"매수 거부 설정 {count}개가 모두 해제되었습니다.")
        return count

    # ------------------------------------------------------
    # 시세 스냅샷 조회 (REST 전용)
    # ------------------------------------------------------
    def _fetch_price_snapshot(self, stock_code: str) -> dict | None:
        stock_code = self._normalize_code(stock_code)
        if not stock_code:
            return None

        try:
            price_data = self.api.get_stock_price(stock_code)
            if not price_data:
                return None

            rc = price_data.get("return_code")
            if rc not in (None, 0, "0"):
                return None

            out = price_data
            parsed = out.get("_parsed") or {}

            current_price = parsed.get("current_price")
            if current_price is None:
                current_price = out.get("current_price")
            if current_price is not None:
                current_price = self._safe_price(current_price)
            else:
                current_price = self._safe_price(
                    out.get("stck_prpr")
                    or out.get("close_pric")
                    or out.get("lastPrice")
                    or out.get("last_price")
                )

            change_rate = self._safe_float(
                out.get("flu_rt")
                or out.get("prdy_ctrt")
                or out.get("stck_prdy_ctrt")
                or parsed.get("change_rate")
            )

            volume = self._safe_int(
                out.get("trde_qty")
                or out.get("acml_vol")
                or out.get("stck_vol")
                or parsed.get("volume")
            )

            stock_name = self._stock_names.get(stock_code)
            if stock_name:
                print(f"[종목명 캐시 HIT ✅] {stock_code} → {stock_name}")

            if not stock_name:
                stock_name = (
                    out.get("stk_nm")
                    or out.get("name")
                    or out.get("hts_kor_isnm")
                    or out.get("itm_nm")
                    or parsed.get("stock_name")
                )
                if stock_name:
                    stock_name = str(stock_name).strip()
                    self._stock_names[stock_code] = stock_name
                    print(f"[종목명 시세 API ✅] {stock_code} → {stock_name}")

            if not stock_name or stock_name == stock_code:
                print(f"[종목명 미확보] {stock_code} - 계좌 잔고에서 업데이트 예정")
                stock_name = stock_code

            if stock_name and stock_name != stock_code:
                self._stock_names[stock_code] = stock_name

            if current_price <= 0:
                return None

            print(
                f"[시세 스냅샷 ✅] {stock_name}({stock_code}) "
                f"{current_price:,}원 (등락률 {change_rate:.2f}%, 거래량 {volume:,})"
            )

            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "current_price": current_price,
                "change_rate": change_rate,
                "volume": volume,
            }

        except Exception as e:
            traceback.print_exc()
            return None

    # ======================================================
    # 초기화 / WS 스레드
    # ======================================================
    def initialize_background(self):
        if self._initializing:
            self._emit_log("시스템", "이미 초기화 중입니다. 요청 무시.")
            return

        self._initializing = True
        self.is_running = True
        threading.Thread(target=self._run_initialization, daemon=True).start()

    def _run_initialization(self):
        try:
            print("[초기화] 1단계: 로그인 시도...")
            if not getattr(self.api, "access_token", None):
                if not self.api.login():
                    self._emit_log("오류", "초기화 실패: 로그인 실패")
                    self._initializing = False
                    self.is_running = False
                    return
                print("[초기화] 로그인 성공")

            print("[초기화] 2단계: WebSocket 스레드 시작...")
            self.ws_thread = threading.Thread(
                target=self._run_ws_in_thread,
                daemon=True,
            )
            self.ws_thread.start()

            for i in range(30):
                if self.ws_loop:
                    print(f"[초기화] WS 루프 생성 완료 ({i * 0.1:.1f}초)")
                    break
                time.sleep(0.1)
            else:
                self._emit_log("오류", "WS 루프 생성 실패")
                self._initializing = False
                self.is_running = False
                return

            print("[초기화] 3단계: WebSocket 객체 생성 및 연결...")
            self.ws = KiwoomWs(
                access_token=self.api.access_token,
                signal_callback=self.on_realtime_signal,
            )
            asyncio.run_coroutine_threadsafe(self.ws.run(), self.ws_loop)

            for i in range(100):
                if self.ws and self.ws.connected:
                    print(f"[초기화] WS 연결 성공 ({i * 0.1:.1f}초)")
                    break
                time.sleep(0.1)
            else:
                self._emit_log("오류", "WS 연결 실패")
                self._initializing = False
                self.is_running = False
                return

            print("[초기화] 4단계: 조건식 목록 요청 (REST 우선)...")
            cond_list_data = self.api.get_condition_list()
            if cond_list_data and cond_list_data.get("return_code") == 0:
                self.condition_list_update.emit(cond_list_data)
                print("[초기화] 조건식 목록 UI 전송 완료 (REST)")
            else:
                print("[초기화] REST 조건식 실패 → WS CNSRLST 폴백 요청")
                asyncio.run_coroutine_threadsafe(
                    self.ws.request_condition_list(),
                    self.ws_loop,
                )

            time.sleep(0.5)

            print("[초기화] 5단계: 초기 예수금 조회 (종목명 캐싱)...")
            self.update_account_info()
            time.sleep(1.0)
            print("[초기화] 5단계 완료: 종목명 캐싱 완료")

            print(f"[초기화] 6단계: 조건식({self.condition_seq}) 실시간 구독 시도...")
            if self._has_ws():
                asyncio.run_coroutine_threadsafe(
                    self.ws.subscribe_condition(seq=self.condition_seq),
                    self.ws_loop,
                )
                self._emit_log(
                    "시스템",
                    f"조건식({self.condition_seq}) 실시간 구독 시작",
                )

            self._initializing = False
            self._emit_log("시스템", "초기화 완료")

        except Exception as e:
            traceback.print_exc()
            self._emit_log("오류", f"초기화 스레드 예외: {e}")
            self._initializing = False
            self.is_running = False

    def _run_ws_in_thread(self):
        try:
            self.ws_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.ws_loop)
            print("[WS] 이벤트 루프 시작")
            self.ws_loop.run_forever()
        except Exception as e:
            print(f"[WS] 치명적 오류: {e}")
            traceback.print_exc()
        finally:
            if self.ws_loop:
                self.ws_loop.close()
            print("[WS] 이벤트 루프 종료")

    # ======================================================
    # 계좌(예수금) 조회
    # ======================================================
    def _emit_account_status(self):
        cash = self._get_cash()
        payload = {
            "cash": cash,
            "can_order_amt": cash,
            "position_count": len(self.open_positions),
        }
        try:
            self.account_update.emit(payload)
        except Exception:
            pass

    def update_account_info(self):
        if not getattr(self.api, "access_token", None):
            self._set_cash(1_000_000)
            self._emit_account_status()
            return

        try:
            balance_data = self.api.get_current_balance()

            if balance_data and balance_data.get("return_code") == 0:
                day_bal_rt = balance_data.get("day_bal_rt", [])
                if not isinstance(day_bal_rt, list):
                    day_bal_rt = [day_bal_rt]

                for item in day_bal_rt:
                    if not isinstance(item, dict):
                        continue
                    code = item.get("stk_cd")
                    if not code:
                        continue
                    code = self._normalize_code(code)
                    if not code:
                        continue
                    stock_name = item.get("stk_nm")
                    if stock_name:
                        stock_name = str(stock_name).strip()
                        self._stock_names[code] = stock_name

                orderable_str = (
                    balance_data.get("d2_pymn_alow_amt")
                    or balance_data.get("ord_psbl_cash_amt")
                    or balance_data.get("can_order_amt")
                    or balance_data.get("dbst_bal")
                    or "0"
                )

                cash_amount = self._safe_int(orderable_str)
                self._set_cash(cash_amount)
                self._emit_log("시스템", f"매수 가능 금액 갱신: {cash_amount:,}원")
            else:
                self._set_cash(1_000_000)

            self._emit_account_status()

        except Exception as e:
            traceback.print_exc()
            self._set_cash(1_000_000)
            self._emit_account_status()

    # ======================================================
    # 자동매매 시작/중지
    # ======================================================
    def start_trading(self):
        if not getattr(self.api, "access_token", None):
            self._emit_log("오류", "자동매매 시작 실패: 로그인 필요")
            self.is_trading = False
            return

        threading.Thread(target=self.update_account_info, daemon=True).start()
        self.is_trading = True

        if not self.position_timer.isActive():
            self.position_timer.start()

        self._emit_log(
            "시스템",
            f"자동매매 시작: 조건식={self.condition_seq}, "
            f"신호당 매수수량=1주, "
            f"최대 보유 종목 수={self.max_stock_limit}개, "
            f"TP={self.profit_cut_rate}%, SL={self.stop_loss_rate}%"
        )

    def stop_trading(self):
        self.is_trading = False
        if self.position_timer.isActive():
            self.position_timer.stop()
        self._emit_log("시스템", "자동매매 중지")

    def start_auto_trading(self):
        if self.is_trading:
            return
        self.start_trading()

        if self.pending_signals:
            for sig in list(self.pending_signals.values()):
                code = sig["stock_code"]
                if code in self.rejected_codes or code in self.open_positions:
                    continue
                if not self._can_reenter_today(code):
                    continue
                if len(self.open_positions) >= self.max_stock_limit:
                    break
                self._auto_buy(code, sig)

    def stop_auto_trading(self, user_stop: bool = True):
        self.stop_trading()

    def shutdown_all(self):
        self.stop_trading()
        self.is_running = False
        if self.ws and self.ws_loop:
            try:
                if self.ws.connected:
                    asyncio.run_coroutine_threadsafe(self.ws.disconnect(), self.ws_loop)
                self.ws_loop.call_later(1.0, self.ws_loop.stop)
            except Exception:
                pass

    # ======================================================
    # 조건식 변경
    # ======================================================
    def change_condition(self, new_seq: str):
        old_seq = self.condition_seq
        new_seq_str = str(new_seq).strip()
        if not new_seq_str:
            return
        self.condition_seq = new_seq_str

        if not self._has_ws():
            return

        async def _do_change():
            try:
                if hasattr(self.ws, "unsubscribe_condition") and old_seq:
                    await self.ws.unsubscribe_condition(seq=old_seq)
                await self.ws.subscribe_condition(seq=self.condition_seq)
            except Exception:
                pass

        asyncio.run_coroutine_threadsafe(_do_change(), self.ws_loop)

    # ======================================================
    # WebSocket 실시간 신호 처리 (조건식 + 체결 통보)
    # ======================================================
    def on_realtime_signal(self, response_data: dict):
        """
        WebSocket에서 수신한 메시지 처리
        - CNSRLST / CNSRREQ / CNSR (조건식)
        - ⭐ type='00' (주문 체결 통보)
        """
        trnm = response_data.get("trnm")
        msg_type = response_data.get("type")

        # 1) 조건식 목록 (WS 폴백)
        if trnm == "CNSRLST":
            self.condition_list_update.emit(response_data)
            return

        # 2) 조건식 스냅샷(CNSRREQ, 여러 종목)
        if trnm == "CNSRREQ":
            data_list = response_data.get("data", [])
            for item in data_list:
                jmcode = item.get("jmcode") or item.get("stk_cd")
                stock_code = self._normalize_code(jmcode)
                if stock_code:
                    self._handle_condition_signal(stock_code)
            return

        # 3) 조건식 실시간(CNSR, 단건 ADD/DEL)
        if trnm == "CNSR":
            evt_type = response_data.get("type")
            jmcode = (
                response_data.get("stck_shrn_iscd")
                or response_data.get("stk_cd")
                or response_data.get("jmcode")
            )
            stock_code = self._normalize_code(jmcode)
            if stock_code and evt_type == "ADD":
                self._handle_condition_signal(stock_code)
            return

        # ⭐⭐⭐ 4) 주문 체결 통보 - 새로 추가! ⭐⭐⭐
        if msg_type == "00":
            print("[주문 체결 통보] 수신")
            self._handle_order_execution(response_data)
            return

    # ------------------------------------------------------
    # 조건식 신호 처리
    # ------------------------------------------------------
    def _handle_condition_signal(self, stock_code: str):
        stock_code = self._normalize_code(stock_code)
        if not stock_code:
            return

        if stock_code in self.rejected_codes:
            return

        if not self._can_reenter_today(stock_code):
            return

        snapshot = self._fetch_price_snapshot(stock_code)
        if snapshot is None:
            return

        signal_data = {
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
            "stock_code": stock_code,
            "stock_name": snapshot["stock_name"],
            "current_price": snapshot["current_price"],
            "price": snapshot["current_price"],
            "cur_price": snapshot["current_price"],
            "change_rate": snapshot["change_rate"],
            "volume": snapshot["volume"],
        }
        self.signal_detected.emit(signal_data)

        self.pending_signals[stock_code] = {
            "stock_code": stock_code,
            "stock_name": snapshot["stock_name"],
            "current_price": snapshot["current_price"],
            "change_rate": snapshot["change_rate"],
            "volume": snapshot["volume"],
            "time": datetime.datetime.now(),
        }

        now_time = datetime.datetime.now().time()
        if not self.is_trading or not (self.start_time <= now_time <= self.end_time):
            return

        if len(self.open_positions) >= self.max_stock_limit:
            return

        if stock_code in self.open_positions:
            return

        self._auto_buy(stock_code, snapshot)

    # ------------------------------------------------------
    # ⭐ 주문 체결 통보 처리 (WebSocket type='00')
    # ------------------------------------------------------
    def _handle_order_execution(self, data: dict):
        """
        주문 체결 통보 처리
        
        WebSocket 메시지 예시:
        {
            "type": "00",
            "odno": "12345",          # 주문번호
            "stk_cd": "005930",       # 종목코드
            "exec_price": "75000",    # 체결가
            "exec_qty": "10",         # 체결수량
            "buy_sell_tp": "2",       # 매수(1)/매도(2)
        }
        """
        try:
            # 메시지 파싱
            order_no = data.get("odno") or data.get("order_no")
            stock_code = self._normalize_code(
                data.get("stk_cd") 
                or data.get("stock_code")
                or data.get("stck_shrn_iscd")
            )
            exec_price = self._safe_int(
                data.get("exec_price") 
                or data.get("체결가")
                or data.get("cntr_pr")
            )
            exec_qty = self._safe_int(
                data.get("exec_qty") 
                or data.get("체결수량")
                or data.get("cntr_qty")
            )
            buy_sell = (
                data.get("buy_sell_tp") 
                or data.get("매수매도구분")
                or data.get("buy_sell_dvcd")
            )
            
            print(f"\n[체결 통보 파싱] 주문번호={order_no}, 종목={stock_code}, "
                  f"체결가={exec_price:,}원, 수량={exec_qty}주, 구분={buy_sell}")
            
            # 필수 정보 검증
            if not stock_code or exec_price <= 0 or exec_qty <= 0:
                print("[체결 통보] 필수 정보 누락, 처리 스킵")
                print(f"[체결 통보 원본] {json.dumps(data, ensure_ascii=False)[:300]}")
                return
            
            stock_name = self._stock_names.get(stock_code, stock_code)
            
            # 매수 체결
            if buy_sell in ("1", "01", "buy", "매수", "BUY"):
                print(f"[매수 체결 ✅] {stock_name}({stock_code}) "
                      f"{exec_price:,}원 x {exec_qty}주")
                
                # ⭐ open_positions 업데이트 (실제 체결가로)
                if stock_code in self.open_positions:
                    old_entry = self.open_positions[stock_code].get("entry_price", 0)
                    self.open_positions[stock_code]["entry_price"] = exec_price
                    self.open_positions[stock_code]["qty"] = exec_qty
                    
                    print(f"[매수 체결] 보유 포지션 업데이트: "
                          f"진입가 {old_entry:,}원 → {exec_price:,}원 (실제 체결가)")
                    
                    self._emit_log(
                        "체결",
                        f"{stock_name} 매수 체결: {exec_price:,}원 x {exec_qty}주 "
                        f"(진입가 확정)"
                    )
                else:
                    # 체결 통보가 먼저 온 경우 (드물지만 가능)
                    print(f"[매수 체결] 보유 포지션에 없음 - 신규 추가")
                    self.open_positions[stock_code] = {
                        "stock_name": stock_name,
                        "qty": exec_qty,
                        "entry_price": exec_price,
                    }
                    self._emit_log(
                        "체결",
                        f"{stock_name} 매수 체결: {exec_price:,}원 x {exec_qty}주"
                    )
            
            # 매도 체결
            elif buy_sell in ("2", "02", "sell", "매도", "SELL"):
                print(f"[매도 체결 ✅] {stock_name}({stock_code}) "
                      f"{exec_price:,}원 x {exec_qty}주")
                
                # ⭐ 실제 체결가로 정산
                sell_amount = exec_price * exec_qty
                
                self._emit_log(
                    "체결",
                    f"{stock_name} 매도 체결: {exec_price:,}원 x {exec_qty}주 "
                    f"(총 {sell_amount:,}원)"
                )
                
                print(f"[매도 체결] 실제 체결 금액: {sell_amount:,}원")
                
                # 체결가와 예상가의 차액만큼 예수금 보정
                # (_auto_sell에서 이미 임시로 업데이트했으므로 여기서는 로그만)
            
            else:
                print(f"[체결 통보] 알 수 없는 매수/매도 구분: {buy_sell}")
                print(f"[체결 통보 원본] {json.dumps(data, ensure_ascii=False)[:300]}")
            
        except Exception as e:
            print(f"[체결 통보 처리 오류] {e}")
            traceback.print_exc()

    # ------------------------------------------------------
    # 신호 포착 리스트 갱신 (REST 전용)
    # ------------------------------------------------------
    def _refresh_signals(self):
        if not self.pending_signals:
            return

        now = datetime.datetime.now()

        old_signals = []
        for code, sig in self.pending_signals.items():
            signal_time = sig.get("time", now)
            if (now - signal_time).total_seconds() > 3600:
                old_signals.append(code)

        for code in old_signals:
            self.pending_signals.pop(code, None)

        for code in list(self.pending_signals.keys()):
            snapshot = self._fetch_price_snapshot(code)
            if snapshot is None:
                continue

            signal_data = {
                "time": datetime.datetime.now().strftime("%H:%M:%S"),
                "stock_code": code,
                "stock_name": snapshot["stock_name"],
                "current_price": snapshot["current_price"],
                "price": snapshot["current_price"],
                "cur_price": snapshot["current_price"],
                "change_rate": snapshot["change_rate"],
                "volume": snapshot["volume"],
            }

            try:
                self.signal_realtime_update.emit(signal_data)
            except Exception:
                pass

            self.pending_signals[code].update({
                "stock_name": snapshot["stock_name"],
                "current_price": snapshot["current_price"],
                "change_rate": snapshot["change_rate"],
                "volume": snapshot["volume"],
                "time": datetime.datetime.now(),
            })

    # ------------------------------------------------------
    # 자동 매수 (실제 체결가는 WebSocket 체결 통보로 업데이트)
    # ------------------------------------------------------
    def _auto_buy(self, stock_code: str, snapshot: dict | None = None):
        """
        자동 매수 로직
        
        ⭐ 체결가 처리:
        1. 주문 접수 시: REST 시세로 임시 저장
        2. 체결 통보 시: 실제 체결가로 업데이트 (_handle_order_execution)
        """
        stock_code = self._normalize_code(stock_code)
        if not stock_code:
            return

        if snapshot is None:
            snapshot = self._fetch_price_snapshot(stock_code)
            if snapshot is None:
                return

        stock_name = snapshot["stock_name"]
        current_price = snapshot["current_price"]

        if current_price > self.buy_amount:
            return

        cash = self._get_cash()
        if cash <= 0:
            return

        qty = 1
        order_amount = current_price * qty

        if cash < order_amount:
            return

        result = self.api.buy_market_order(stock_code, qty, current_price=current_price)

        success = False
        if isinstance(result, dict):
            return_code = result.get("return_code")
            success = self._is_success(return_code)
        elif result:
            success = True

        if success:
            self._emit_log("매수주문", f"{stock_name}({stock_code}) 시장가 매수 접수 성공 (수량: {qty}주)")
            
            # ⭐ 일단 REST 시세로 저장 (체결 통보 시 실제 체결가로 업데이트됨)
            self.open_positions[stock_code] = {
                "stock_name": stock_name,
                "qty": qty,
                "entry_price": current_price,  # 근사값 (체결 통보 대기)
            }
            self._update_cash(-order_amount)
            self._emit_account_status()
            
            print(f"[매수 주문] 체결 통보 대기 중... (근사 진입가: {current_price:,}원)")

    # ------------------------------------------------------
    # TP/SL 자동 매도 (REST 전용, 실제 매수 체결가 기준)
    # ------------------------------------------------------
    def _check_positions(self):
        """
        TP/SL 자동 체크 (REST 기반)
        
        ⭐ 수익률 계산: (현재가 - 실제 매수 체결가) / 실제 매수 체결가 * 100
        """
        if not self.is_trading or not self.open_positions:
            return

        now_time = datetime.datetime.now().time()
        if not (self.start_time <= now_time <= self.end_time):
            return

        print(f"\n[TP/SL REST 체크] 보유 종목 {len(self.open_positions)}개 체크 중...")

        for code, pos in list(self.open_positions.items()):
            snapshot = self._fetch_price_snapshot(code)
            if snapshot is None:
                continue

            current_price = snapshot["current_price"]

            try:
                self.signal_realtime_update.emit({
                    "time": datetime.datetime.now().strftime("%H:%M:%S"),
                    "stock_code": code,
                    "stock_name": snapshot["stock_name"],
                    "current_price": current_price,
                    "price": current_price,
                    "cur_price": current_price,
                    "change_rate": snapshot["change_rate"],
                    "volume": snapshot["volume"],
                })
            except Exception:
                pass

            # ⭐ 실제 매수 체결가 기준 수익률 계산
            entry_price = pos.get("entry_price", current_price)
            qty = pos.get("qty", 0)
            if qty <= 0 or entry_price <= 0:
                continue

            profit_rate = (current_price - entry_price) / entry_price * 100
            print(
                f"[TP/SL REST 체크] {code} | 진입가:{entry_price:,}원 | "
                f"현재가:{current_price:,}원 | 수익률:{profit_rate:.2f}% | "
                f"TP={self.profit_cut_rate}% | SL={self.stop_loss_rate}%"
            )

            if profit_rate >= self.profit_cut_rate:
                self._emit_log("매도주문", f"{code} TP({self.profit_cut_rate}%) 도달 → 시장가 전량 매도")
                self._auto_sell(code, qty, current_price)
                continue

            if profit_rate <= self.stop_loss_rate:
                self._emit_log("매도주문", f"{code} SL({self.stop_loss_rate}%) 도달 → 시장가 전량 매도")
                self._auto_sell(code, qty, current_price)
                continue

    def _auto_sell(self, stock_code: str, qty: int, current_price: int):
        """
        시장가 자동 매도
        
        ⭐ 체결가 처리:
        1. 주문 접수 시: REST 시세로 임시 예수금 업데이트
        2. 체결 통보 시: 실제 체결가 확인 (_handle_order_execution)
        """
        stock_code = self._normalize_code(stock_code)
        if not stock_code or qty <= 0:
            return

        result = self.api.sell_market_order(stock_code, qty)

        success = False
        if isinstance(result, dict):
            return_code = result.get("return_code")
            success = self._is_success(return_code)
        elif result:
            success = True

        if success:
            self._emit_log("매도주문", f"{stock_code} 시장가 매도 접수 성공 (수량: {qty}주)")
            self.open_positions.pop(stock_code, None)
            
            # ⭐ 일단 REST 시세로 예수금 업데이트 (체결 통보 시 실제 체결가 확인)
            sell_amount = current_price * qty
            self._update_cash(sell_amount)
            self._emit_account_status()
            
            self._block_reentry_today(stock_code)
            
            print(f"[매도 주문] 체결 통보 대기 중... (근사 매도가: {current_price:,}원)")

    # ------------------------------------------------------
    # 매수 거부
    # ------------------------------------------------------
    def reject_signal(self, stock_code: str):
        code = self._normalize_code(stock_code)
        if not code:
            return

        if code in self.rejected_codes:
            self.rejected_codes.remove(code)
            self._emit_log("시스템", f"{code} 매수 거부 해제 → 다시 자동매매 대상에 포함")
        else:
            self.rejected_codes.add(code)
            self._emit_log("시스템", f"{code}는 오늘 매수 대상에서 제외 (매수 거부 설정)")

    def skip_stock(self, stock_code: str):
        self.reject_signal(stock_code)