"""
상품 데이터 모듈 패키지
"""
from app.data.product_db import get_product_db, ProductDB

__all__ = [
    "get_product_db",
    "ProductDB",
]
