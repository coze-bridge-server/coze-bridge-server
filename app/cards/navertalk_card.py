"""
네이버톡톡 카드형(CompositeContent) 모듈

요구사항:
- compositeList 배열로 캐러셀 구성
- image.imageUrl = image_url (800x400 원본 사용)
- title = product_name
- description = 가격 정보
- buttonList: LINK 타입 버튼 "상품 보러가기" -> url/mobileUrl = button_url
- 이미지 클릭 링크 불가 (톡톡 제약) -> 버튼으로 유도
- image_url 누락 -> 이미지 없는 텍스트 카드로 fallback
- price/discount_price가 nan이나 빈값 -> 가격 영역 미표시

네이버톡톡 compositeContent 구조:
{
    "compositeList": [
        {
            "title": "상품명",
            "description": "가격 정보",
            "image": {"imageUrl": "https://..."},
            "buttonList": [
                {
                    "type": "LINK",
                    "data": {
                        "title": "상품 보러가기",
                        "url": "https://...",
                        "mobileUrl": "https://..."
                    }
                }
            ]
        }
    ]
}

네이버톡톡 compositeContent 스펙 참고:
- https://github.com/navertalk/chatbot-api#compositecontent

제한사항 (네이버톡톡 공식):
- compositeList: 최대 10개 Composite
- title: 최대 200자
- description: 최대 1000자
- image: JPG/JPEG/PNG/GIF / 530x290px 권장
- buttonList: 최대 10개 / title 최대 18자
"""
from typing import Optional

from app.config.logging import logger
from app.config.settings import get_settings
from app.cards.utils import safe_price, build_price_description


# =========================================================================
# 메인 진입점 — NaverTalkHandler.format_response()에서 호출
# =========================================================================

def build_navertalk_card_response(cards: list[dict]) -> Optional[dict]:
    """
    Coze 카드 데이터 리스트를 네이버톡톡 compositeContent 응답으로 변환

    변환 규칙:
    - 카드 0개 -> None (호출자가 텍스트 폴백 처리)
    - 카드 1~10개 -> compositeContent 응답
    - 카드 10개 초과 -> 앞 10개만 사용

    Args:
        cards: Coze에서 파싱된 카드 데이터 리스트
               각 카드: {product_name, image_url, button_url, price, discount_price, ...}

    Returns:
        네이버톡톡 응답 dict 또는 None
        {
            "event": "send",
            "compositeContent": {
                "compositeList": [...]
            }
        }
    """
    if not cards:
        return None

    # 네이버톡톡 compositeList 최대 10개 제한
    if len(cards) > 10:
        logger.warning(
            f"네이버톡톡 카드 10개 초과 -> 앞 10개만 사용 (전체 {len(cards)}개)"
        )
        cards = cards[:10]

    # 각 카드를 Composite 객체로 변환
    composite_list = []
    for card in cards:
        composite = _build_composite(card)
        if composite:
            composite_list.append(composite)

    if not composite_list:
        logger.warning("네이버톡톡 유효한 Composite 카드 없음 -> None 반환")
        return None

    return {
        "event": "send",
        "compositeContent": {
            "compositeList": composite_list,
        },
    }


# =========================================================================
# Composite 빌드 — 개별 카드
# =========================================================================

def _build_composite(card: dict) -> Optional[dict]:
    """
    단일 Coze 카드 데이터를 네이버톡톡 Composite 객체로 변환

    요구사항 매핑:
    - title = product_name
    - description = 가격 정보 (price / discount_price)
    - image.imageUrl = image_url
    - buttonList = LINK 버튼 "상품 보러가기"
    - 이미지 클릭 링크 불가 (톡톡 제약) → 버튼으로 유도

    Args:
        card: 단일 카드 데이터 dict

    Returns:
        Composite dict 또는 None
    """
    settings = get_settings()
    result = {}

    # --- 제목: product_name -> title ---
    title = card.get("product_name") or card.get("title") or ""

    if title:
        if len(title) > 200:
            title = title[:197] + "..."
        result["title"] = title

    # --- 설명: 가격 정보 ---
    description = build_price_description(card)

    if description:
        if len(description) > 1000:
            description = description[:997] + "..."
        result["description"] = description

    # --- 이미지 ---
    image_url = card.get("image_url", "")

    # image_url 누락 시 기본 이미지 폴백
    if not image_url and settings.CARD_DEFAULT_IMAGE_URL:
        image_url = settings.CARD_DEFAULT_IMAGE_URL

    if image_url:
        result["image"] = {"imageUrl": image_url}

    # --- 버튼 (LINK 타입) ---
    button_url = card.get("button_url", "")
    if button_url:
        label = settings.get_naver_button_label()
        if len(label) > 18:
            label = label[:18]

        result["buttonList"] = [
            {
                "type": "LINK",
                "data": {
                    "title": label,
                    "url": button_url,
                    "mobileUrl": button_url,
                },
            }
        ]

    # --- 유효성 검증: title 또는 description 중 하나 이상 필요 ---
    if not result.get("title") and not result.get("description"):
        if result.get("image"):
            result["title"] = "상품 정보"
        else:
            logger.warning(f"Composite 빌드 실패 — 제목/설명/이미지 모두 없음: {card}")
            return None

    return result
