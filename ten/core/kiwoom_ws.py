# core/kiwoom_ws.py
import asyncio
import json
import traceback
from typing import Callable, Optional

import websockets
from websockets.exceptions import ConnectionClosed


class KiwoomWs:
    """
    키움증권 WebSocket 클라이언트 (v3.1 - 조건식 + 주문체결)

    ✅ 기능:
    1) LOGIN 메시지로 인증
    2) 조건식 목록 조회 (CNSRLST)
    3) 조건식 실시간 구독 / 해제 (CNSRREQ / CNSRCLR)
    4) 조건식 실시간 신호 수신 (CNSR)
    5) ⭐ 주문 체결 통보 수신 (type='00')
    6) PING/PONG 자동 처리
    7) 지수 백오프 재연결 로직
    8) HEARTBEAT 로그로 WebSocket 상태 주기 출력

    ❌ 제거된 기능 (REST로 대체):
    - 실시간 시세 구독 (REG, type=0A) → 키움 REST API는 미지원
    - REAL 시세 메시지 처리 → REST 폴링으로 대체
    
    ⭐ v3.1 개선사항 (v3.0 기반):
    - 주문 체결 통보(type='00') 처리 복원
    - 실제 체결가 확인 가능
    - 조건식 + 체결 알림만 처리 (시세는 REST)
    """

    # SOCKET_URL = 'wss://mockapi.kiwoom.com:10000/api/dostk/websocket'  # 모의투자
    SOCKET_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"  # 실전투자

    def __init__(
        self,
        access_token: str,
        signal_callback: Optional[Callable] = None,
    ):
        self.access_token = access_token
        self.signal_callback = signal_callback
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        self.logged_in = False
        self._running = True
        self._reconnect_attempt = 0
        self._max_reconnect_attempts = 3
        self._backoff_time = 2.0

        # 조건식 실시간 구독 관리
        self.subscribed_conditions: set[str] = set()

        # HEARTBEAT 관련 상태
        self._heartbeat_interval = 10.0  # 초 단위: 10초마다 상태 출력
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._last_message_ts: float | None = None

    # ======================================================
    # 안전한 return_code 비교 메서드
    # ======================================================
    @staticmethod
    def _is_success(return_code) -> bool:
        """
        return_code가 성공인지 안전하게 확인
        키움 API는 정수 0 또는 문자열 "0" 반환 가능
        """
        return str(return_code) in ("0", "00", "000")

    # ======================================================
    # 메인 루프
    # ======================================================
    async def run(self):
        """WebSocket 메인 루프"""
        if self._heartbeat_task is None or self._heartbeat_task.done():
            loop = asyncio.get_event_loop()
            self._heartbeat_task = loop.create_task(self._heartbeat_loop())

        while self._running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                print(f"[KiwoomWs] 오류 발생: {e}")
                traceback.print_exc()
                if self._running:
                    await self._handle_reconnect()

        # run() 루프 완전히 끝날 때 HEARTBEAT 정리
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = None

    async def _connect_and_listen(self):
        """연결 및 메시지 수신"""
        try:
            print(f"[KiwoomWs] 연결 시도: {self.SOCKET_URL}")

            async with websockets.connect(
                self.SOCKET_URL,
                ping_interval=20,
                ping_timeout=10,
            ) as ws:
                self.ws = ws
                self.connected = True
                self.logged_in = False
                self._reconnect_attempt = 0
                self._backoff_time = 2.0
                self._last_message_ts = asyncio.get_event_loop().time()

                print("[KiwoomWs] 연결 성공!")

                # 연결 후 즉시 LOGIN 메시지 전송
                await self._send_login()
                print("[KiwoomWs] LOGIN 메시지 전송 완료, 서버 응답 대기...")

                print("[KiwoomWs] 메시지 수신 대기 중...")
                async for message in ws:
                    try:
                        self._last_message_ts = asyncio.get_event_loop().time()
                        await self._handle_message(message)
                    except Exception as e:
                        print(f"[KiwoomWs] 메시지 처리 오류: {e}")
                        traceback.print_exc()

        except ConnectionClosed as e:
            print(f"[KiwoomWs] 연결 종료: {e}")
            self.connected = False
            self.logged_in = False
        except Exception as e:
            print(f"[KiwoomWs] 연결 오류: {e}")
            traceback.print_exc()
            self.connected = False
            self.logged_in = False

    async def _send_login(self):
        """LOGIN 메시지 전송"""
        login_msg = {
            "trnm": "LOGIN",
            "token": self.access_token,
        }
        await self._send_message_raw(login_msg)

    async def _handle_reconnect(self):
        """지수 백오프 재연결"""
        if self._reconnect_attempt >= self._max_reconnect_attempts:
            print(
                f"[KiwoomWs] 최대 재연결 시도 횟수({self._max_reconnect_attempts}) 초과 - 60초 대기"
            )
            await asyncio.sleep(60)
            self._reconnect_attempt = 0
            self._backoff_time = 2.0
            return

        wait_time = min(self._backoff_time, 60.0)
        print(
            f"[KiwoomWs] {wait_time:.1f}초 후 재연결 시도 "
            f"({self._reconnect_attempt + 1}/{self._max_reconnect_attempts})"
        )
        await asyncio.sleep(wait_time)
        self._reconnect_attempt += 1
        self._backoff_time *= 2

    async def _restore_subscriptions(self):
        """재연결 후 기존 구독 복원"""
        # LOGIN 완료까지 대기 (최대 5초)
        for _ in range(50):
            if self.logged_in:
                break
            await asyncio.sleep(0.1)
        else:
            print("[KiwoomWs] 경고: LOGIN 응답 타임아웃, 구독 복원 중단")
            return
        
        # 추가 안전 대기 (100ms)
        await asyncio.sleep(0.1)
        
        for seq in list(self.subscribed_conditions):
            print(f"[KiwoomWs] 조건식({seq}) 재구독 중...")
            await self.subscribe_condition(seq)
            await asyncio.sleep(0.05)  # 요청 간 간격

    # ======================================================
    # HEARTBEAT 루프
    # ======================================================
    async def _heartbeat_loop(self):
        """
        일정 주기마다 WebSocket / 구독 상태를 터미널에 출력하는 HEARTBEAT
        """
        loop = asyncio.get_event_loop()
        while self._running:
            await asyncio.sleep(self._heartbeat_interval)

            now = loop.time()
            last_ts = self._last_message_ts
            if last_ts is not None:
                gap = now - last_ts
                gap_str = f"{gap:.1f}초 전"
            else:
                gap_str = "수신 기록 없음"

            print(
                "[KiwoomWs HEARTBEAT] "
                f"running={self._running}, "
                f"connected={self.connected}, "
                f"logged_in={self.logged_in}, "
                f"조건식구독={len(self.subscribed_conditions)}개, "
                f"마지막_메시지_이후={gap_str}"
            )

            if not self.connected:
                print("[KiwoomWs HEARTBEAT] ⚠️ 현재 WebSocket이 연결되어 있지 않습니다.")

    # ======================================================
    # 메시지 처리
    # ======================================================
    async def _handle_message(self, message: str):
        """
        수신 메시지 처리
        
        v3.0+: 조건식 + 주문체결 알림 처리
        - LOGIN
        - PING/PONG
        - CNSRLST (조건식 목록)
        - CNSRREQ (조건식 스냅샷)
        - CNSR (조건식 실시간)
        - CNSRCLR (조건식 해제)
        - ⭐ 주문체결 알림 (type='00')
        """
        try:
            # 원본 메시지 로깅 (PING 제외)
            if '"trnm":"PING"' not in message and '"trnm": "PING"' not in message:
                print(f"\n[KiwoomWs 원본 수신] {message[:500]}...")
            data = json.loads(message)
        except Exception as e:
            print(f"[KiwoomWs] JSON 파싱 실패: {message[:100]}")
            print(f"[KiwoomWs] 파싱 오류: {e}")
            return

        trnm = data.get("trnm")
        msg_type = data.get("type")

        # 메시지 타입 로깅 (PING 제외)
        if trnm != "PING":
            print(f"[KiwoomWs] 📥 메시지 타입: trnm={trnm}, type={msg_type}")

        # 1) LOGIN 응답
        if trnm == "LOGIN":
            return_code = data.get("return_code")
            if not self._is_success(return_code):
                print(f"[KiwoomWs] ❌ 로그인 실패: {data.get('return_msg')}")
                self.logged_in = False
                self._running = False
            else:
                print("[KiwoomWs] ✅ 로그인 성공!")
                self.logged_in = True
                # 로그인 성공 후 구독 복원
                await self._restore_subscriptions()

            # LOGIN도 콜백으로 전달
            if self.signal_callback:
                try:
                    self.signal_callback(data)
                except Exception as e:
                    print(f"[KiwoomWs] LOGIN 콜백 오류: {e}")
                    traceback.print_exc()
            return

        # 2) PING/PONG 처리
        if trnm == "PING":
            await self._send_message_raw(data)
            return

        # 3) 조건검색 관련 응답/신호
        if trnm in ("CNSRREQ", "CNSRCLR", "CNSR", "CNSRLST"):
            if trnm == "CNSR":
                print("[KiwoomWs] 📡 조건검색 실시간 신호 수신 (CNSR)")
                print(f"[KiwoomWs CNSR] {json.dumps(data, ensure_ascii=False)[:300]}")

            if self.signal_callback:
                try:
                    self.signal_callback(data)
                except Exception as e:
                    print(f"[KiwoomWs] {trnm} 콜백 오류: {e}")
                    traceback.print_exc()
            return

        # ⭐⭐⭐ 4) 주문 체결 통보 (type='00') ⭐⭐⭐
        if msg_type == "00":
            print("[KiwoomWs] 💰 주문 체결 통보 수신")
            print(f"[KiwoomWs 체결] {json.dumps(data, ensure_ascii=False)[:500]}")

            if self.signal_callback:
                try:
                    self.signal_callback(data)
                except Exception as e:
                    print(f"[KiwoomWs] 체결 통보 콜백 오류: {e}")
                    traceback.print_exc()
            return

        # 5) 기타 알 수 없는 메시지
        print(f"[KiwoomWs] ⚠️ 처리되지 않은 메시지 타입: trnm={trnm}, type={msg_type}")
        print(f"[KiwoomWs] 원본 데이터: {json.dumps(data, ensure_ascii=False)[:300]}")

        # 그래도 콜백은 전달
        if self.signal_callback:
            try:
                self.signal_callback(data)
            except Exception as e:
                print(f"[KiwoomWs] 알 수 없는 메시지 콜백 오류: {e}")
                traceback.print_exc()

    async def _send_message_raw(self, message: dict):
        """메시지 전송"""
        if not self.ws:
            print("[KiwoomWs] WebSocket 미연결")
            return

        try:
            await self.ws.send(json.dumps(message, ensure_ascii=False))
            if message.get("trnm") != "PING":
                print(f"[KiwoomWs] 전송: {message}")
        except Exception as e:
            print(f"[KiwoomWs] 메시지 전송 실패: {e}")
            traceback.print_exc()

    # ======================================================
    # 조건식 관련
    # ======================================================
    async def request_condition_list(self):
        """조건식 목록 요청 (CNSRLST)"""
        msg = {
            "trnm": "CNSRLST",
        }
        await self._send_message_raw(msg)

    async def subscribe_condition(self, seq: str):
        """조건식 실시간 구독 (CNSRREQ)"""
        seq = str(seq).strip()
        if not seq:
            print("[KiwoomWs] 잘못된 조건식 번호")
            return

        if not self.logged_in:
            print(f"[KiwoomWs] ⚠️ 로그인 전 - 조건식({seq}) 구독 보류(자동 복원 예정)")
            self.subscribed_conditions.add(seq)
            return

        print(f"[KiwoomWs] 조건식({seq}) 실시간 구독 요청 중...")

        msg = {
            "trnm": "CNSRREQ",
            "seq": seq,
            "search_type": "1",  # 0: 일반조회, 1: 조건검색+실시간
            "stex_tp": "K",      # K: KRX
        }

        await self._send_message_raw(msg)
        self.subscribed_conditions.add(seq)
        print(f"[KiwoomWs] ✅ 조건식({seq}) 실시간 구독 요청 전송 완료")

    async def unsubscribe_condition(self, seq: str):
        """조건식 실시간 구독 해제 (CNSRCLR)"""
        seq = str(seq).strip()
        if not seq:
            return

        if not self.logged_in:
            self.subscribed_conditions.discard(seq)
            return

        msg = {
            "trnm": "CNSRCLR",
            "seq": seq,
        }
        await self._send_message_raw(msg)
        self.subscribed_conditions.discard(seq)
        print(f"[KiwoomWs] 조건식({seq}) 실시간 구독 해제 요청 전송")

    # ======================================================
    # 연결 종료
    # ======================================================
    async def disconnect(self):
        print("[KiwoomWs] 연결 종료 요청")
        self._running = False
        self.connected = False
        self.logged_in = False

        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass

        self.subscribed_conditions.clear()
        print("[KiwoomWs] 연결 종료 완료")