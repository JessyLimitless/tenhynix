"""
Vanilla Trading Basic - Core Module
키움증권 API 연동 및 트레이딩 로직
────────────────────────────────────────

⭐ 주요 버전 업데이트
v1.1.0
  - WebSocket HEARTBEAT(상태 모니터링) 기능 추가
  - 터미널에서 WS 연결 유지 여부 실시간 확인 가능
  - TraderLogic 내부에 status timer 추가

v1.0.0
  - 초기 버전
  - REST 로그인
  - 조건식 실시간 구독
  - TP/SL 자동 매도 (REAL 기반)
  - 예수금 조회 및 계좌 관리
"""

# 현재 모듈 버전
__version__ = "1.1.0"


# 모듈 공개 범위 (필요한 것만 외부에 공개)
__all__ = [
    "TraderLogic",
]
