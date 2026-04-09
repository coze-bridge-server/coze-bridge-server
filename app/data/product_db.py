"""
상품 데이터베이스 + 텍스트 매칭 엔진

Coze 봇 응답 텍스트에서 상품 모델명/상품명/카테고리 키워드를 탐지하여
매칭되는 상품의 카드 데이터를 자동 생성

설계 원칙:
- products.json 파일 하나만 교체하면 상품 데이터가 바뀜 (코드 수정 불필요)
- 서버 시작 시 1회 로드 후 캐싱 (핫 리로드 지원)
- 매칭 우선순위: 모델명 정확 매치 > 상품명 포함 > 키워드 포함 > 카테고리 매치
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from app.config.logging import logger
from app.config.settings import get_settings


@dataclass
class Product:
    """개별 상품 데이터"""
    model: str = ""
    product_name: str = ""
    category: str = ""
    color: str = ""
    lineup: str = ""
    features: str = ""
    image_url: str = ""
    button_url: str = ""
    price: Optional[int] = None
    discount_price: Optional[int] = None
    keywords: list[str] = field(default_factory=list)

    def to_card_dict(self) -> dict:
        """카드 모듈(kakao_card / navertalk_card)에 넘길 dict 변환"""
        return {
            "product_name": self.product_name,
            "image_url": self.image_url,
            "button_url": self.button_url,
            "price": self.price,
            "discount_price": self.discount_price,
        }


class ProductDB:
    """
    상품 데이터베이스 + 텍스트 매칭 엔진

    사용 흐름:
    1. 서버 시작 시 products.json 로드
    2. Coze 텍스트 응답 수신
    3. match_from_text()로 텍스트 분석 → 매칭 상품 리스트 반환
    4. 핸들러가 카드 모듈로 전달하여 캐러셀 생성
    """

    def __init__(self, json_path: str = "products.json"):
        self._json_path = json_path
        self._products: list[Product] = []
        self._load()

    def _load(self) -> None:
        """products.json에서 상품 데이터 로드"""
        path = Path(self._json_path)
        if not path.exists():
            logger.warning(f"상품 DB 파일 없음: {self._json_path}")
            return

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            items = raw.get("products", [])

            self._products = []
            for item in items:
                if not isinstance(item, dict):
                    continue

                product = Product(
                    model=item.get("model", ""),
                    product_name=item.get("product_name", ""),
                    category=item.get("category", ""),
                    color=item.get("color", ""),
                    lineup=item.get("lineup", ""),
                    features=item.get("features", ""),
                    image_url=item.get("image_url", ""),
                    button_url=item.get("button_url", ""),
                    price=item.get("price"),
                    discount_price=item.get("discount_price"),
                    keywords=item.get("keywords", []),
                )
                self._products.append(product)

            logger.info(f"상품 DB 로드 완료: {len(self._products)}개 상품")

        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"상품 DB 로드 실패: {e}")

    def reload(self) -> int:
        """상품 DB 핫 리로드"""
        self._products.clear()
        self._load()
        return len(self._products)

    def get_all(self) -> list[Product]:
        """전체 상품 리스트 반환"""
        return list(self._products)

    def find_by_model(self, model: str) -> Optional[Product]:
        """모델명으로 정확 검색"""
        model_upper = model.upper()
        for p in self._products:
            if p.model.upper() == model_upper:
                return p
        return None

    def find_by_category(self, category: str) -> list[Product]:
        """카테고리로 검색"""
        return [p for p in self._products if p.category == category]

    # =========================================================================
    # 핵심: 텍스트 매칭 엔진
    # =========================================================================

    def match_from_text(self, text: str, max_results: int = 3) -> list[Product]:
        """
        Coze 봇 응답 텍스트에서 상품을 자동 매칭

        매칭 전략 (점수 기반):
        1. 모델명 정확 매치 (텍스트에 모델명이 포함) → +100점
        2. 상품명 포함 (텍스트에 상품명의 핵심 부분 포함) → +50점
        3. 키워드 매치 (keywords 배열의 단어가 텍스트에 포함) → 키워드당 +10점
        4. 카테고리 매치 (텍스트에 "정수기"/"냉장고" 등 포함) → +5점

        Args:
            text: Coze 봇 응답 텍스트
            max_results: 최대 반환 상품 수 (기본 3개)

        Returns:
            점수 높은 순으로 정렬된 상품 리스트
        """
        if not text or not self._products:
            return []

        text_upper = text.upper()
        # 정규화: 공백/특수문자 통일
        text_normalized = re.sub(r'\s+', ' ', text_upper)

        scored: list[tuple[int, Product]] = []

        for product in self._products:
            score = 0

            # 1. 모델명 정확 매치 (+100)
            if product.model and product.model.upper() in text_upper:
                score += 100

            # 2. 상품명 핵심 부분 매치 (+50)
            #    "LG 퓨리케어 오브제컬렉션 얼음정수기(블랙)" 에서
            #    "퓨리케어", "얼음정수기", "블랙" 등 핵심 단어 추출
            name_keywords = self._extract_name_keywords(product.product_name)
            name_match_count = sum(
                1 for kw in name_keywords if kw.upper() in text_upper)
            if name_match_count >= 2:
                score += 50
            elif name_match_count == 1:
                score += 20

            # 3. keywords 배열 매치 (키워드당 +10)
            for kw in product.keywords:
                if kw.upper() in text_upper:
                    score += 10

            # 4. 카테고리 매치 (+5)
            if product.category and product.category in text:
                score += 5

            if score > 0:
                scored.append((score, product))

        # 점수 높은 순 정렬
        scored.sort(key=lambda x: x[0], reverse=True)

        # 상위 N개 반환
        results = [product for _, product in scored[:max_results]]

        if results:
            logger.info(
                f"상품 매칭 완료: {len(results)}개 "
                f"[{', '.join(p.model for p in results)}] "
                f"(점수: {[s for s, _ in scored[:max_results]]})"
            )

        return results

    @staticmethod
    def _extract_name_keywords(product_name: str) -> list[str]:
        """
        상품명에서 매칭용 핵심 키워드 추출

        "LG 퓨리케어 오브제컬렉션 얼음정수기(블랙)"
        → ["퓨리케어", "오브제컬렉션", "얼음정수기", "블랙"]

        "LG 디오스 AI 오브제컬렉션 STEM 얼음정수 냉장고(...)"
        → ["디오스", "오브제컬렉션", "STEM", "얼음정수", "냉장고"]
        """
        # 괄호 안 내용 별도 추출
        paren_match = re.findall(r'[（(]([^)）]+)[)）]', product_name)
        paren_words = []
        for m in paren_match:
            paren_words.extend(re.split(r'[,、/\s]+', m))

        # 괄호 제거 후 분리
        name_clean = re.sub(r'[（(][^)）]*[)）]', '', product_name)
        words = re.split(r'[\s/,]+', name_clean)

        # "LG" 같은 일반 브랜드명 제외
        skip_words = {"LG", "lg", "AI", "ai"}
        keywords = []
        for w in words + paren_words:
            w = w.strip()
            if len(w) >= 2 and w not in skip_words:
                keywords.append(w)

        return keywords


# === 싱글턴 ===
_db: Optional[ProductDB] = None


def get_product_db() -> ProductDB:
    """ProductDB 싱글턴 팩토리"""
    global _db
    if _db is None:
        settings = get_settings()
        # products.json 경로 — 환경변수로 변경 가능하도록 확장 가능
        _db = ProductDB("products.json")
    return _db
