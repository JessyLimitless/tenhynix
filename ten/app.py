# app.py
import sys
import traceback
import configparser
from pathlib import Path

from PyQt5 import QtCore
from PyQt5.QtWidgets import QApplication, QMessageBox

# ----------------------------------------------------
# 프로젝트 경로 설정
# ----------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent

# 프로젝트 루트 경로를 sys.path에 추가
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

# ----------------------------------------------------
# MainWindow import (ui/main_window.py)
# ----------------------------------------------------
try:
    # 기본: 패키지 형태 (ui/__init__.py 존재)
    from ui.main_window import MainWindow
    print("[초기화 ✅] ui.main_window.MainWindow import 성공")
except Exception as e1:
    print(f"[초기화 ⚠️] ui.main_window import 실패, 단일 모듈 fallback 시도: {e1}")
    traceback.print_exc()

    try:
        # fallback: ui 폴더를 직접 path에 추가하고 main_window 모듈로 import
        UI_DIR = ROOT_DIR / "ui"
        if UI_DIR.is_dir() and (str(UI_DIR) not in sys.path):
            sys.path.append(str(UI_DIR))

        from main_window import MainWindow  # ui/main_window.py를 직접 모듈로 취급
        print("[초기화 ✅] fallback 경로에서 MainWindow import 성공")
    except Exception as e2:
        print(f"[초기화 ❌] MainWindow import 최종 실패: {e2}")
        traceback.print_exc()
        QMessageBox.critical(
            None,
            "초기화 오류",
            f"MainWindow를 불러올 수 없습니다.\n\n{e2}\n\n"
            "ui/main_window.py 파일이 존재하는지 확인하세요."
        )
        sys.exit(1)


# ----------------------------------------------------
# config.ini 검증
# ----------------------------------------------------
def create_default_config():
    """
    기본 config.ini 파일 생성
    """
    config = configparser.ConfigParser()
    
    # GLOBAL_SETTINGS 섹션
    config['GLOBAL_SETTINGS'] = {
        'CONDITION_SEQ': '0',
        'BUY_AMOUNT': '200000',
        'MAX_STOCKS': '5',
        'START_TIME': '09:00',
        'END_TIME': '15:30',
    }
    
    # 기본 매도 전략
    config['SELL_STRATEGY:기본 매도 전략'] = {
        'STOP_LOSS_RATE': '-1.50',
        'PROFIT_CUT_RATE': '1.50',
    }
    
    try:
        config_path = ROOT_DIR / "config.ini"
        with open(config_path, 'w', encoding='utf-8') as f:
            config.write(f)
        print(f"[설정 ✅] config.ini 파일 생성 완료: {config_path}")
        return True
    except Exception as e:
        print(f"[설정 ❌] config.ini 생성 실패: {e}")
        return False


