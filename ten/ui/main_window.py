# ui/main_window.py

import sys
import traceback
import configparser
import json
from datetime import datetime

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QGridLayout, QLabel, QPushButton, QSpinBox, QRadioButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView,
    QFormLayout, QTimeEdit, QDoubleSpinBox,
    QComboBox, QMessageBox, QLineEdit
)
from PyQt5.QtCore import Qt, QTime, QTimer, QDateTime
from PyQt5.QtGui import QColor, QBrush

# TraderLogic 경로는 프로젝트 구조에 맞게 조정
try:
    from core.trader_logic import TraderLogic
except ImportError:
    try:
        from trader_logic import TraderLogic
    except ImportError:
        TraderLogic = None  # 임시 방어용 (실제 환경에서는 반드시 올바르게 import 해야 함)

DEFAULT_SELL_STRATEGY = "기본 매도 전략"
CONFIG_SELL_PREFIX = "SELL_STRATEGY:"
CONFIG_GLOBAL_SECTION = "GLOBAL_SETTINGS"
MAX_LOG_ROWS = 1000  # 로그 테이블 최대 행 수


class MainWindow(QMainWindow):
    """
    Vanilla Trading Basic - 최소 UI 버전 (자동매매 전용)

    - 상단: 타이틀 + 자동매매 ON/OFF, 전략/설정 저장, 시간 설정
    - 중단: 좌측(매수 조건), 우측(매도 조건)
    - 하단: 탭(계좌 현황, 신호 포착, 자동매매 현황)

    ⭐ v2.3 개선사항: 실시간 시세 업데이트 시 종목명 갱신 추가
    ⭐ v2.4 개선사항:
        - CONDITION_SEQ 저장/복원 (config.ini 연동)
        - 등락률 0%일 때 색상 중립 처리
        - 상태바에 날짜+시간 표시
    """

    # ========================================
    # 클래스 변수: 스타일시트 정의
    # ========================================
    BTN_STYLE_PRIMARY = """
        QPushButton {
            background-color: #2563eb;
            color: white;
            padding: 7px 16px;
            border-radius: 18px;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: #1d4ed8;
        }
        QPushButton:disabled {
            background-color: #4b5563;
            color: #9ca3af;
        }
    """

    BTN_STYLE_DANGER = """
        QPushButton {
            background-color: #dc2626;
            color: white;
            padding: 7px 16px;
            border-radius: 18px;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: #b91c1c;
        }
        QPushButton:disabled {
            background-color: #4b5563;
            color: #9ca3af;
        }
    """

    BTN_STYLE_SECONDARY = """
        QPushButton {
            background-color: #111827;
            color: #e5e7eb;
            padding: 6px 14px;
            border-radius: 16px;
            font-weight: 500;
            border: 1px solid #1f2937;
        }
        QPushButton:hover {
            background-color: #1f2937;
        }
    """

    BTN_STYLE_REJECT_OFF = """
        QPushButton {
            background-color: #111827;
            color: #f97316;
            border-radius: 12px;
            padding: 4px 8px;
            border: 1px solid #1f2937;
            font-size: 10pt;
        }
        QPushButton:hover {
            background-color: #1f2937;
        }
    """

    BTN_STYLE_REJECT_ON = """
        QPushButton {
            background-color: #7f1d1d;
            color: #fca5a5;
            border-radius: 12px;
            padding: 4px 8px;
            border: 1px solid #b91c1c;
            font-size: 10pt;
        }
        QPushButton:hover {
            background-color: #991b1b;
        }
    """

    def __init__(self):
        super().__init__()
        print("[UI] MainWindow가 생성되었습니다.")

        # TraderLogic 생성
        if TraderLogic is None:
            QMessageBox.critical(
                self,
                "오류",
                "TraderLogic 클래스를 import 할 수 없습니다.\n경로를 확인해 주세요.",
            )
            sys.exit(1)

        self.logic = TraderLogic()
        print("TraderLogic (핵심 두뇌 최소 버전) 객체가 생성되었습니다.")

        # 신호 테이블 행 캐시 (종목코드 -> 행 인덱스)
        self._signal_row_map = {}

        # 설정 로드
        self.config = configparser.ConfigParser()
        try:
            self.config.read("config.ini", encoding="utf-8")
        except Exception:
            pass

        # ✅ 글로벌 설정(매수 금액, 최대 종목 수, 시작/종료 시간, CONDITION_SEQ)
        self._load_global_settings()

        # UI 구성 + 시그널 연결
        self.initUI()
        self._connect_signals()

        # 상태바 시간 타이머
        self._init_timer()

        # TraderLogic 백그라운드 초기화
        if hasattr(self.logic, "initialize_background"):
            self.logic.initialize_background()
            print("[UI] TraderLogic.initialize_background 호출 완료")

    # ========================================
    # 헬퍼 메서드: 안전한 데이터 변환
    # ========================================
    def _safe_int(self, value, default=0):
        """안전하게 정수 변환"""
        try:
            if value is None:
                return default
            return int(str(value).replace(",", "").replace("+", "").replace("-", ""))
        except Exception:
            return default

    def _safe_float(self, value, default=0.0):
        """안전하게 실수 변환"""
        try:
            if value is None:
                return default
            return float(str(value).replace("%", "").replace("+", ""))
        except Exception:
            return default

    # ========================================
    # 글로벌 설정 저장/로드
    # ========================================
    def _load_global_settings(self):
        """
        config.ini 의 GLOBAL_SETTINGS 섹션에서
        - BUY_AMOUNT
        - MAX_STOCKS
        - START_TIME
        - END_TIME
        - CONDITION_SEQ (추가)
        를 읽어온다.
        """
        if CONFIG_GLOBAL_SECTION not in self.config:
            # 기본값
            self.buy_amount_saved = 200000
            self.max_stocks_saved = 5
            self.start_time_saved = "09:00"
            self.end_time_saved = "15:30"
            # ✅ CONDITION_SEQ 기본값 (0: 미설정)
            self.condition_seq_saved = 0
            return

        try:
            self.buy_amount_saved = self.config.getint(
                CONFIG_GLOBAL_SECTION, "BUY_AMOUNT", fallback=200000
            )
            self.max_stocks_saved = self.config.getint(
                CONFIG_GLOBAL_SECTION, "MAX_STOCKS", fallback=5
            )
            self.start_time_saved = self.config.get(
                CONFIG_GLOBAL_SECTION, "START_TIME", fallback="09:00"
            )
            self.end_time_saved = self.config.get(
                CONFIG_GLOBAL_SECTION, "END_TIME", fallback="15:30"
            )
            # ✅ CONDITION_SEQ 로드
            self.condition_seq_saved = self.config.getint(
                CONFIG_GLOBAL_SECTION, "CONDITION_SEQ", fallback=0
            )
        except Exception as e:
            print(f"[UI] 글로벌 설정 로드 실패: {e}")
            self.buy_amount_saved = 200000
            self.max_stocks_saved = 5
            self.start_time_saved = "09:00"
            self.end_time_saved = "15:30"
            self.condition_seq_saved = 0

    def _save_global_settings(self):
        """
        글로벌 설정 저장
        - BUY_AMOUNT / MAX_STOCKS / START_TIME / END_TIME
        - CONDITION_SEQ (현재 선택된 조건식 번호)
        """
        if CONFIG_GLOBAL_SECTION not in self.config:
            self.config.add_section(CONFIG_GLOBAL_SECTION)

        self.config[CONFIG_GLOBAL_SECTION]["BUY_AMOUNT"] = str(self.spin_buy_amount.value())
        self.config[CONFIG_GLOBAL_SECTION]["MAX_STOCKS"] = str(self.spin_max_stocks.value())
        self.config[CONFIG_GLOBAL_SECTION]["START_TIME"] = self.time_start.time().toString(
            "HH:mm"
        )
        self.config[CONFIG_GLOBAL_SECTION]["END_TIME"] = self.time_end.time().toString("HH:mm")

        # ✅ 현재 선택된 조건식 번호를 CONDITION_SEQ로 저장
        seq = None
        idx = self.combo_condition.currentIndex()
        if idx >= 0:
            seq = self.combo_condition.itemData(idx)

        if not seq:
            # itemData가 비어 있으면 텍스트에서 한 번 더 추출
            text = self.combo_condition.currentText().strip()
            if text.startswith("[") and "]" in text:
                seq = text.split("]")[0].lstrip("[").strip()
            elif text.isdigit():
                seq = text

        self.config[CONFIG_GLOBAL_SECTION]["CONDITION_SEQ"] = str(seq or 0)

        try:
            with open("config.ini", "w", encoding="utf-8") as f:
                self.config.write(f)
        except Exception as e:
            self._add_log_entry(
                {
                    "action": "오류",
                    "details": f"config.ini 저장 실패: {e}",
                }
            )

    # ========================================
    # UI 구성
    # ========================================
    def initUI(self):
        self.setWindowTitle("Vanilla Trading Basic (Kiwoom REST)")
        self.setMinimumSize(1100, 750)
        self.statusBar().showMessage("준비 완료.")

        # ✨ 전체 다크 테마 스타일
        self.setStyleSheet("""
            QMainWindow {
                background-color: #020617;
            }
            QWidget {
                font-family: 'Segoe UI', 'Malgun Gothic', sans-serif;
                font-size: 11pt;
                color: #e2e8f0;
            }
            QLabel {
                color: #cbd5f5;
            }
            QGroupBox {
                border: 1px solid #1f2937;
                border-radius: 8px;
                margin-top: 10px;
                background-color: #020617;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #9ca3af;
            }
            QTabWidget::pane {
                border: 1px solid #1f2937;
                border-radius: 8px;
                top: -1px;
            }
            QTabBar::tab {
                background: #020617;
                border: 1px solid #1f2937;
                border-bottom: none;
                padding: 6px 16px;
                margin-right: 2px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                color: #9ca3af;
            }
            QTabBar::tab:selected {
                background: #0f172a;
                color: #e5e7eb;
            }
            QTabBar::tab:hover {
                background: #111827;
            }
            QTableWidget {
                background-color: #020617;
                gridline-color: #1f2937;
                border: none;
                selection-background-color: #1d4ed8;
                selection-color: #e5e7eb;
                alternate-background-color: #020617;
            }
            QHeaderView::section {
                background-color: #020617;
                color: #9ca3af;
                padding: 6px 4px;
                border: 0px;
                border-bottom: 1px solid #1f2937;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox {
                background-color: #020617;
                border: 1px solid #1f2937;
                border-radius: 4px;
                padding: 4px 6px;
                color: #e5e7eb;
            }
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
            QTimeEdit:focus, QComboBox:focus {
                border: 1px solid #2563eb;
            }
            QScrollBar:vertical {
                width: 10px;
                background: #020617;
            }
            QScrollBar::handle:vertical {
                background: #4b5563;
                border-radius: 4px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical {
                height: 0;
            }
        """)

        central = QWidget()
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(16)

        # ---------------- 상단 컨트롤 바 ----------------
        self.btn_auto_on = QPushButton("자동매매 ON")
        self.btn_auto_off = QPushButton("자동매매 OFF")
        self.btn_auto_off.setEnabled(False)
        self.btn_save_all = QPushButton("전략 / 설정 저장")

        # 버튼 스타일 적용
        self.btn_auto_on.setStyleSheet(self.BTN_STYLE_PRIMARY)
        self.btn_auto_off.setStyleSheet(self.BTN_STYLE_DANGER)
        self.btn_save_all.setStyleSheet(self.BTN_STYLE_SECONDARY)

        # 자동매매 시간
        start_time_q = QTime.fromString(self.start_time_saved, "HH:mm")
        if not start_time_q.isValid():
            start_time_q = QTime(9, 0)

        end_time_q = QTime.fromString(self.end_time_saved, "HH:mm")
        if not end_time_q.isValid():
            end_time_q = QTime(15, 30)

        self.time_start = QTimeEdit(start_time_q)
        self.time_start.setDisplayFormat("HH:mm")
        self.time_end = QTimeEdit(end_time_q)
        self.time_end.setDisplayFormat("HH:mm")

        self.time_start.setMaximumWidth(70)
        self.time_end.setMaximumWidth(70)

        # ✨ 상단 타이틀 + 컨트롤 바
        top_layout = QHBoxLayout()
        top_layout.setSpacing(12)

        title_box = QVBoxLayout()
        self.lbl_title = QLabel("Vanilla Trading Basic")
        self.lbl_title.setStyleSheet("font-size: 22px; font-weight: 700; color: #e5e7eb;")
        self.lbl_subtitle = QLabel("Kiwoom REST 기반 조건식 자동매매")
        self.lbl_subtitle.setStyleSheet("font-size: 11px; color: #6b7280;")
        title_box.addWidget(self.lbl_title)
        title_box.addWidget(self.lbl_subtitle)
        title_box.setSpacing(0)

        top_layout.addLayout(title_box)
        top_layout.addStretch(1)

        top_layout.addWidget(self.btn_auto_on)
        top_layout.addWidget(self.btn_auto_off)
        top_layout.addWidget(self.btn_save_all)

        top_layout.addSpacing(12)

        lbl_time = QLabel("자동매매 시간")
        lbl_time.setStyleSheet("color: #9ca3af;")
        top_layout.addWidget(lbl_time)
        top_layout.addWidget(self.time_start)
        top_layout.addWidget(QLabel("~"))
        top_layout.addWidget(self.time_end)

        main_layout.addLayout(top_layout)
        main_layout.addSpacing(14)

        # ---------------- 중단: 매수/매도 설정 영역 ----------------
        mid_layout = QHBoxLayout()
        mid_layout.setSpacing(18)

        # ---- 좌측: 매수 조건 ----
        group_buy = QGroupBox("매수 조건")
        layout_buy = QGridLayout()
        layout_buy.setSpacing(8)
        layout_buy.setContentsMargins(14, 10, 14, 12)
        layout_buy.setColumnStretch(0, 0)
        layout_buy.setColumnStretch(1, 1)
        layout_buy.setColumnStretch(2, 0)
        layout_buy.setColumnStretch(3, 0)

        self.combo_condition = QComboBox()
        self.combo_condition.setEditable(True)

        self.spin_buy_amount = QSpinBox()
        self.spin_buy_amount.setRange(10000, 50000000)
        self.spin_buy_amount.setSingleStep(10000)
        self.spin_buy_amount.setValue(self.buy_amount_saved)

        self.spin_max_stocks = QSpinBox()
        self.spin_max_stocks.setRange(1, 50)
        self.spin_max_stocks.setValue(self.max_stocks_saved)

        self.radio_buy_market = QRadioButton("시장가 매수 (고정)")
        self.radio_buy_market.setChecked(True)
        self.radio_buy_market.setEnabled(False)

        layout_buy.addWidget(QLabel("매수 조건식"), 0, 0)
        layout_buy.addWidget(self.combo_condition, 0, 1, 1, 3)

        layout_buy.addWidget(QLabel("1주 최대 가격 (이하만 매수)"), 1, 0)
        layout_buy.addWidget(self.spin_buy_amount, 1, 1)
        layout_buy.addWidget(QLabel("원"), 1, 2)

        layout_buy.addWidget(QLabel("최대 자동매매 종목 수"), 2, 0)
        layout_buy.addWidget(self.spin_max_stocks, 2, 1)

        layout_buy.addWidget(QLabel("매수 방식"), 3, 0)
        layout_buy.addWidget(self.radio_buy_market, 3, 1, 1, 3)

        group_buy.setLayout(layout_buy)

        # ---- 우측: 매도 조건 ----
        group_sell = QGroupBox("매도 조건")
        layout_sell = QGridLayout()
        layout_sell.setSpacing(8)
        layout_sell.setContentsMargins(14, 10, 14, 12)
        layout_sell.setColumnStretch(0, 0)
        layout_sell.setColumnStretch(1, 1)
        layout_sell.setColumnStretch(2, 0)
        layout_sell.setColumnStretch(3, 0)

        self.input_strategy_name = QLineEdit(DEFAULT_SELL_STRATEGY)
        self.btn_save_strategy = QPushButton("현재 전략 이름으로 저장")
        self.btn_save_strategy.setStyleSheet(self.BTN_STYLE_SECONDARY)
        self.combo_sell_condition = QComboBox()

        layout_sell.addWidget(QLabel("전략 이름"), 0, 0)
        layout_sell.addWidget(self.input_strategy_name, 0, 1)
        layout_sell.addWidget(self.btn_save_strategy, 0, 2, 1, 2)

        layout_sell.addWidget(QLabel("저장된 전략"), 1, 0)
        layout_sell.addWidget(self.combo_sell_condition, 1, 1, 1, 3)

        self.spin_stop_loss = QDoubleSpinBox()
        self.spin_stop_loss.setRange(-50.0, 0.0)
        self.spin_stop_loss.setSingleStep(0.1)
        # ✅ TraderLogic에 값이 있으면 그걸 초기값으로 사용, 없으면 -1.50
        self.spin_stop_loss.setValue(
            float(getattr(self.logic, "stop_loss_rate", -1.50))
        )

        self.spin_profit_cut = QDoubleSpinBox()
        self.spin_profit_cut.setRange(0.0, 100.0)
        self.spin_profit_cut.setSingleStep(0.1)
        # ✅ TraderLogic에 값이 있으면 그걸 초기값으로 사용, 없으면 1.50
        self.spin_profit_cut.setValue(
            float(getattr(self.logic, "profit_cut_rate", 1.50))
        )

        lbl_sl = QLabel("평균가 대비 현재 수익률이")
        lbl_pc = QLabel("평균가 대비 현재 수익률이")

        layout_sell.addWidget(lbl_sl, 2, 0)
        layout_sell.addWidget(self.spin_stop_loss, 2, 1)
        layout_sell.addWidget(QLabel("% 이하이면 전량 매도"), 2, 2, 1, 2)

        layout_sell.addWidget(lbl_pc, 3, 0)
        layout_sell.addWidget(self.spin_profit_cut, 3, 1)
        layout_sell.addWidget(QLabel("% 이상이면 전량 매도"), 3, 2, 1, 2)

        self.radio_sell_market = QRadioButton("시장가 매도 (고정)")
        self.radio_sell_market.setChecked(True)
        self.radio_sell_market.setEnabled(False)
        layout_sell.addWidget(QLabel("매도 방식"), 4, 0)
        layout_sell.addWidget(self.radio_sell_market, 4, 1, 1, 3)

        group_sell.setLayout(layout_sell)

        mid_layout.addWidget(group_buy, 1)
        mid_layout.addWidget(group_sell, 1)
        main_layout.addLayout(mid_layout)
        main_layout.addSpacing(12)

        # ---------------- 하단: 탭 ----------------
        self.tabs = QTabWidget()
        self.tab_account = QWidget()
        self.tab_signal = QWidget()
        self.tab_log = QWidget()

        self.tabs.addTab(self.tab_account, "계좌 현황")
        self.tabs.addTab(self.tab_signal, "신호 포착")
        self.tabs.addTab(self.tab_log, "자동매매 현황")

        # --- 계좌 현황 탭 ---
        acc_layout = QVBoxLayout(self.tab_account)
        acc_layout.setContentsMargins(18, 18, 18, 18)
        acc_layout.setSpacing(16)

        title_cash = QLabel("REST 기준 매수 가능 금액")
        title_cash.setStyleSheet("font-weight: 600; color: #9ca3af; font-size: 12px;")
        title_cash.setAlignment(Qt.AlignCenter)

        card = QGroupBox()
        card.setTitle("")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)

        self.label_cash = QLabel("0 원")
        font_big = self.label_cash.font()
        font_big.setPointSize(22)
        font_big.setBold(True)
        self.label_cash.setFont(font_big)
        self.label_cash.setAlignment(Qt.AlignCenter)
        self.label_cash.setStyleSheet("color: #e5e7eb;")

        card_layout.addWidget(self.label_cash)
        acc_layout.addWidget(title_cash)
        acc_layout.addWidget(card, alignment=Qt.AlignTop)
        acc_layout.addStretch(1)

        # --- 신호 포착 탭 ---
        sig_layout = QVBoxLayout(self.tab_signal)
        sig_layout.setContentsMargins(8, 8, 8, 8)
        sig_layout.setSpacing(8)

        self.table_signal = QTableWidget()
        self.table_signal.setColumnCount(7)
        self.table_signal.setHorizontalHeaderLabels(
            ["시간", "종목명", "종목코드", "현재가", "등락률(%)", "거래량", "매수 거부"]
        )

        header_sig = self.table_signal.horizontalHeader()
        header_sig.setSectionResizeMode(QHeaderView.Stretch)

        self.table_signal.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_signal.setAlternatingRowColors(True)
        self.table_signal.verticalHeader().setVisible(False)
        self.table_signal.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)

        sig_layout.addWidget(self.table_signal)

        # --- 자동매매 현황 탭 ---
        log_layout = QVBoxLayout(self.tab_log)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.setSpacing(8)

        self.table_log = QTableWidget()
        self.table_log.setColumnCount(4)
        self.table_log.setHorizontalHeaderLabels(["시간", "종목명", "구분", "상세내역"])
        self.table_log.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table_log.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table_log.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table_log.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table_log.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table_log.setAlternatingRowColors(True)
        self.table_log.verticalHeader().setVisible(False)
        self.table_log.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)

        log_layout.addWidget(self.table_log)

        main_layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        # 매도 전략 목록 로드
        self._load_sell_strategies()

    # ========================================
    # 시그널 연결
    # ========================================
    def _connect_signals(self):
        try:
            # 버튼
            self.btn_auto_on.clicked.connect(self.on_start_trading_clicked)
            self.btn_auto_off.clicked.connect(self.on_stop_trading_clicked)
            self.btn_save_all.clicked.connect(self.on_save_all_clicked)
            self.btn_save_strategy.clicked.connect(self.on_save_strategy_clicked)

            # 콤보
            self.combo_condition.currentIndexChanged.connect(self.on_condition_changed)
            self.combo_sell_condition.currentTextChanged.connect(
                self.on_sell_condition_changed
            )

            # TraderLogic -> UI
            if hasattr(self.logic, "account_update"):
                self.logic.account_update.connect(self.update_account_status)
                print("[UI 시그널 연결] account_update ✓")

            if hasattr(self.logic, "log_update"):
                self.logic.log_update.connect(self._add_log_entry)
                print("[UI 시그널 연결] log_update ✓")

            if hasattr(self.logic, "condition_list_update"):
                self.logic.condition_list_update.connect(self.populate_condition_combo)
                print("[UI 시그널 연결] condition_list_update ✓")

            if hasattr(self.logic, "signal_detected"):
                self.logic.signal_detected.connect(self.add_signal_entry)
                print("[UI 시그널 연결] signal_detected ✓")

            if hasattr(self.logic, "signal_realtime_update"):
                self.logic.signal_realtime_update.connect(self.update_signal_row_realtime)
                print("[UI 시그널 연결] ⭐ signal_realtime_update → update_signal_row_realtime 연결 완료 ✓")
            else:
                print("[UI 시그널 연결 경고] signal_realtime_update 시그널이 TraderLogic에 없습니다!")

            print("[UI] 버튼 및 TraderLogic 시그널 연결 완료.")
        except Exception as e:
            print(f"[UI 오류] 시그널 연결 중 예외: {e}")
            traceback.print_exc()

    # ========================================
    # 타이머 (상태바 시간)
    # ========================================
    def _init_timer(self):
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_current_time)
        self.timer.start(1000)
        self.update_current_time()

    def update_current_time(self):
        """
        상태바에 현재 날짜+시간 표시
        """
        now = QDateTime.currentDateTime()
        self.statusBar().showMessage(
            f"현재 시간: {now.toString('yyyy-MM-dd hh:mm:ss')}"
        )

    # ========================================
    # 매도 전략 관리
    # ========================================
    def _load_sell_strategies(self):
        self.combo_sell_condition.clear()

        strategies = []
        for section in self.config.sections():
            if section.startswith(CONFIG_SELL_PREFIX):
                strategies.append(section[len(CONFIG_SELL_PREFIX):])

        if not strategies:
            self.save_sell_strategy(DEFAULT_SELL_STRATEGY)
            strategies.append(DEFAULT_SELL_STRATEGY)

        strategies = sorted(set(strategies))
        for name in strategies:
            self.combo_sell_condition.addItem(name)

        if strategies:
            self.combo_sell_condition.setCurrentIndex(0)
            self.on_sell_condition_changed(self.combo_sell_condition.currentText())

    def save_sell_strategy(self, strategy_name: str) -> bool:
        section_name = CONFIG_SELL_PREFIX + strategy_name
        try:
            if section_name not in self.config:
                self.config.add_section(section_name)

            self.config[section_name]["STOP_LOSS_RATE"] = str(self.spin_stop_loss.value())
            self.config[section_name]["PROFIT_CUT_RATE"] = str(self.spin_profit_cut.value())

            with open("config.ini", "w", encoding="utf-8") as f:
                self.config.write(f)

            return True
        except Exception as e:
            self._add_log_entry(
                {
                    "action": "오류",
                    "details": f"매도 전략 저장 실패: {e}",
                }
            )
            return False

    def on_sell_condition_changed(self, strategy_name: str):
        if not strategy_name:
            return

        section_name = CONFIG_SELL_PREFIX + strategy_name
        try:
            if section_name in self.config:
                sl = self.config.getfloat(section_name, "STOP_LOSS_RATE", fallback=-1.50)
                pc = self.config.getfloat(section_name, "PROFIT_CUT_RATE", fallback=1.50)
                self.spin_stop_loss.setValue(sl)
                self.spin_profit_cut.setValue(pc)

            self.input_strategy_name.setText(strategy_name)
        except Exception as e:
            self._add_log_entry(
                {
                    "action": "오류",
                    "details": f"전략 로드 실패({strategy_name}): {e}",
                }
            )

    # ========================================
    # 버튼 핸들러
    # ========================================
    def on_save_all_clicked(self):
        if self.spin_buy_amount.value() < 10000:
            QMessageBox.warning(self, "경고", "1주 최대 가격은 최소 10,000원 이상으로 설정해 주세요.")
            return

        if self.time_start.time() >= self.time_end.time():
            QMessageBox.warning(self, "경고", "시작 시간은 종료 시간보다 이전이어야 합니다.")
            return

        self._save_global_settings()
        strategy_name = self.input_strategy_name.text().strip() or DEFAULT_SELL_STRATEGY
        if self.save_sell_strategy(strategy_name):
            self._load_sell_strategies()

        QMessageBox.information(self, "저장 완료", "전략 및 글로벌 설정을 저장했습니다.")
        self._add_log_entry(
            {
                "action": "시스템",
                "details": "전략 및 글로벌 설정 저장",
            }
        )

    def on_save_strategy_clicked(self):
        strategy_name = self.input_strategy_name.text().strip()
        if not strategy_name:
            QMessageBox.warning(self, "경고", "전략 이름을 입력해 주세요.")
            return

        if self.save_sell_strategy(strategy_name):
            self._load_sell_strategies()
            QMessageBox.information(
                self, "저장 완료", f"매도 전략 '{strategy_name}'을(를) 저장했습니다."
            )

    def on_start_trading_clicked(self):
        """
        [자동매매 ON]
        - UI 설정을 TraderLogic에 전달
        - TraderLogic.start_auto_trading() 호출
        """
        try:
            # 매수 관련 설정
            self.logic.buy_amount = self.spin_buy_amount.value()
            self.logic.max_stock_limit = self.spin_max_stocks.value()

            # 시간 설정
            self.logic.start_time = self.time_start.time().toPyTime()
            self.logic.end_time = self.time_end.time().toPyTime()

            # ✅ TP/SL은 "항상 화면에서" 가져와서 적용
            sl = float(self.spin_stop_loss.value())
            tp = float(self.spin_profit_cut.value())

            # TraderLogic에 update_sell_strategy 메서드가 있다면 우선 사용
            if hasattr(self.logic, "update_sell_strategy"):
                try:
                    self.logic.update_sell_strategy(
                        profit_cut_rate=tp,
                        stop_loss_rate=sl,
                    )
                except TypeError:
                    # 시그니처가 다르거나 에러가 나면 안전하게 직접 속성 세팅
                    self.logic.stop_loss_rate = sl
                    self.logic.profit_cut_rate = tp
            else:
                # 구버전 TraderLogic 호환: 직접 속성 할당
                self.logic.stop_loss_rate = sl
                self.logic.profit_cut_rate = tp

            # 조건식 번호
            idx = self.combo_condition.currentIndex()
            seq = self.combo_condition.itemData(idx)
            if seq is not None:
                self.logic.condition_seq = str(seq)

            # 자동매매 시작
            if hasattr(self.logic, "start_auto_trading"):
                self.logic.start_auto_trading()
            elif hasattr(self.logic, "start_trading"):
                self.logic.start_trading()
            else:
                self._add_log_entry(
                    {
                        "action": "경고",
                        "details": "TraderLogic에 자동매매 시작 메서드가 없습니다.",
                    }
                )

            self.btn_auto_on.setEnabled(False)
            self.btn_auto_off.setEnabled(True)

            self._add_log_entry(
                {
                    "action": "시스템",
                    "details": f"자동매매 ON - 시작 (TP={tp:.2f}%, SL={sl:.2f}%)",
                }
            )
        except Exception as e:
            traceback.print_exc()
            self._add_log_entry(
                {
                    "action": "오류",
                    "details": f"자동매매 시작 중 오류: {e}",
                }
            )

    def on_stop_trading_clicked(self):
        """
        [자동매매 OFF]
        """
        try:
            if hasattr(self.logic, "stop_auto_trading"):
                self.logic.stop_auto_trading()
            elif hasattr(self.logic, "stop_trading"):
                self.logic.stop_trading()
            else:
                self._add_log_entry(
                    {
                        "action": "경고",
                        "details": "TraderLogic에 자동매매 중지 메서드가 없습니다.",
                    }
                )

            self.btn_auto_on.setEnabled(True)
            self.btn_auto_off.setEnabled(False)

            self._add_log_entry(
                {
                    "action": "시스템",
                    "details": "자동매매 OFF - 자동매매 중지 요청",
                }
            )
        except Exception as e:
            traceback.print_exc()
            self._add_log_entry(
                {
                    "action": "오류",
                    "details": f"자동매매 중지 중 오류: {e}",
                }
            )

    # ========================================
    # 매수 거부 버튼 핸들러
    # ========================================
    def on_reject_signal_clicked(self, stock_code: str):
        """
        신호 테이블의 [매수 거부] 버튼 클릭 시 호출.
        - TraderLogic.reject_signal(stock_code) 호출 (토글 동작)
        - 버튼 텍스트/색 + 행 배경색 토글
        """
        stock_code = (stock_code or "").strip()
        if not stock_code:
            return

        try:
            # 내부 로직에 토글 요청
            if hasattr(self.logic, "reject_signal"):
                self.logic.reject_signal(stock_code)
            elif hasattr(self.logic, "skip_stock"):
                self.logic.skip_stock(stock_code)
            else:
                self._add_log_entry(
                    {
                        "action": "경고",
                        "details": f"매수 거부 처리 메서드 없음 (code={stock_code})",
                    }
                )
                return

            # UI 토글 반영
            self.update_reject_button_ui(stock_code)

            # 로그
            rejected_set = getattr(self.logic, "rejected_codes", set())
            status = "ON" if stock_code in rejected_set else "OFF"
            self._add_log_entry(
                {
                    "action": "사용자",
                    "stock_name": stock_code,
                    "details": f"종목({stock_code}) 매수 거부 {status}",
                }
            )

        except Exception as e:
            traceback.print_exc()
            self._add_log_entry(
                {
                    "action": "오류",
                    "stock_name": stock_code,
                    "details": f"매수 거부 처리 중 오류: {str(e)[:100]}",
                }
            )

    def update_reject_button_ui(self, stock_code: str):
        """
        TraderLogic.rejected_codes 상태를 기준으로
        - 버튼 텍스트/색상 토글
        - 해당 행 배경색 토글
        """
        table = self.table_signal
        row_count = table.rowCount()
        code = stock_code.strip()
        if code.startswith("A"):
            code = code[1:]

        # 현재 거부 상태: TraderLogic.rejected_codes 참고
        rejected_set = getattr(self.logic, "rejected_codes", set())
        is_rejected = code in rejected_set

        for row in range(row_count):
            item_code = table.item(row, 2)
            if not item_code:
                continue

            existing = item_code.text().strip()
            if existing.startswith("A"):
                existing = existing[1:]

            if existing != code:
                continue

            btn = table.cellWidget(row, 6)
            if not isinstance(btn, QPushButton):
                continue

            if is_rejected:
                # 매수 거부 ON 상태
                btn.setText("거부 해제")
                btn.setStyleSheet(self.BTN_STYLE_REJECT_ON)
                for col in range(7):
                    item = table.item(row, col)
                    if item:
                        item.setBackground(QBrush(QColor("#1e293b")))
            else:
                # 매수 거부 OFF 상태
                btn.setText("매수 거부")
                btn.setStyleSheet(self.BTN_STYLE_REJECT_OFF)
                for col in range(7):
                    item = table.item(row, col)
                    if item:
                        item.setBackground(QBrush(QColor("#020617")))

    # ========================================
    # 조건식 관련
    # ========================================
    def on_condition_changed(self, index: int):
        if index < 0:
            return
        seq = self.combo_condition.itemData(index)
        if not seq:
            text = self.combo_condition.currentText().strip()
            if text.startswith("[") and "]" in text:
                seq = text.split("]")[0].lstrip("[").strip()
            elif text.isdigit():
                seq = text

        if not seq:
            return

        seq_str = str(seq)
        self.logic.condition_seq = seq_str
        print(f"[UI] 조건식 변경 → {seq_str}")
        self._add_log_entry(
            {
                "action": "시스템",
                "details": f"조건식 {seq_str}번으로 변경",
            }
        )

        # 신호 테이블 초기화
        self.table_signal.setRowCount(0)
        self._signal_row_map.clear()
        print(f"[UI 캐시] 신호 테이블 초기화 완료 (조건식 변경)")

        if hasattr(self.logic, "change_condition"):
            try:
                self.logic.change_condition(seq_str)
            except Exception as e:
                traceback.print_exc()
                self._add_log_entry(
                    {
                        "action": "오류",
                        "details": f"TraderLogic.change_condition 호출 중 오류: {e}",
                    }
                )

    def populate_condition_combo(self, data: dict):
        if "output1" in data:
            condition_list = data.get("output1", [])
        elif "data" in data:
            condition_list = data.get("data", [])
        else:
            condition_list = []

        if not condition_list:
            self._add_log_entry(
                {
                    "action": "경고",
                    "details": "수신된 조건식 목록이 비어 있습니다.",
                }
            )
            return

        self.combo_condition.clear()

        try:
            for item in condition_list:
                if isinstance(item, list) and len(item) >= 2:
                    seq = item[0]
                    name = item[1].strip()
                elif isinstance(item, dict):
                    name = (
                        item.get("name")
                        or item.get("cnd_nm")
                        or item.get("cond_nm")
                    )
                    seq = (
                        item.get("seq")
                        or item.get("cnd_sq")
                        or item.get("cond_indx")
                    )
                else:
                    continue

                if name and seq:
                    self.combo_condition.addItem(f"[{seq}] {name}", str(seq))

            if self.combo_condition.count() > 0:
                # ✅ 저장된 CONDITION_SEQ를 우선적으로 선택
                target_index = -1
                saved_seq_str = str(getattr(self, "condition_seq_saved", 0))
                if saved_seq_str != "0":
                    for i in range(self.combo_condition.count()):
                        if self.combo_condition.itemData(i) == saved_seq_str:
                            target_index = i
                            break

                if target_index == -1:
                    target_index = 0  # 없으면 첫 번째

                self.combo_condition.setCurrentIndex(target_index)
                first_seq = self.combo_condition.currentData()
                if first_seq:
                    self.logic.condition_seq = str(first_seq)
                    print(f"[UI] 조건식 자동선택: {self.combo_condition.currentText()} (seq={first_seq})")

                self._add_log_entry(
                    {
                        "action": "시스템",
                        "details": f"조건식 목록 로드 완료 ({self.combo_condition.count()}개)",
                    }
                )
            else:
                self._add_log_entry(
                    {
                        "action": "경고",
                        "details": "조건식 목록 파싱 결과 유효한 항목이 없습니다.",
                    }
                )
        except Exception as e:
            traceback.print_exc()
            self._add_log_entry(
                {
                    "action": "오류",
                    "details": f"조건식 목록 파싱 실패: {e}",
                }
            )

    # ========================================
    # 계좌 / 로그 / 신호 UI
    # ========================================
    def _add_log_entry(self, log_dict: dict):
        """
        로그 테이블에 항목 추가
        - 최대 MAX_LOG_ROWS 개로 제한
        """
        if not hasattr(self, "table_log"):
            return
        try:
            if self.table_log.rowCount() >= MAX_LOG_ROWS:
                self.table_log.removeRow(0)

            row = self.table_log.rowCount()
            self.table_log.insertRow(row)

            time_str = log_dict.get("time") or datetime.now().strftime("%H:%M:%S")
            stock_name = (
                log_dict.get("stock_name")
                or log_dict.get("name")
                or log_dict.get("code")
                or "-"
            )
            action = log_dict.get("action") or log_dict.get("tag") or "-"
            details = (
                log_dict.get("details")
                or log_dict.get("msg")
                or json.dumps(log_dict, ensure_ascii=False)
            )

            item_time = QTableWidgetItem(time_str)
            item_stock = QTableWidgetItem(str(stock_name))
            item_action = QTableWidgetItem(str(action))
            item_details = QTableWidgetItem(str(details))

            item_time.setTextAlignment(Qt.AlignCenter)
            item_stock.setTextAlignment(Qt.AlignCenter)
            item_action.setTextAlignment(Qt.AlignCenter)

            self.table_log.setItem(row, 0, item_time)
            self.table_log.setItem(row, 1, item_stock)
            self.table_log.setItem(row, 2, item_action)
            self.table_log.setItem(row, 3, item_details)

            self.table_log.scrollToBottom()

        except Exception as e:
            print(f"[UI 로그 오류] _add_log_entry 실패: {e}")
            traceback.print_exc()

    def update_account_status(self, data: dict):
        """
        계좌 현황 업데이트 (매수 가능 금액)
        """
        try:
            cash = self._safe_int(
                data.get("can_order_amt")
                or data.get("ord_psbl_cash_amt")
                or data.get("cash")
                or data.get("deposit")
                or data.get("현금")
                or data.get("예수금")
            )

            self.label_cash.setText(f"{cash:,.0f} 원")
        except Exception as e:
            print(f"[UI 오류] update_account_status 중 예외: {e}")
            traceback.print_exc()
            self._add_log_entry(
                {
                    "action": "오류",
                    "details": f"계좌 현황 갱신 중 오류: {str(e)[:100]}",
                }
            )

    def add_signal_entry(self, data: dict):
        """
        신호 포착 테이블 업데이트 (개선된 B안 + 매수 거부 버그 수정 + 디버깅 로그)
        - 종목코드 기준으로 '한 행만 유지'
        - 처음 들어온 종목코드는 새 행 추가 + 매수 거부 버튼 생성
        - 이후 동일 종목 신호는 같은 행에 덮어쓰기
        - ✅ 캐싱을 통한 O(1) 행 검색
        """
        stock_code_raw = ""
        try:
            print(f"\n[UI 신호 추가] 데이터 수신: {json.dumps(data, ensure_ascii=False)[:200]}")

            table = self.table_signal

            time_str = str(data.get("time") or datetime.now().strftime("%H:%M:%S"))
            stock_name = str(
                data.get("stock_name") or data.get("stk_nm") or data.get("name") or ""
            )
            stock_code_raw = str(
                data.get("stock_code") or data.get("stk_cd") or data.get("code") or ""
            ).strip()

            if not stock_code_raw:
                print("[UI 신호 추가 스킵] 종목코드 없음")
                return

            stock_code = stock_code_raw[1:] if stock_code_raw.startswith("A") else stock_code_raw
            print(f"[UI 신호 추가] 종목코드={stock_code}, 종목명={stock_name}")

            price = (
                data.get("current_price")
                or data.get("price")
                or data.get("cur_price")
                or data.get("prc")
                or 0
            )
            change_rate = (
                data.get("change_rate")
                or data.get("chg_rate")
                or data.get("prc_rto")
                or 0.0
            )
            volume = data.get("volume") or data.get("vol") or 0

            price_val = self._safe_int(price)
            rate_val = self._safe_float(change_rate)
            vol_int = self._safe_int(volume)

            target_row = self._signal_row_map.get(stock_code, -1)
            print(f"[UI 캐시 조회] {stock_code} → 행 인덱스={target_row}")

            # 새 행 추가
            if target_row == -1:
                target_row = table.rowCount()
                table.insertRow(target_row)
                self._signal_row_map[stock_code] = target_row
                print(f"[UI 캐시 등록] {stock_code} → 새 행={target_row} 추가 및 캐시 등록 완료")

                item_name = QTableWidgetItem(stock_name)
                item_code_item = QTableWidgetItem(stock_code)
                item_name.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                item_code_item.setTextAlignment(Qt.AlignCenter)
                table.setItem(target_row, 1, item_name)
                table.setItem(target_row, 2, item_code_item)

                btn_reject = QPushButton("매수 거부")
                btn_reject.clicked.connect(
                    lambda _, code=stock_code: self.on_reject_signal_clicked(code)
                )

                rejected_set = getattr(self.logic, "rejected_codes", set())
                if stock_code in rejected_set:
                    btn_reject.setText("거부 해제")
                    btn_reject.setStyleSheet(self.BTN_STYLE_REJECT_ON)
                    print(f"[UI 매수 거부] {stock_code} 초기 상태=ON")
                else:
                    btn_reject.setStyleSheet(self.BTN_STYLE_REJECT_OFF)
                    print(f"[UI 매수 거부] {stock_code} 초기 상태=OFF")

                table.setCellWidget(target_row, 6, btn_reject)
            else:
                print(f"[UI 신호 업데이트] {stock_code} 기존 행={target_row} 덮어쓰기")

            # 공통 컬럼 덮어쓰기
            item_time = QTableWidgetItem(time_str)
            item_time.setTextAlignment(Qt.AlignCenter)
            table.setItem(target_row, 0, item_time)

            if stock_name:
                item_name = QTableWidgetItem(stock_name)
                item_name.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                table.setItem(target_row, 1, item_name)

            item_code_item = QTableWidgetItem(stock_code)
            item_code_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(target_row, 2, item_code_item)

            item_price = QTableWidgetItem(f"{price_val:,.0f}")
            item_price.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(target_row, 3, item_price)

            # ✅ 등락률 색상 0%일 때 중립색 처리
            rate_item = QTableWidgetItem(f"{rate_val:.2f}")
            rate_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if rate_val > 0:
                rate_item.setForeground(QBrush(QColor("#ef4444")))  # 빨강
            elif rate_val < 0:
                rate_item.setForeground(QBrush(QColor("#3b82f6")))  # 파랑
            else:
                rate_item.setForeground(QBrush(QColor("#e5e7eb")))  # 중립
            table.setItem(target_row, 4, rate_item)

            vol_item = QTableWidgetItem(f"{vol_int:,}")
            vol_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(target_row, 5, vol_item)

            print(f"[UI 신호 추가 완료] {stock_name}({stock_code}) 행={target_row} 업데이트 성공")
        except Exception as e:
            error_msg = f"신호 포착 행 추가 실패 (종목: {stock_code_raw})"
            print(f"[UI 오류] {error_msg}: {e}")
            traceback.print_exc()
            self._add_log_entry(
                {
                    "action": "오류",
                    "stock_name": stock_code_raw,
                    "details": f"{error_msg}: {str(e)[:100]}",
                }
            )

    def update_signal_row_realtime(self, data: dict):
        """
        실시간 시세 기반 신호 포착 테이블 업데이트 (디버깅 로그 강화 + 종목명 업데이트)
        - ✅ 캐싱을 통한 O(1) 행 검색
        - ✅ 종목명 업데이트 추가
        - ✅ 등락률 색상 0%일 때 중립색 처리
        """
        stock_code_raw = ""
        try:
            print(f"\n[UI 실시간 갱신 호출됨] 데이터: {json.dumps(data, ensure_ascii=False)[:200]}")

            stock_code_raw = str(
                data.get("stock_code") or data.get("stk_cd") or data.get("code") or ""
            ).strip()
            if not stock_code_raw:
                print("[UI 실시간 갱신 스킵] 종목코드 없음")
                return

            stock_code = stock_code_raw[1:] if stock_code_raw.startswith("A") else stock_code_raw
            time_str = str(data.get("time") or datetime.now().strftime("%H:%M:%S"))

            print(f"[UI 실시간 갱신] 종목코드={stock_code}, 시간={time_str}")

            # ⭐ 종목명 파싱 추가
            stock_name = str(
                data.get("stock_name")
                or data.get("stk_nm")
                or data.get("name")
                or stock_code  # 종목명 없으면 코드 표시
            ).strip()

            print(f"[UI 실시간 갱신] 종목명={stock_name}")

            price = (
                data.get("current_price")
                or data.get("price")
                or data.get("cur_price")
                or 0
            )
            change_rate = (
                data.get("change_rate")
                or data.get("chg_rate")
                or data.get("prc_rto")
                or 0.0
            )
            volume = data.get("volume") or data.get("vol") or 0

            price_val = self._safe_int(price)
            rate_val = self._safe_float(change_rate)
            vol_int = self._safe_int(volume)

            print(f"[UI 실시간 갱신] 파싱 완료: 가격={price_val:,}, 등락률={rate_val:.2f}%, 거래량={vol_int:,}")

            target_row = self._signal_row_map.get(stock_code, -1)
            print(f"[UI 캐시 조회] {stock_code} → 행 인덱스={target_row}")

            if target_row == -1:
                print(f"[UI 실시간 갱신 스킵] {stock_code}는 신호 테이블에 없음 (캐시 미스)")
                print(f"[UI 캐시 상태] 현재 캐시: {list(self._signal_row_map.keys())[:10]}...")
                return

            table = self.table_signal
            print(f"[UI 실시간 갱신] {stock_code} 테이블 행={target_row} 업데이트 시작")

            # 시간 업데이트
            item_time = QTableWidgetItem(time_str)
            item_time.setTextAlignment(Qt.AlignCenter)
            table.setItem(target_row, 0, item_time)

            # ⭐⭐⭐ 종목명 업데이트 추가! ⭐⭐⭐
            if stock_name and stock_name != stock_code:
                item_name = QTableWidgetItem(stock_name)
                item_name.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                table.setItem(target_row, 1, item_name)
                print(f"[UI 실시간 갱신] 종목명 업데이트: {stock_name}")

            # 종목코드
            item_code_item = QTableWidgetItem(stock_code)
            item_code_item.setTextAlignment(Qt.AlignCenter)
            table.setItem(target_row, 2, item_code_item)

            # 현재가
            item_price = QTableWidgetItem(f"{price_val:,.0f}")
            item_price.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(target_row, 3, item_price)

            # ✅ 등락률 (0%일 때 중립색)
            rate_item = QTableWidgetItem(f"{rate_val:.2f}")
            rate_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if rate_val > 0:
                rate_item.setForeground(QBrush(QColor("#ef4444")))
            elif rate_val < 0:
                rate_item.setForeground(QBrush(QColor("#3b82f6")))
            else:
                rate_item.setForeground(QBrush(QColor("#e5e7eb")))
            table.setItem(target_row, 4, rate_item)

            # 거래량
            vol_item = QTableWidgetItem(f"{vol_int:,}")
            vol_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            table.setItem(target_row, 5, vol_item)

            print(f"[UI 실시간 갱신 성공] {stock_name}({stock_code}) 행={target_row} 테이블 업데이트 완료 ✓")
        except Exception as e:
            error_msg = f"신호 실시간 갱신 실패 (종목: {stock_code_raw})"
            print(f"[UI 오류] {error_msg}: {e}")
            traceback.print_exc()
            self._add_log_entry(
                {
                    "action": "오류",
                    "stock_name": stock_code_raw,
                    "details": f"{error_msg}: {str(e)[:100]}",
                }
            )
