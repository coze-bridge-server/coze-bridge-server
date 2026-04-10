"""
네이버톡톡 웹훅 서버 핸들러

담당 역할:
- 네이버톡톡 웹훅 이벤트(open/send/leave/friend) 수신 및 파싱
- send 이벤트일 때만 Coze API 호출하여 봇 응답 생성
- Coze 응답을 네이버톡톡 textContent/compositeContent 포맷으로 변환
- 5초 Read Timeout 초과 시 안내 메시지 선발송 + 빈 200 OK 반환 + 비동기 본답변
- 분할 전송: 보내기 API로 안내 메시지 즉시 전송 → 본답변 후발송

카드형 응답:
- Coze가 상품 JSON을 반환하면 compositeContent Carousel로 자동 변환
- 카드 모듈: app.cards.navertalk_card.build_navertalk_card_response()

네이버톡톡 API 스펙 참고:
- https://github.com/navertalk/chatbot-api
"""
import asyncio
import time
from typing import Any, Optional

import httpx

from app.handlers.base import BaseMessageHandler
from app.modules.coze_client import CozeClient
from app.cards.navertalk_card import build_navertalk_card_response
from app.config.logging import logger
from app.config.settings import get_settings


# --- 네이버톡톡 보내기 API URL ---
NAVER_TALK_SEND_API = "https://gw.talk.naver.com/chatbot/v1/event"


