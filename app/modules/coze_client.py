"""
Coze API v3 공용 클라이언트 모듈
- 모든 채널 핸들러가 공유하는 Coze API 호출 로직
- Non-streaming 방식: POST /v3/chat (stream=false) -> 폴링 -> 메시지 조회
- 5초 타임아웃 내에 응답 완료 여부 판단
- 타임아웃 초과 시 백그라운드 태스크로 폴링 계속
- 텍스트 응답에서 상품 키워드 자동 매칭 → 카드 데이터 생성
"""
import asyncio
import time
import json
import re
from typing import Optional
import httpx

from app.config.logging import logger
from app.config.settings import get_settings


# --- Coze API 응답 상태 상수 ---
class ChatStatus:
    """Coze Chat API 상태값 상수"""
    CREATED = "created"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    REQUIRES_ACTION = "requires_action"


class CozeClient:
    """
    Coze v3 Chat API 비동기 클라이언트

    사용 흐름:
    1. chat() 호출 -> 대화 생성 + 폴링 + 메시지 조회 (5초 타임아웃 적용)
    2. 타임아웃 초과 시 -> chat_id/conversation_id 반환
    3. 응답 파싱 -> type="answer" 메시지에서 텍스트/카드 데이터 추출
    4. 카드 데이터 없으면 -> 텍스트에서 상품 키워드 자동 매칭
    """

    def __init__(
        self,
        bot_id: str,
        pat: str,
        api_base: str = "https://api.coze.com",
        timeout_seconds: float = 3.5,
    ):
        self.bot_id = bot_id
        self.pat = pat
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds

        self._headers = {
            "Authorization": f"Bearer {pat}",
            "Content-Type": "application/json",
        }

    async def chat(
        self,
        user_id: str,
        message: str,
        conversation_id: Optional[str] = None,
    ) -> dict:
        """
        Coze 봇에게 메시지를 보내고 응답을 받아옴 (타임아웃 내)

        Returns:
            {
                "success": bool,
                "text": str,
                "cards": list[dict],
                "timed_out": bool,
                "chat_id": str,
                "conversation_id": str,
                "error": str | None,
            }
        """
        start_time = time.monotonic()

        try:
            chat_data = await self._create_chat(user_id, message, conversation_id)

            if not chat_data:
                return self._error_result("Coze 대화 생성 실패")

            chat_id = chat_data.get("id", "")
            conv_id = chat_data.get("conversation_id", "")

            logger.info(f"Coze 대화 생성 완료 chat_id={chat_id}")

            elapsed = time.monotonic() - start_time
            remaining = self.timeout_seconds - elapsed

            if remaining <= 0:
                return self._timeout_result(chat_id, conv_id)

            completed = await self._poll_until_complete(chat_id, conv_id, remaining)

            if not completed:
                return self._timeout_result(chat_id, conv_id)

            messages = await self._get_messages(chat_id, conv_id)
            return self._parse_messages(messages, chat_id, conv_id)

        except httpx.TimeoutException:
            logger.warning("Coze API HTTP 타임아웃 발생")
            return self._error_result("Coze API 타임아웃")
        except Exception as e:
            logger.error(f"Coze API 호출 중 예외: {type(e).__name__}: {str(e)}")
            return self._error_result(f"Coze API 오류: {type(e).__name__}")

    async def poll_and_get_result(
        self,
        chat_id: str,
        conversation_id: str,
        max_wait: float = 55.0,
        poll_interval: float = 1.0,
    ) -> dict:
        """백그라운드에서 Coze 응답을 폴링하여 결과를 가져옴"""
        try:
            completed = await self._poll_until_complete(
                chat_id, conversation_id, max_wait, poll_interval
            )

            if not completed:
                return self._error_result("Coze 봇 응답 시간 초과 (최대 대기 시간 경과)")

            messages = await self._get_messages(chat_id, conversation_id)
            return self._parse_messages(messages, chat_id, conversation_id)

        except Exception as e:
            logger.error(f"비동기 폴링 중 예외: {type(e).__name__}: {str(e)}")
            return self._error_result(f"비동기 폴링 오류: {type(e).__name__}")

    # =========================================================================
    # 내부 메서드
    # =========================================================================

    async def _create_chat(
        self,
        user_id: str,
        message: str,
        conversation_id: Optional[str] = None,
    ) -> Optional[dict]:
        """Coze v3 Chat API로 대화 생성 (Non-streaming)"""
        url = f"{self.api_base}/v3/chat"

        body = {
            "bot_id": self.bot_id,
            "user_id": user_id,
            "stream": False,
            "auto_save_history": True,
            "additional_messages": [
                {
                    "role": "user",
                    "content": message,
                    "content_type": "text",
                }
            ],
        }

        params = {}
        if conversation_id:
            params["conversation_id"] = conversation_id

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url,
                headers=self._headers,
                json=body,
                params=params,
            )

        logger.info(f"Coze /v3/chat 응답 status={response.status_code}")

        if response.status_code != 200:
            logger.error(
                f"Coze API 에러 status={response.status_code} body={response.text[:200]}")
            return None

        result = response.json()

        if result.get("code", -1) != 0:
            logger.error(
                f"Coze API 비즈니스 에러 code={result.get('code')} msg={result.get('msg')}")
            return None

        return result.get("data", {})

    async def _poll_until_complete(
        self,
        chat_id: str,
        conversation_id: str,
        max_wait: float,
        poll_interval: float = 0.5,
    ) -> bool:
        """GET /v3/chat/retrieve를 주기적으로 호출하여 완료 여부 확인"""
        url = f"{self.api_base}/v3/chat/retrieve"
        params = {
            "chat_id": chat_id,
            "conversation_id": conversation_id,
        }

        start = time.monotonic()

        async with httpx.AsyncClient(timeout=5.0) as client:
            while (time.monotonic() - start) < max_wait:
                try:
                    response = await client.get(
                        url,
                        headers=self._headers,
                        params=params,
                    )

                    if response.status_code == 200:
                        data = response.json().get("data", {})
                        status = data.get("status", "")

                        if status == ChatStatus.COMPLETED:
                            logger.info(f"Coze 대화 완료 chat_id={chat_id}")
                            return True
                        elif status == ChatStatus.FAILED:
                            error_msg = data.get("last_error", {}).get(
                                "msg", "알 수 없는 오류")
                            logger.error(
                                f"Coze 대화 실패 chat_id={chat_id} error={error_msg}")
                            return True

                except httpx.TimeoutException:
                    logger.warning(f"폴링 HTTP 타임아웃 chat_id={chat_id}")

                await asyncio.sleep(poll_interval)

        logger.warning(f"폴링 시간 초과 chat_id={chat_id} max_wait={max_wait}s")
        return False

    async def _get_messages(
        self,
        chat_id: str,
        conversation_id: str,
    ) -> list:
        """대화의 메시지 목록 조회"""
        url = f"{self.api_base}/v3/chat/message/list"
        params = {
            "chat_id": chat_id,
            "conversation_id": conversation_id,
        }

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                url,
                headers=self._headers,
                params=params,
            )

        if response.status_code != 200:
            logger.error(f"메시지 조회 실패 status={response.status_code}")
            return []

        result = response.json()
        return result.get("data", [])

    def _parse_messages(self, messages: list, chat_id: str, conversation_id: str) -> dict:
        """
        Coze 메시지 목록에서 봇 응답(type=answer)을 추출

        파싱 우선순위:
        1. [CARDS]...[/CARDS] 태그 → 태그 내부 JSON을 카드로 추출
        2. content 전체가 순수 JSON → 카드 데이터로 처리
        3. 위 두 가지 해당 안 되면 → 텍스트 + 상품 DB 자동 매칭

        추가: type=follow_up 메시지 → suggestions 리스트로 추출
        """
        text_parts = []
        cards = []
        suggestions = []  # Coze Auto-suggestion 추천 질문

        for msg in messages:
            msg_type = msg.get("type", "")
            role = msg.get("role", "")
            content = msg.get("content", "")

            # --- follow_up 타입: 추천 질문(Auto-suggestion) 추출 ---
            if msg_type == "follow_up" and content.strip():
                suggestions.append(content.strip())
                continue

            # --- answer 타입만 본답변으로 처리 ---
            if msg_type != "answer" or role != "assistant":
                continue

            # --- 1단계: [CARDS]...[/CARDS] 태그 기반 추출 ---
            extracted_cards, remaining_text = self._extract_cards_from_tags(
                content)
            if extracted_cards:
                cards.extend(extracted_cards)
                if remaining_text.strip():
                    text_parts.append(remaining_text.strip())
                continue

            # --- 2단계: content 전체가 순수 JSON ---
            parsed_cards = self._try_parse_cards(content)
            if parsed_cards:
                cards.extend(parsed_cards)
            else:
                # --- 3단계: 일반 텍스트 ---
                if content.strip():
                    text_parts.append(content.strip())

        full_text = "\n".join(text_parts) if text_parts else ""

        # === 4단계: 카드가 없고 텍스트가 있으면 → 상품 DB 자동 매칭 ===
        if not cards and full_text:
            try:
                from app.data.product_db import get_product_db
                db = get_product_db()
                matched = db.match_from_text(full_text, max_results=3)
                if matched:
                    cards = [p.to_card_dict() for p in matched]
                    logger.info(
                        f"상품 자동 매칭 {len(cards)}개 → 카드 생성 "
                        f"[{', '.join(p.model for p in matched)}]"
                    )
            except Exception as e:
                logger.warning(f"상품 자동 매칭 실패: {type(e).__name__}: {str(e)}")

        # === 로깅: 추천 질문 수 ===
        if suggestions:
            logger.info(f"Coze 추천 질문 {len(suggestions)}개 추출: {[s[:30] for s in suggestions]}")

        return {
            "success": True,
            "text": full_text,
            "cards": cards,
            "suggestions": suggestions,
            "timed_out": False,
            "chat_id": chat_id,
            "conversation_id": conversation_id,
            "error": None,
        }

    def _extract_cards_from_tags(self, content: str) -> tuple[list, str]:
        """텍스트 안에서 [CARDS]...[/CARDS] 태그를 찾아 카드 JSON을 추출"""
        pattern = r'\[CARDS\]\s*(.*?)\s*\[/CARDS\]'
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)

        if not match:
            return [], content

        json_str = match.group(1).strip()
        cards = self._try_parse_cards(json_str)

        if not cards:
            logger.warning(f"[CARDS] 태그 발견했으나 JSON 파싱 실패: {json_str[:200]}")
            remaining = re.sub(pattern, '', content,
                               flags=re.DOTALL | re.IGNORECASE)
            return [], remaining

        remaining = re.sub(pattern, '', content,
                           flags=re.DOTALL | re.IGNORECASE)

        logger.info(f"[CARDS] 태그에서 카드 {len(cards)}개 추출 완료")
        return cards, remaining

    def _try_parse_cards(self, content: str) -> list:
        """Coze 응답에서 카드형 데이터를 JSON 파싱 시도"""
        content = content.strip()
        if not content:
            return []

        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return []

        card_keys = {"image_url", "product_name", "title", "description",
                     "price", "discount_price", "discount", "button_url"}

        if isinstance(data, list):
            cards = [item for item in data if isinstance(
                item, dict) and card_keys & set(item.keys())]
            return cards if cards else []

        if isinstance(data, dict):
            for wrapper_key in ("products", "items", "data", "cards"):
                if wrapper_key in data and isinstance(data[wrapper_key], list):
                    cards = [
                        item for item in data[wrapper_key]
                        if isinstance(item, dict) and card_keys & set(item.keys())
                    ]
                    if cards:
                        return cards

            if card_keys & set(data.keys()):
                return [data]

        return []

    def _timeout_result(self, chat_id: str, conversation_id: str) -> dict:
        """타임아웃 결과"""
        return {
            "success": False,
            "text": "",
            "cards": [],
            "suggestions": [],
            "timed_out": True,
            "chat_id": chat_id,
            "conversation_id": conversation_id,
            "error": None,
        }

    def _error_result(self, error_msg: str) -> dict:
        """에러 결과"""
        return {
            "success": False,
            "text": "",
            "cards": [],
            "suggestions": [],
            "timed_out": False,
            "chat_id": "",
            "conversation_id": "",
            "error": error_msg,
        }


def get_coze_client(
    bot_id: Optional[str] = None,
    pat: Optional[str] = None,
    api_base: Optional[str] = None,
    timeout: Optional[float] = None,
) -> "CozeClient":
    """CozeClient 팩토리 함수"""
    settings = get_settings()
    return CozeClient(
        bot_id=bot_id or settings.COZE_BOT_ID,
        pat=pat or settings.COZE_PAT,
        api_base=api_base or settings.COZE_API_BASE,
        timeout_seconds=timeout or settings.COZE_TIMEOUT,
    )


def get_coze_client_for_client(client_key: Optional[str] = None) -> "CozeClient":
    """멀티 고객사용 CozeClient 팩토리"""
    from app.config.client_config import get_client_config

    config = get_client_config(client_key)

    if config is None:
        raise ValueError(f"고객사 설정을 찾을 수 없습니다: {client_key}")

    if not config.is_valid():
        raise ValueError(f"고객사 설정이 불완전합니다: {client_key}")

    return CozeClient(
        bot_id=config.coze_bot_id,
        pat=config.coze_pat,
        api_base=config.coze_api_base,
        timeout_seconds=config.timeout_seconds,
    )
