"""카드 빌드 검증: thumbnail.link.web이 정상 생성되는지 단위 테스트"""
import json
from app.cards.kakao_card import build_kakao_card_output

# =========================================================================
# 테스트 1: BasicCard — button_url 있을 때 thumbnail.link 생성
# =========================================================================
card_with_url = [{
    "product_name": "정수기 A모델",
    "description": "월 29,900원~",
    "image_url": "https://example.com/img.jpg",
    "button_url": "https://example.com/product/1",
}]

result = build_kakao_card_output(card_with_url)
card = result[0]["basicCard"]
assert "link" in card["thumbnail"], "FAIL: thumbnail.link 없음"
assert card["thumbnail"]["link"]["web"] == "https://example.com/product/1"
print("✅ Test 1 PASS: BasicCard thumbnail.link.web 정상")
print(json.dumps(card["thumbnail"], indent=2, ensure_ascii=False))

# =========================================================================
# 테스트 2: BasicCard — button_url 없을 때 link 미생성
# =========================================================================
card_no_url = [{
    "product_name": "정수기 B모델",
    "image_url": "https://example.com/img2.jpg"
}]

result2 = build_kakao_card_output(card_no_url)
card2 = result2[0]["basicCard"]
assert "link" not in card2["thumbnail"], "FAIL: button_url 없는데 link 생김"
print("✅ Test 2 PASS: button_url 없으면 link 미생성")

# =========================================================================
# 테스트 3: Carousel — 카드 2~3개일 때 캐러셀로 묶이는지 확인
# =========================================================================
cards_multi = [
    {
        "product_name": "정수기 A",
        "image_url": "https://example.com/img1.jpg",
        "button_url": "https://example.com/product/1",
        "price": 29900,
    },
    {
        "product_name": "공기청정기 B",
        "image_url": "https://example.com/img2.jpg",
        "button_url": "https://example.com/product/2",
        "price": 39900,
        "discount_price": 35900,
    },
    {
        "product_name": "건조기 C",
        "image_url": "https://example.com/img3.jpg",
        "button_url": "https://example.com/product/3",
    },
]

result3 = build_kakao_card_output(cards_multi)
assert "carousel" in result3[0], "FAIL: 복수 카드인데 carousel 아님"
carousel = result3[0]["carousel"]
assert carousel["type"] == "basicCard"
assert len(carousel["items"]) == 3
for i, item in enumerate(carousel["items"]):
    assert "link" in item["thumbnail"], f"FAIL: carousel item[{i}] thumbnail.link 없음"
    assert item["thumbnail"]["link"]["web"] == cards_multi[i]["button_url"]
print("✅ Test 3 PASS: Carousel 3개 카드 + 모든 thumbnail.link.web 정상")

# =========================================================================
# 테스트 4: 가격 포맷팅 — 할인가 + 정가
# =========================================================================
card_with_prices = [{
    "product_name": "공기청정기",
    "price": 39900,
    "discount_price": 35900,
    "image_url": "https://example.com/img3.jpg",
    "button_url": "https://example.com/product/3",
}]
result4 = build_kakao_card_output(card_with_prices)
card4 = result4[0]["basicCard"]
assert "description" in card4, "FAIL: 가격 description 없음"
assert "35,900" in card4["description"], f"FAIL: 할인가 미표시: {card4['description']}"
print(f"✅ Test 4 PASS: 가격 description 정상 — {card4['description']}")

# =========================================================================
# 테스트 5: 가격 없는 카드 — description 미표시
# =========================================================================
card_no_price = [{
    "product_name": "건조기",
    "image_url": "https://example.com/img4.jpg",
    "button_url": "https://example.com/product/4",
}]
result5 = build_kakao_card_output(card_no_price)
card5 = result5[0]["basicCard"]
assert "description" not in card5, f"FAIL: 가격 없는데 description 있음: {card5.get('description')}"
print("✅ Test 5 PASS: 가격 없으면 description 미표시")

# =========================================================================
# 테스트 6: nan / 빈값 가격 처리
# =========================================================================
card_nan_price = [{
    "product_name": "냉장고",
    "price": "nan",
    "discount_price": "",
    "image_url": "https://example.com/img5.jpg",
    "button_url": "https://example.com/product/5",
}]
result6 = build_kakao_card_output(card_nan_price)
card6 = result6[0]["basicCard"]
assert "description" not in card6, f"FAIL: nan 가격인데 description 있음: {card6.get('description')}"
print("✅ Test 6 PASS: nan/빈값 가격 -> description 미표시")

print("\n🎉 ALL TESTS PASSED")
