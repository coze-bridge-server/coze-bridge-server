"""
프로젝트 전역 설정 모듈
- 환경변수를 Pydantic Settings로 읽어서 타입 안전하게 관리
- Railway 배포 시 환경변수 자동 주입됨
- 멀티 고객사 환경에서는 이 설정이 폴백(기본값) 역할
- 실제 고객사별 설정은 clients.json -> ClientConfigManager에서 관리
"""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """전역 설정 클래스 — .env 또는 시스템 환경변수에서 값을 로드"""

    # --- 서버 설정 ---
    PORT: int = 8000                              # 서버 포트 (Railway는 자동 주입)
    ENV: str = "production"                       # 환경 구분: development / production
    LOG_LEVEL: str = "info"                       # 로그 레벨

    # --- 기본 Coze API 설정 (clients.json 없을 때 폴백) ---
    COZE_BOT_ID: str = ""                         # 기본 Coze 봇 ID
    COZE_PAT: str = ""                            # 기본 Coze PAT
    COZE_API_BASE: str = "https://api.coze.com"   # Coze API 기본 URL
    # Coze 응답 대기 타임아웃 (초) — 카카오/톡톡 5초 SLA 내 안전마진
    COZE_TIMEOUT: float = 3.5

    # --- 네이버톡톡 설정 (clients.json 없을 때 폴백) ---
    NAVER_TALK_PARTNER_ID: str = ""               # 네이버톡톡 파트너 ID
    NAVER_TALK_TOKEN: str = ""                    # 네이버톡톡 인증 토큰

    # --- 멀티 고객사 설정 ---
    CLIENT_CONFIG_JSON: str = "clients.json"      # 고객사 설정 파일 경로

    # --- 관리자 설정 ---
    ADMIN_SECRET: str = ""                        # /admin 엔드포인트 인증키 (선택)

    # =====================================================================
    # 분할 전송 안내 메시지 — .env로 분리하여 코드 수정 없이 변경 가능
    # =====================================================================

    # 카카오 콜백 대기 안내 메시지 (useCallback의 data.text에 표시)
    GUIDE_MESSAGE_KAKAO: str = "답변을 준비하고 있어요 잠시만 기다려주세요!"
    # 네이버톡톡 안내 메시지 (보내기 API로 선발송)
    GUIDE_MESSAGE_NAVER: str = "답변을 준비하고 있어요 잠시만 기다려주세요!"
    # Coze API 실패 시 에러 안내 메시지 (양 채널 공용)
    ERROR_MESSAGE: str = "죄송합니다 일시적인 오류가 발생했습니다 잠시 후 다시 시도해주세요"
    # Coze API 타임아웃 (콜백 1분) 초과 시 메시지
    TIMEOUT_MESSAGE: str = "죄송합니다 응답 생성에 시간이 너무 오래 걸렸습니다 다시 질문해주세요"

    # =====================================================================
    # 카드형 응답 설정 — .env로 분리하여 코드 수정 없이 커스터마이징 가능
    # =====================================================================

    # 버튼 텍스트 (고객이 문구 변경 시 .env만 수정)
    CARD_BUTTON_LABEL: str = "상품 보러가기"         # 카카오 + 네이버톡톡 공용 버튼 텍스트
    # 카카오 전용 (비어있으면 CARD_BUTTON_LABEL 사용)
    CARD_BUTTON_LABEL_KAKAO: str = ""
    # 네이버 전용 (비어있으면 CARD_BUTTON_LABEL 사용)
    CARD_BUTTON_LABEL_NAVER: str = ""

    # 기본 이미지 URL (image_url 누락 시 사용할 폴백 이미지)
    CARD_DEFAULT_IMAGE_URL: str = ""               # 비어있으면 이미지 없는 텍스트 카드로 fallback

    # 가격 표시 형식
    # 할인가 + 정가 모두 있을 때
    CARD_PRICE_FORMAT: str = "할인가: {discount_price} (정가: {price})"
    # 정가만 있을 때
    CARD_PRICE_ONLY_FORMAT: str = "월 {price}"
    # 할인가만 있을 때
    CARD_DISCOUNT_ONLY_FORMAT: str = "월 {discount_price}"

    # =====================================================================
    # Auto-suggestion 글자수 제한 — Coze 추천 질문 truncate 설정
    # =====================================================================

    # 카카오 quickReplies label 최대 글자수 (초과 시 "..." 붙여서 자르기)
    SUGGESTION_MAX_LENGTH_KAKAO: int = 14
    # 네이버톡톡 buttonList title 최대 글자수 (초과 시 "..." 붙여서 자르기)
    SUGGESTION_MAX_LENGTH_TALK: int = 18

    # =====================================================================
    # 카카오 웰컴카드 설정 — 첫 메시지 시 자동 표시
    # =====================================================================

    # 웰컴카드 이미지 URL (EC2 내부 Caddy가 서빙하는 static 이미지 경로)
    WELCOME_IMAGE_URL: str = "https://imhyun-bot.duckdns.org/images/welcome.jpg"
    # 웰컴카드 인사말 텍스트
    WELCOME_MESSAGE: str = "반갑습니다. 가전 전문가의 노하우를 담은 LG전자 구독 AI 매니저입니다. AI가 실시간으로 최적의 가전 구독 설계를 도와드립니다."
    # 웰컴카드 버튼1: 맞춤 가전 추천받기 (텍스트 발화)
    WELCOME_BTN1_LABEL: str = "맞춤 가전 추천받기"
    WELCOME_BTN1_VALUE: str = "맞춤 가전 추천받기"
    # 웰컴카드 버튼2: LG전자 구독 혜택 확인 (URL 이동)
    WELCOME_BTN2_LABEL: str = "LG전자 구독 혜택 확인"
    WELCOME_BTN2_URL: str = "https://www.lge.co.kr/subscribe"
    # 웰컴카드 버튼3: 상담사 연결 (상담톡 전환)
    WELCOME_BTN3_LABEL: str = "상담사 연결(직접 문의)"
    # 세션 만료 시간 (초) — 이 시간 이후 재진입 시 다시 웰컴카드 표시
    WELCOME_SESSION_TIMEOUT: int = 1800
    # 웰컴카드 모드: A = 웰컴카드만 반환(AI답변은 다음 메시지부터) / B = 웰컴카드 즉시 + AI답변 콜백
    WELCOME_MODE: str = "B"

    # 카카오 BasicCard 캐러셀 고정비율 설정
    CARD_KAKAO_FIXED_RATIO: bool = False           # 카카오 thumbnail fixedRatio

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
    }

    # --- 카드 설정 헬퍼 메서드 ---

    def get_kakao_button_label(self) -> str:
        """카카오 전용 버튼 텍스트 반환 — 전용 설정이 없으면 공용 사용"""
        return self.CARD_BUTTON_LABEL_KAKAO or self.CARD_BUTTON_LABEL

    def get_naver_button_label(self) -> str:
        """네이버 전용 버튼 텍스트 반환 — 전용 설정이 없으면 공용 사용"""
        return self.CARD_BUTTON_LABEL_NAVER or self.CARD_BUTTON_LABEL


@lru_cache()
def get_settings() -> Settings:
    """설정 싱글턴 — 앱 전체에서 동일 인스턴스 재사용"""
    return Settings()