class NaverTalkHandler(BaseMessageHandler):
    """
    네이버톡톡 웹훅 핸들러

    생성 시 CozeClient + 네이버톡톡 인증 토큰을 주입받아 사용
    멀티 고객사 환경에서는 고객사별로 다른 설정이 주입됨
    """

    def __init__(self, coze_client: CozeClient, naver_talk_token: str):
        """
        Args:
            coze_client: 해당 고객사용 Coze API 클라이언트
            naver_talk_token: 네이버톡톡 보내기 API 인증 토큰
        """
        self._coze = coze_client
        self._token = naver_talk_token

    # =========================================================================
    # 1. 요청 파싱
    # =========================================================================

    async def parse_request(self, raw_request: dict) -> dict:
        """네이버톡톡 웹훅 이벤트에서 필요한 정보를 추출"""
        event = raw_request.get("event", "")
        user_id = raw_request.get("user", "unknown")
        options = raw_request.get("options", {})

        message = ""
        input_type = ""

        if event == "send":
            text_content = raw_request.get("textContent", {})
            if text_content:
                message = text_content.get("text", "")
                input_type = text_content.get("inputType", "typing")

            image_content = raw_request.get("imageContent", {})
            if not message and image_content:
                message = image_content.get("imageUrl", "")
                input_type = "image"

            if not message and text_content:
                code = text_content.get("code", "")
                if code:
                    message = code
                    input_type = "button"

        logger.info(
            f"네이버톡톡 요청 파싱 완료 "
            f"event={event} "
            f"user_id={user_id[:10]}... "
            f"message={message[:50] if message else '(없음)'} "
            f"input_type={input_type}"
        )

        return {
            "event": event,
            "user_id": user_id,
            "message": message,
            "input_type": input_type,
            "options": options,
            "raw": raw_request,
        }

    # =========================================================================
    # 2. Coze API 호출
    # =========================================================================

    async def call_coze(self, parsed: dict) -> dict:
        """Coze API를 호출하여 봇 응답을 받아옴"""
        result = await self._coze.chat(
            user_id=parsed["user_id"],
            message=parsed["message"],
        )

        logger.info(
            f"Coze 호출 결과 (네이버톡톡) "
            f"success={result['success']} "
            f"timed_out={result['timed_out']} "
            f"has_text={'Y' if result['text'] else 'N'} "
            f"cards={len(result['cards'])}"
        )

        return result

    # =========================================================================
    # 3. 응답 포맷팅
    # =========================================================================

    async def format_response(self, coze_result: dict, parsed: dict) -> dict:
        """
        Coze 응답을 네이버톡톡 응답 포맷으로 변환

        톡톡은 한 응답에 textContent OR compositeContent 하나만 가능
        → 카드가 있으면 카드를 메인 응답으로, 텍스트는 보내기 API로 선발송

        추가: suggestions → compositeContent의 buttonList로 변환
        (텍스트 응답 시 compositeContent로 전환하여 버튼 추가)
        """
        if not coze_result["success"] and not coze_result["timed_out"]:
            return self._text_response("죄송합니다 일시적인 오류가 발생했습니다")

        text = coze_result.get("text", "")
        cards = coze_result.get("cards", [])
        suggestions = coze_result.get("suggestions", [])

        # --- 카드 빌드 ---
        card_response = None
        if cards:
            try:
                card_response = build_navertalk_card_response(cards)
            except Exception as e:
                logger.error(f"네이버톡톡 카드 빌드 예외: {type(e).__name__}: {str(e)}")

        # --- 텍스트 + 카드 동시 존재 ---
        if text and card_response:
            # 텍스트를 보내기 API로 먼저 전송 후 카드를 웹훅 응답으로 반환
            user_id = parsed.get("user_id", "")
            if user_id:
                asyncio.create_task(self._send_text_before_card(user_id, text))
            return card_response

        # --- 카드만 ---
        if card_response:
            return card_response

        # --- 텍스트만 (+ 추천 질문 버튼) ---
        if text:
            # suggestions가 있으면 compositeContent로 전환하여 버튼 추가
            suggestion_buttons = self._build_suggestion_buttons(suggestions)
            if suggestion_buttons:
                return self._text_with_buttons_response(text, suggestion_buttons)
            return self._text_response(text)

        return self._text_response("죄송합니다 응답을 생성하지 못했습니다")

    async def _send_text_before_card(self, user_id: str, text: str) -> None:
        """카드 응답 전에 텍스트를 보내기 API로 선발송"""
        try:
            await self.send_message(user_id, text)
            logger.info(f"네이버톡톡 텍스트 선발송 완료 user={user_id[:10]}...")
        except Exception as e:
            logger.warning(f"네이버톡톡 텍스트 선발송 실패: {type(e).__name__}: {str(e)}")

    # =========================================================================
    # 4. 타임아웃 처리
    # =========================================================================

    async def handle_timeout(self, parsed: dict) -> dict:
        """
        5초 초과 시 안내 메시지 선발송 후 빈 200 OK 반환

        분할 전송 1단계: 보내기 API로 안내 메시지를 즉시 전송
        네이버톡톡은 웹훅 응답으로는 안내 메시지를 줄 수 없으므로
        보내기 API(POST)로 별도 전송 → 빈 200 반환하여 웹훅 종료
        """
        settings = get_settings()
        guide_msg = settings.GUIDE_MESSAGE_NAVER
        user_id = parsed.get("user_id", "")

        if user_id:
            guide_start = time.monotonic()
            sent = await self.send_message(user_id, guide_msg)
            guide_elapsed = time.monotonic() - guide_start
            logger.info(
                f"네이버톡톡 분할전송 안내메시지 "
                f"sent={sent} "
                f"elapsed={guide_elapsed:.3f}s "
                f"user={user_id[:10]}..."
            )

        return {}

    # =========================================================================
    # 5. 메인 파이프라인
    # =========================================================================

    async def handle(self, raw_request: dict) -> dict:
        """
        네이버톡톡 웹훅 이벤트 처리 메인 파이프라인 (분할 전송 적용)

        send 이벤트 분할 전송 흐름:
        1. Coze 호출 (timeout_seconds 타임아웃)
        2-A. 타임아웃 내 완료 → 즉시 웹훅 응답
        2-B. 타임아웃 초과 → 안내 메시지 선발송(보내기 API) + 빈 200 반환 + 백그라운드 본답변
        """
        settings = get_settings()
        parsed = await self.parse_request(raw_request)
        event = parsed["event"]

        # open 이벤트
        if event == "open":
            return self._handle_open(parsed)

        # leave 이벤트
        if event == "leave":
            logger.info(f"네이버톡톡 leave 이벤트 user={parsed['user_id'][:10]}...")
            return {}

        # friend 이벤트
        if event == "friend":
            return self._handle_friend(parsed)

        # echo / action / persistentMenu -> 무시
        if event in ("echo", "action", "persistentMenu"):
            return {}

        # send 이벤트가 아니면 빈 200 반환
        if event != "send":
            logger.warning(f"네이버톡톡 미지원 이벤트: {event}")
            return {}

        # --- send 이벤트 처리 ---

        if not parsed["message"].strip():
            logger.info("네이버톡톡 빈 메시지 수신 -> 무시")
            return {}

        request_start = time.monotonic()
        coze_result = await self.call_coze(parsed)

        # 정상 응답
        if coze_result["success"] and not coze_result["timed_out"]:
            elapsed = time.monotonic() - request_start
            logger.info(f"네이버톡톡 즉시응답 완료 elapsed={elapsed:.2f}s user={parsed['user_id'][:10]}...")
            return await self.format_response(coze_result, parsed)

        # 타임아웃 → 분할 전송 (안내 메시지 선발송 + 비동기 본답변)
        if coze_result["timed_out"]:
            guide_elapsed = time.monotonic() - request_start
            logger.info(
                f"네이버톡톡 분할전송 진입 "
                f"guide_elapsed={guide_elapsed:.2f}s "
                f"chat_id={coze_result['chat_id']} "
                f"user={parsed['user_id'][:10]}..."
            )

            asyncio.create_task(
                self._async_send(
                    user_id=parsed["user_id"],
                    chat_id=coze_result["chat_id"],
                    conversation_id=coze_result["conversation_id"],
                    parsed=parsed,
                )
            )

            return await self.handle_timeout(parsed)

        # 기타 에러
        error_msg = coze_result.get("error", "알 수 없는 오류")
        logger.error(f"네이버톡톡 Coze 호출 실패: {error_msg}")
        return self._text_response(settings.ERROR_MESSAGE)

    # =========================================================================
    # 이벤트 핸들러
    # =========================================================================

    def _handle_open(self, parsed: dict) -> dict:
        """open 이벤트 — 환영 메시지"""
        options = parsed.get("options", {})
        inflow = options.get("inflow", "none")

        logger.info(
            f"네이버톡톡 open 이벤트 "
            f"user={parsed['user_id'][:10]}... "
            f"inflow={inflow}"
        )

        return self._text_response("안녕하세요! 무엇을 도와드릴까요?")

    def _handle_friend(self, parsed: dict) -> dict:
        """friend 이벤트 — 친구 추가/해제"""
        options = parsed.get("options", {})
        friend_set = options.get("set", "")

        logger.info(
            f"네이버톡톡 friend 이벤트 "
            f"user={parsed['user_id'][:10]}... "
            f"set={friend_set}"
        )

        if friend_set == "on":
            return self._text_response("친구 추가 감사합니다! 무엇이든 물어보세요")
        elif friend_set == "off":
            return {}

        return {}

    # =========================================================================
    # 비동기 보내기
    # =========================================================================

    async def _async_send(
        self,
        user_id: str,
        chat_id: str,
        conversation_id: str,
        parsed: dict,
    ) -> None:
        """
        백그라운드 태스크: Coze 폴링 완료 후 네이버톡톡 보내기 API로 본답변 전송

        분할 전송 2단계: 안내 메시지 이후 실제 AI 응답을 보내기 API로 전달
        """
        settings = get_settings()
        send_start = time.monotonic()

        try:
            logger.info(f"네이버톡톡 비동기 전송 시작 chat_id={chat_id}")

            coze_result = await self._coze.poll_and_get_result(
                chat_id=chat_id,
                conversation_id=conversation_id,
                max_wait=55.0,
                poll_interval=1.0,
            )

            if not coze_result["success"]:
                error_msg = coze_result.get("error", "응답 생성에 실패했습니다")
                logger.error(f"네이버톡톡 비동기 Coze 폴링 실패: {error_msg}")
                await self._send_and_log(user_id, chat_id, self._text_response(settings.ERROR_MESSAGE))
                return

            # --- 텍스트와 카드를 순서대로 분리 전송 ---