def validate_config():
    """
    config.ini 파일 검증 및 필수 설정 확인
    - CONDITION_SEQ가 0인지 경고
    - 필수 섹션 확인
    - 파일이 없으면 자동 생성
    """
    config_path = ROOT_DIR / "config.ini"
    
    if not config_path.exists():
        print("[설정 ⚠️] config.ini 파일이 없습니다.")
        
        response = QMessageBox.question(
            None,
            "설정 파일 생성",
            "config.ini 파일이 없습니다.\n\n"
            "기본 설정으로 새로 생성하시겠습니까?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )
        
        if response == QMessageBox.Yes:
            if create_default_config():
                QMessageBox.information(
                    None,
                    "설정 파일 생성 완료",
                    "config.ini 파일이 생성되었습니다.\n\n"
                    "⚠️ 조건식 번호를 반드시 설정하세요!\n"
                    "현재: CONDITION_SEQ = 0 (작동하지 않음)\n\n"
                    "키움 HTS에서 조건식 번호를 확인하여\n"
                    "config.ini 파일을 수정하세요."
                )
                # 생성 후 검증 계속 진행
            else:
                return False
        else:
            print("[설정] 사용자가 생성을 취소했습니다.")
            return False
    
    try:
        config = configparser.ConfigParser()
        config.read(config_path, encoding="utf-8")
        
        # GLOBAL_SETTINGS 섹션 확인
        if "GLOBAL_SETTINGS" in config:
            condition_seq = config.get("GLOBAL_SETTINGS", "CONDITION_SEQ", fallback=None)
            
            if condition_seq == "0":
                print("\n" + "=" * 60)
                print("⚠️  경고: 조건식 번호가 '0'으로 설정되어 있습니다!")
                print("=" * 60)
                print("조건식 번호 '0'은 키움 서버가 거부할 가능성이 높습니다.")
                print()
                print("📋 해결 방법:")
                print("1. 키움 HTS → 조건검색 → 조건식 관리")
                print("2. 사용할 조건식의 번호 확인 (예: 1, 2, 3...)")
                print("3. config.ini 파일 수정:")
                print("   [GLOBAL_SETTINGS]")
                print("   CONDITION_SEQ = 1  # ← 올바른 번호로 변경")
                print("=" * 60)
                print()
                
                # 사용자에게 계속 진행할지 물어봄
                response = QMessageBox.warning(
                    None,
                    "조건식 번호 확인 필요",
                    "조건식 번호가 '0'으로 설정되어 있습니다.\n\n"
                    "이 상태로 실행하면 조건식 신호를 받지 못할 수 있습니다.\n\n"
                    "계속 진행하시겠습니까?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No
                )
                
                if response == QMessageBox.No:
                    print("[초기화] 사용자가 취소했습니다.")
                    return False
            
            else:
                print(f"[설정 ✅] 조건식 번호: {condition_seq}")
        
        # 기타 설정 로그
        if "GLOBAL_SETTINGS" in config:
            buy_amount = config.get("GLOBAL_SETTINGS", "BUY_AMOUNT", fallback="N/A")
            max_stocks = config.get("GLOBAL_SETTINGS", "MAX_STOCKS", fallback="N/A")
            print(f"[설정 ℹ️] 매수 금액: {buy_amount}원")
            print(f"[설정 ℹ️] 최대 종목 수: {max_stocks}개")
        
        return True
        
    except Exception as e:
        print(f"[설정 ⚠️] config.ini 검증 중 오류: {e}")
        traceback.print_exc()
        return True  # 오류가 있어도 계속 진행


# ----------------------------------------------------
# 전역 예외 처리 훅
# ----------------------------------------------------
def excepthook(exc_type, exc_value, exc_tb):
    """
    전역 예외 처리 훅.
    - 콘솔에 트레이스백 출력
    - 팝업으로 간단한 오류 메시지 표시
    """
    err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print("\n" + "=" * 60)
    print("[!! 전역 예외 발생 !!]")
    print("=" * 60)
    print(err_msg)
    print("=" * 60)

    try:
        QMessageBox.critical(
            None,
            "치명적 오류",
            f"예상치 못한 오류가 발생했습니다.\n\n{exc_value}\n\n"
            f"자세한 내용은 콘솔 로그를 확인해 주세요."
        )
    except Exception:
        # QApplication 초기화 이전에 난 예외 등
        pass

    # 기본 동작(프로세스 종료)은 유지
    sys.__excepthook__(exc_type, exc_value, exc_tb)


# ----------------------------------------------------
# 의존성 패키지 검증
# ----------------------------------------------------
def validate_python_version():
    """
    Python 버전 검증
    - Python 3.8 이상 필요
    """
    print("\n[Python 버전 검증]")
    print("-" * 40)
    
    version_info = sys.version_info
    version_str = f"{version_info.major}.{version_info.minor}.{version_info.micro}"
    
    print(f"현재 Python 버전: {version_str}")
    
    if version_info < (3, 8):
        print(f"❌ Python 3.8 이상 필요 (현재: {version_str})")
        QMessageBox.critical(
            None,
            "Python 버전 오류",
            f"Python 3.8 이상이 필요합니다.\n"
            f"현재 버전: {version_str}\n\n"
            "Python을 업그레이드하세요."
        )
        return False
    
    print(f"✅ Python 버전 확인 완료")
    print("-" * 40)
    return True


def validate_dependencies():
    """
    필수 패키지 검증
    - PyQt5
    - requests
    - websockets
    """
    print("\n[의존성 패키지 검증]")
    print("-" * 40)
    
    missing_packages = []
    
    # PyQt5
    try:
        import PyQt5
        from PyQt5.QtCore import PYQT_VERSION_STR
        print(f"✅ PyQt5 {PYQT_VERSION_STR}")
    except ImportError:
        print("❌ PyQt5 없음")
        missing_packages.append("PyQt5")
    except Exception as e:
        print(f"⚠️  PyQt5 버전 확인 실패 (계속 진행): {e}")
    
    # requests
    try:
        import requests
        print(f"✅ requests {requests.__version__}")
    except ImportError:
        print("❌ requests 없음")
        missing_packages.append("requests")
    
    # websockets
    try:
        import websockets
        print(f"✅ websockets {websockets.__version__}")
    except ImportError:
        print("❌ websockets 없음")
        missing_packages.append("websockets")
    
    print("-" * 40)
    
    if missing_packages:
        pkg_list = ", ".join(missing_packages)
        print(f"\n⚠️  누락된 패키지: {pkg_list}")
        QMessageBox.warning(
            None,
            "의존성 오류",
            f"다음 패키지가 설치되지 않았습니다:\n{pkg_list}\n\n"
            f"설치 명령어:\n"
            f"pip install {' '.join(missing_packages)}"
        )
        return False
    
    print("[의존성 검증 완료]\n")
    return True


# ----------------------------------------------------
# 환경 검증
# ----------------------------------------------------
def validate_environment():
    """
    실행 환경 검증
    - 필수 디렉토리 확인
    - 필수 파일 확인
    - 핵심 모듈 import 테스트
    """
    print("\n[환경 검증 시작]")
    print("-" * 40)
    
    # 필수 디렉토리
    required_dirs = ["core", "ui"]
    for dir_name in required_dirs:
        dir_path = ROOT_DIR / dir_name
        if dir_path.is_dir():
            print(f"✅ {dir_name}/ 디렉토리 존재")
        else:
            print(f"❌ {dir_name}/ 디렉토리 없음!")
            QMessageBox.critical(
                None,
                "환경 오류",
                f"{dir_name}/ 디렉토리를 찾을 수 없습니다.\n\n"
                "프로젝트 구조를 확인하세요."
            )
            return False
    
    # 필수 파일
    required_files = {
        "core/trader_logic.py": True,   # 필수
        "core/kiwoom_api.py": True,     # 필수
        "core/kiwoom_ws.py": True,      # 필수
        "ui/main_window.py": True,      # 필수
        "config.ini": False,            # 선택 (자동 생성)
    }
    
    for file_path, is_required in required_files.items():
        full_path = ROOT_DIR / file_path
        if full_path.is_file():
            print(f"✅ {file_path} 존재")
        else:
            if is_required:
                print(f"❌ {file_path} 없음 (필수 파일!)")
                QMessageBox.critical(
                    None,
                    "환경 오류",
                    f"필수 파일을 찾을 수 없습니다:\n{file_path}\n\n"
                    "프로젝트 구조를 확인하세요."
                )
                return False
            else:
                print(f"⚠️  {file_path} 없음 (자동 생성됨)")
    
    print("-" * 40)
    
    # ✅ 핵심 모듈 import 테스트
    print("\n[핵심 모듈 검증]")
    print("-" * 40)
    
    try:
        from core.kiwoom_api import KiwoomApi
        print("✅ KiwoomApi import 성공")
    except Exception as e:
        print(f"❌ KiwoomApi import 실패: {e}")
        QMessageBox.critical(
            None,
            "모듈 오류",
            f"KiwoomApi 모듈을 불러올 수 없습니다.\n\n{e}\n\n"
            "core/kiwoom_api.py를 확인하세요."
        )
        return False
    
    try:
        from core.kiwoom_ws import KiwoomWs
        print("✅ KiwoomWs import 성공")
    except Exception as e:
        print(f"❌ KiwoomWs import 실패: {e}")
        QMessageBox.critical(
            None,
            "모듈 오류",
            f"KiwoomWs 모듈을 불러올 수 없습니다.\n\n{e}\n\n"
            "core/kiwoom_ws.py를 확인하세요."
        )
        return False
    
    try:
        from core.trader_logic import TraderLogic
        print("✅ TraderLogic import 성공")
        
        # ✅ 필수 메서드 검증
        required_methods = [
            'initialize_background',
            'start_auto_trading',
            'stop_auto_trading',
            'reject_signal',
        ]
        
        missing_methods = []
        for method in required_methods:
            if not hasattr(TraderLogic, method):
                missing_methods.append(method)
        
        if missing_methods:
            print(f"⚠️  TraderLogic 누락 메서드: {', '.join(missing_methods)}")
            print("   (프로그램은 계속 실행되지만 일부 기능이 작동하지 않을 수 있습니다)")
        else:
            print("   └─ 필수 메서드 확인 완료")
            
    except Exception as e:
        print(f"❌ TraderLogic import 실패: {e}")
        QMessageBox.critical(
            None,
            "모듈 오류",
            f"TraderLogic 모듈을 불러올 수 없습니다.\n\n{e}\n\n"
            "core/trader_logic.py를 확인하세요."
        )
        return False
    
    print("-" * 40)
    print("[환경 검증 완료]\n")
    return True


# ----------------------------------------------------
# 메인 진입점
# ----------------------------------------------------
def main():
    """
    애플리케이션 메인 진입점
    """
    print("\n" + "=" * 60)
    print("  Vanilla Trading Basic - Kiwoom REST")
    print("  버전: 1.0")
    print("=" * 60)
    print()
    
    # 0. Python 버전 검증
    if not validate_python_version():
        print("[초기화 ❌] Python 버전 검증 실패")
        input("\n아무 키나 눌러 종료...")
        sys.exit(1)
    
    # 1. 의존성 패키지 검증
    if not validate_dependencies():
        print("[초기화 ❌] 의존성 검증 실패")
        input("\n아무 키나 눌러 종료...")
        sys.exit(1)
    
    # 2. 환경 검증
    if not validate_environment():
        print("[초기화 ❌] 환경 검증 실패")
        sys.exit(1)
    
    # 3. config.ini 검증
    if not validate_config():
        print("[초기화 ❌] 설정 검증 실패 또는 사용자 취소")
        sys.exit(0)
    
    # 4. 전역 예외 훅 등록
    sys.excepthook = excepthook
    print("[초기화 ✅] 전역 예외 처리 훅 등록 완료")

    # 5. 고해상도 DPI 설정 (Windows HiDPI 대비)
    try:
        QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
        QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)
        print("[초기화 ✅] HiDPI 설정 완료")
    except Exception as e:
        # Qt 버전에 따라 없을 수 있음 – 무시
        print(f"[초기화 ℹ️] HiDPI 설정 스킵: {e}")

    # 6. QApplication 생성
    print("[초기화] QApplication 생성 중...")
    app = QApplication(sys.argv)
    app.setApplicationName("Vanilla Trading Basic")
    app.setOrganizationName("MuseAI")
    app.setOrganizationDomain("muse.ai")
    print("[초기화 ✅] QApplication 생성 완료")

    # 7. MainWindow 생성
    print("\n[초기화] MainWindow 생성 중...")
    print("-" * 40)
    try:
        window = MainWindow()
        print("-" * 40)
        print("[초기화 ✅] MainWindow 생성 완료")
    except Exception as e:
        print("-" * 40)
        print(f"[초기화 ❌] MainWindow 생성 중 예외: {e}")
        traceback.print_exc()
        QMessageBox.critical(
            None,
            "초기화 오류",
            f"메인 윈도우 생성 중 오류가 발생했습니다.\n\n{e}\n\n"
            "자세한 내용은 콘솔 로그를 확인하세요."
        )
        sys.exit(1)

    # 8. 메인 윈도우 표시
    window.show()
    print("\n[초기화 ✅] UI 표시 완료")
    print("[초기화] 이벤트 루프 진입...")
    print("=" * 60)
    print()
    
    # 9. Qt 이벤트 루프 시작
    sys.exit(app.exec_())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n[종료] 사용자가 프로그램을 종료했습니다.")
        sys.exit(0)
    except Exception as e:
        print(f"\n\n[치명적 오류] 예상치 못한 오류: {e}")
        traceback.print_exc()
        sys.exit(1)