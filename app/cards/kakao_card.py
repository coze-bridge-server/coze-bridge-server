"""
카카오 오픈빌더 카드형 말풍선 모듈

요구사항:
- thumbnail.imageUrl = image_url
- thumbnail.link.web = button_url (이미지 클릭 시 URL 이동 필수)
- title = product_name
- description = 가격 정보 (price / discount_price 있으면 할인가 표시)
- buttons: "상품 보러가기" -> button_url 연결
- fixedRatio: false
- 상품 2~3개 -> carousel로 묶어서 응답
- image_url 누락 -> 이미지 없는 텍스트 카드로 fallback
- price/discount_price가 nan이나 빈값 -> 가격 영역 미표시
- Coze 응답에 상품 데이터 없음 -> 일반 텍스트 응답

카카오 BasicCard 구조:
{
    "title": "상품명",
    "description": "가격 정보",
    "thumbnail": {
        "imageUrl": "https://...",
        "fixedRatio": false,
        "link": {"web": "https://..."}
    },
    "buttons": [{"action": "webLink", "label": "상품 보러가기", "webLinkUrl": "..."}]
}

카카오 스펙 참고:
- https://kakaobusiness.gitbook.io/main/tool/chatbot/skill_guide/answer_json_format

제한사항 (카카오 공식):
- BasicCard: title + description 합계 최대 400자
- Carousel: 최대 10장
- 버튼: 최대 3개 / label 최대 14자
- thumbnail imageUrl: 필수 (BasicCard에서 thumbnail 사용 시)
"""
from typing import Optional

from app.config.logging import logger
from app.config.settings import get_settings
from app.cards.utils import safe_price, build_price_description


# =========================================================================
# 메인 진입점 — KakaoHandler.format_response()에서 호출
# =========================================================================

def build_kakao_card_output(cards: list[dict]) -> list[dict]:
    """
    Coze 카드 데이터 리스트를 카카오 SkillResponse outputs 배열로 변환

    변환 규칙:
    - 카드 0개 -> 빈 리스트 (호출자가 텍스트 폴백 처리)
    - 카드 1개 -> [{"basicCard": {...}}]
    - 카드 2~10개 -> [{"carousel": {"type": "basicCard", "items": [...]}}]
    - 카드 10개 초과 -> 앞 10개만 캐러셀 처리 (카카오 제한)

    Args:
        cards: Coze에서 파싱된 카드 데이터 리스트
               각 카드: {product_name, image_url, button_url, price, discount_price, ...}

    Returns:
        카카오 SkillResponse의 outputs 배열에 넣을 수 있는 dict 리스트
    """
    if not cards:
        return []

    # 카카오 캐러셀 최대 10장 제한
    if len(cards) > 10:
        logger.warning(f"카카오 카드 10장 초과 -> 앞 10개만 사용 (전체 {len(cards)}개)")
        cards = cards[:10]

    # BasicCard로 빌드
    built_cards = [_build_basic_card(card) for card in cards]

    # 유효한 카드만 필터링 (빌드 실패한 None 제거)
    built_cards = [c for c in built_cards if c is not None]

    if not built_cards:
        return []

    # 단일 카드 -> 개별 출력
    if len(built_cards) == 1:
        return [{"basicCard": built_cards[0]}]

    # 복수 카드 -> 캐러셀
    return [
        {
            "carousel": {
                "type": "basicCard",
                "items": built_cards,
            }
        }
    ]


# =========================================================================
# BasicCard 빌드 — 개별 카드
# =========================================================================

def _build_basic_card(card: dict) -> Optional[dict]:
    """
    단일 Coze 카드 데이터를 카카오 BasicCard로 변환

    요구사항 매핑:
    - title = product_name
    - description = 가격 정보 (price/discount_price 포맷팅)
    - thumbnail.imageUrl = image_url
    - thumbnail.link.web = button_url (이미지 클릭 시 URL 이동)
    - thumbnail.fixedRatio = false (환경변수로 변경 가능)
    - buttons = "상품 보러가기" webLink 버튼

    Args:
        card: 단일 카드 데이터 dict

    Returns:
        BasicCard 내부 dict 또는 None (유효하지 않은 경우)
    """
    settings = get_settings()

    # --- 제목 추출: product_name -> title -> "상품 정보" (폴백) ---
    title = card.get("product_name") or card.get("title") or "상품 정보"

    # --- 설명(가격 정보) 구성 ---
    description = build_price_description(card)

    # 제목 + 설명 합산 400자 제한 (카카오 공식)
    if len(title) + len(description) > 400:
        max_desc = 400 - len(title) - 3
        if max_desc > 0:
            description = description[:max_desc] + "..."
        else:
            description = ""

    result = {}

    # 제목 설정
    if title:
        result["title"] = title

    # 설명 설정 (가격 정보가 있을 때만)
    if description:
        result["description"] = description

    # --- 썸네일 (이미지) ---
    image_url = card.get("image_url", "")
    button_url = card.get("button_url", "")

    # image_url 누락 시 기본 이미지 URL 폴백
    if not image_url and settings.CARD_DEFAULT_IMAGE_URL:
        image_url = settings.CARD_DEFAULT_IMAGE_URL

    if image_url:
        thumbnail = {
            "imageUrl": image_url,
            "fixedRatio": settings.CARD_KAKAO_FIXED_RATIO,
        }
        # 이미지 클릭 시 URL 이동 (요구사항 필수)
        if button_url:
            thumbnail["link"] = {"web": button_url}
        result["thumbnail"] = thumbnail

    # --- 버튼 ---
    if button_url:
        label = settings.get_kakao_button_label()
        # 카카오 버튼 label 최대 14자
        if len(label) > 14:
            label = label[:14]

        result["buttons"] = [
            {
                "action": "webLink",
                "label": label,
                "webLinkUrl": button_url,
            }
        ]

    # 최소한 제목이라도 있어야 유효한 카드
    if not result.get("title"):
        logger.warning(f"BasicCard 빌드 실패 — 제목 없음: {card}")
        return None

    return result
