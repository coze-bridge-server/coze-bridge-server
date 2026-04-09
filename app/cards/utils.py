"""
카드 공통 유틸리티 모듈
- kakao_card / navertalk_card 에서 공유하는 헬퍼 함수
- 가격 파싱, 가격 description 포맷팅 등
"""
import math
from typing import Optional

from app.config.settings import get_settings


# =========================================================================
# 가격 안전 추출 헬퍼
# =========================================================================

def safe_price(value) -> Optional[int]:
    """
    가격 값을 안전하게 정수로 변환

    다양한 입력 형태 처리:
    - None -> None
    - nan (float) -> None
    - "" (빈 문자열) -> None
    - "nan" (문자열) -> None
    - 29900 (int) -> 29900
    - 29900.0 (float) -> 29900
    - "29900" -> 29900
    - "29,900" -> 29900
    - "29,900원" -> 29900

    Args:
        value: 가격 원본 값

    Returns:
        정수 가격 또는 None (유효하지 않은 경우)
    """
    if value is None:
        return None

    # float nan 체크
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return int(value)

    # int 그대로
    if isinstance(value, int):
        return value

    # 문자열 처리
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        # "nan" 문자열 체크
        if stripped.lower() in ("nan", "none", "null", "n/a", "-"):
            return None
        # 숫자만 추출
        digits = "".join(c for c in stripped if c.isdigit())
        if digits:
            try:
                return int(digits)
            except ValueError:
                return None
        return None

    return None


# =========================================================================
# 가격 정보 → description 텍스트 생성
# =========================================================================

def build_price_description(card: dict) -> str:
    """
    카드의 price / discount_price를 description 텍스트로 변환

    환경변수 포맷을 사용하여 고객이 문구 변경 가능:
    - CARD_PRICE_FORMAT: 정가 + 할인가 모두 있을 때
    - CARD_PRICE_ONLY_FORMAT: 정가만 있을 때
    - CARD_DISCOUNT_ONLY_FORMAT: 할인가만 있을 때

    nan / 빈값 / None -> 미표시

    Args:
        card: 카드 데이터 dict

    Returns:
        가격 설명 문자열 (가격 없으면 빈 문자열)
    """
    settings = get_settings()

    price = safe_price(card.get("price"))
    discount_price = safe_price(card.get("discount_price"))

    # 둘 다 없으면 빈 문자열
    if price is None and discount_price is None:
        return ""

    # 둘 다 있으면 할인가 + 정가 포맷
    if price is not None and discount_price is not None:
        price_str = f"{price:,}원"
        discount_str = f"{discount_price:,}원"
        try:
            return settings.CARD_PRICE_FORMAT.format(
                price=price_str,
                discount_price=discount_str,
            )
        except (KeyError, IndexError):
            return f"할인가: {discount_str} (정가: {price_str})"

    # 정가만 있을 때
    if price is not None:
        price_str = f"{price:,}원"
        try:
            return settings.CARD_PRICE_ONLY_FORMAT.format(price=price_str)
        except (KeyError, IndexError):
            return f"월 {price_str}"

    # 할인가만 있을 때
    if discount_price is not None:
        discount_str = f"{discount_price:,}원"
        try:
            return settings.CARD_DISCOUNT_ONLY_FORMAT.format(
                discount_price=discount_str,
            )
        except (KeyError, IndexError):
            return f"월 {discount_str}"

    return ""
