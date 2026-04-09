"""
카드형 응답 모듈 패키지

채널별 카드 빌더:
- kakao_card: 카카오 오픈빌더 BasicCard / Carousel
- navertalk_card: 네이버톡톡 compositeContent
"""
from app.cards.kakao_card import build_kakao_card_output
from app.cards.navertalk_card import build_navertalk_card_response

__all__ = [
    "build_kakao_card_output",
    "build_navertalk_card_response",
]