# --- 텍스트와 카드를 순서대로 분리 전송 ---
            text = coze_result.get("text", "")
            cards = coze_result.get("cards", [])
            suggestions = coze_result.get("suggestions", [])

            card_response = None
            if cards:
                try:
                    card_response = build_navertalk_card_response(cards)
                except Exception as e:
                    logger.error(
                        f"네이버톡톡 비동기 카드 빌드 예외: {type(e).__name__}: {str(e)}")

            # 추천 질문 버튼 빌드 (텍스트 전송 시 함께 표시)
            suggestion_buttons = self._build_suggestion_buttons(suggestions)

            if text and card_response:
                # 1) 텍스트 먼저 전송 (추천 질문 버튼 포함)
                if suggestion_buttons:
                    await self._send_and_log(user_id, chat_id, self._text_with_buttons_response(text, suggestion_buttons), label="텍스트+버튼")
                else:
                    await self._send_and_log(user_id, chat_id, self._text_response(text), label="텍스트")
                # 2) 카드 후발송
                await self._send_and_log(user_id, chat_id, card_response, label="카드")
            elif card_response:
                await self._send_and_log(user_id, chat_id, card_response, label="카드")
            elif text:
                # 텍스트만 전송 (추천 질문 버튼 포함)
                if suggestion_buttons:
                    await self._send_and_log(user_id, chat_id, self._text_with_buttons_response(text, suggestion_buttons), label="텍스트+버튼")
                else:
                    await self._send_and_log(user_id, chat_id, self._text_response(text), label="텍스트")
            else:
                await self._send_and_log(user_id, chat_id, self._text_response("죄송합니다 응답을 생성하지 못했습니다"))

            send_elapsed = time.monotonic() - send_start
            logger.info(f"네이버톡톡 비동기 전송 전체 완료 chat_id={chat_id} total_elapsed={send_elapsed:.2f}s")

        except httpx.TimeoutException:
            logger.error(f"네이버톡톡 보내기 API HTTP 타임아웃 chat_id={chat_id}")
        except Exception as e:
            logger.error(
                f"네이버톡톡 비동기 전송 예외: "
                f"{type(e).__name__}: {str(e)} "
                f"chat_id={chat_id}"
            )

    # =========================================================================
    # 보내기 API 공용 전송 + 로깅
    # =========================================================================

    async def _send_and_log(
        self, user_id: str, chat_id: str, response_body: dict, label: str = "응답"
    ) -> None:
        """보내기 API로 전송 + 결과 로깅하는 공용 메서드"""
        send_payload = {**response_body, "user": user_id}

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                NAVER_TALK_SEND_API,
                json=send_payload,
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "Authorization": self._token,
                },
            )

        logger.info(
            f"네이버톡톡 보내기 API {label} 전송 "
            f"status={resp.status_code} "
            f"chat_id={chat_id}"
        )

        if resp.status_code == 200:
            try:
                result = resp.json()
                if not result.get("success", False):
                    logger.warning(
                        f"네이버톡톡 보내기 API 비정상 응답 "
                        f"code={result.get('resultCode')} "
                        f"msg={result.get('resultMessage', '')}"
                    )
            except Exception:
                pass
        else:
            logger.error(
                f"네이버톡톡 보내기 API HTTP 에러 "
                f"status={resp.status_code} "
                f"body={resp.text[:200]}"
            )

    # =========================================================================
    # 동기 보내기
    # =========================================================================

    async def send_message(self, user_id: str, text: str) -> bool:
        """네이버톡톡 보내기 API를 직접 호출하여 메시지 전송"""
        payload = {
            "event": "send",
            "user": user_id,
            "textContent": {"text": text},
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    NAVER_TALK_SEND_API,
                    json=payload,
                    headers={
                        "Content-Type": "application/json;charset=UTF-8",
                        "Authorization": self._token,
                    },
                )

            if resp.status_code == 200:
                result = resp.json()
                return result.get("success", False)

            logger.error(f"보내기 API 실패 status={resp.status_code}")
            return False

        except Exception as e:
            logger.error(f"보내기 API 예외: {type(e).__name__}: {str(e)}")
            return False

    # =========================================================================
    # 헬퍼 메서드
    # =========================================================================

    @staticmethod
    def _build_suggestion_buttons(suggestions: list[str]) -> list[dict]:
        """
        Coze Auto-suggestion 추천 질문을 네이버톡톡 buttonList로 변환

        - title: 최대 SUGGESTION_MAX_LENGTH_TALK 글자 (초과 시 "…" 붙여서 자르기)
        - code: 원본 전체 텍스트 (클릭 시 code 값이 send 이벤트로 전송)
        - 네이버톡톡 buttonList 최대 10개 제한

        네이버톡톡 TEXT 버튼 스펙:
        {
            "type": "TEXT",
            "data": {
                "title": "표시 텍스트 (18자)",
                "code": "실제 전송 텍스트 (원본)"
            }
        }
        """
        if not suggestions:
            return []

        settings = get_settings()
        max_len = settings.SUGGESTION_MAX_LENGTH_TALK
        buttons = []

        for suggestion in suggestions[:10]:  # 톡톡 최대 10개
            original = suggestion.strip()
            if not original:
                continue

            # title truncate: 최대 글자수 초과 시 "…" 붙이기
            if len(original) > max_len:
                title = original[:max_len - 1] + "…"
            else:
                title = original

            buttons.append({
                "type": "TEXT",
                "data": {
                    "title": title,
                    "code": original,  # 원본 전체 텍스트가 발화로 전송
                },
            })

        if buttons:
            logger.info(f"네이버톡톡 suggestion 버튼 {len(buttons)}개 생성")

        return buttons

    @staticmethod
    def _text_with_buttons_response(text: str, buttons: list[dict]) -> dict:
        """
        텍스트 + 추천 질문 버튼을 compositeContent로 변환

        네이버톡톡은 textContent에 buttonList를 넣을 수 없으므로
        compositeContent로 전환하여 텍스트 + 버튼을 함께 표시
        """
        if not text or not text.strip():
            text = "죄송합니다 응답을 생성하지 못했습니다"

        # compositeContent title 최대 200자 제한 (네이버톡톡 공식)
        if len(text) > 200:
            text = text[:197] + "..."

        composite = {
            "title": text,
        }

        if buttons:
            composite["buttonList"] = buttons

        return {
            "event": "send",
            "compositeContent": {
                "compositeList": [composite],
            },
        }

    @staticmethod
    def _text_response(text: str) -> dict:
        """네이버톡톡 textContent 응답 생성"""
        if not text or not text.strip():
            text = "죄송합니다 응답을 생성하지 못했습니다"

        return {
            "event": "send",
            "textContent": {
                "text": text,
            },
        }

    @staticmethod
    def _error_response(message: str) -> dict:
        """에러 상황에서 네이버톡톡 응답 생성"""
        return {
            "event": "send",
            "textContent": {
                "text": message,
            },
        }

    @staticmethod
    def _cards_to_text_fallback(cards: list) -> str:
        """카드 빌드 실패 시 텍스트 폴백"""
        lines = []
        for i, card in enumerate(cards, 1):
            parts = []
            name = card.get("product_name") or card.get("title", "")
            if name:
                parts.append(f"[{name}]")
            desc = card.get("description", "")
            if desc:
                parts.append(desc)
            price = card.get("price", "")
            if price:
                parts.append(f"가격: {price}")
            url = card.get("button_url", "")
            if url:
                parts.append(url)

            if parts:
                lines.append(f"{i}. " + " / ".join(parts))

        return "\n".join(lines)
