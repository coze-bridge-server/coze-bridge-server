"""
카카오 오픈빌더 스킬 서버 핸들러

담당 역할:
- 카카오 오픈빌더의 스킬 요청(SkillPayload)을 파싱
- Coze API를 호출하여 봇 응답을 받아옴
- Coze 응답을 카카오 SkillResponse 정규 포맷으로 변환
- 5초 타임아웃 초과 시 useCallback + callbackUrl 비동기 처리
- 분할 전송: 안내 메시지 즉시 표시 → 본답변 콜백 후발송

카드형 응답:
- Coze가 상품 JSON을 반환하면 BasicCard Carousel로 자동 변환
- 카드 모듈: app.cards.kakao_card.build_kakao_card_output()

카카오 SkillResponse 스펙 참고:
- https://kakaobusiness.gitbook.io/main/tool/chatbot/skill_guide/answer_json_format
- https://kakaobusiness.gitbook.io/main/tool/chatbot/skill_guide/ai_chatbot_callback_guide
"""
import asyncio
import time
from typing import Any, Optional

import httpx

from app.handlers.base import BaseMessageHandler
from app.modules.coze_client import CozeClient
from app.cards.kakao_card import build_kakao_card_output
from app.config.logging import logger
from app.config.settings import get_settings


class KakaoHandler(BaseMessageHandler):
    """
    카카오톡 오픈빌더 SkillResponse 핸들러

    생성 시 CozeClient 인스턴스를 주입받아 사용
    멀티 고객사 환경에서는 고객사별로 다른 CozeClient가 주입됨
    """

    def __init__(self, coze_client: CozeClient):
        """
        Args:
            coze_client: 해당 고객사용 Coze API 클라이언트
        """
        self._coze = coze_client

    # =========================================================================
    # 1. 요청 파싱 — 카카오 SkillPayload -> 공통 내부 포맷
    # =========================================================================

    async def parse_request(self, raw_request: dict) -> dict:
        """
        카카오 오픈빌더 SkillPayload에서 필요한 정보를 추출

        Returns:
            {
                "user_id": str,
                "message": str,
                "callback_url": str,
                "raw": dict,
            }
        """
        user_request = raw_request.get("userRequest", {})
        user_info = user_request.get("user", {})

        user_id = user_info.get("id", "unknown")
        utterance = user_request.get("utterance", "")
        callback_url = user_request.get("callbackUrl", "")

        logger.info(
            f"카카오 요청 파싱 완료 "
            f"user_id={user_id} "
            f"utterance={utterance[:50]} "
            f"has_callback={'Y' if callback_url else 'N'}"
        )

        return {
            "user_id": user_id,
            "message": utterance,
            "callback_url": callback_url,
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
            f"Coze 호출 결과 "
            f"success={result['success']} "
            f"timed_out={result['timed_out']} "
            f"has_text={'Y' if result['text'] else 'N'} "
            f"cards={len(result['cards'])}"
        )

        return result

    # =========================================================================
    # 3. 응답 포맷팅 — Coze 결과 -> 카카오 SkillResponse
    # =========================================================================

    async def format_response(self, coze_result: dict, parsed: dict) -> dict:
        """
        Coze 응답을 카카오 SkillResponse로 변환

        텍스트 + 카드 동시 응답 지원:
        - 카드만 있음 → carousel만 출력
        - 텍스트 + 카드 → simpleText + carousel 동시 출력
        - 텍스트만 있음 → simpleText 출력
        """
        if not coze_result["success"] and not coze_result["timed_out"]:
            return self._error_response("죄송합니다 일시적인 오류가 발생했습니다")

        outputs = []
        text = coze_result.get("text", "")
        cards = coze_result.get("cards", [])

        # --- 카드 빌드 ---
        card_output = []
        if cards:
            try:
                card_output = build_kakao_card_output(cards)
            except Exception as e:
                logger.error(f"카카오 카드 빌드 예외: {type(e).__name__}: {str(e)}")

        # --- 텍스트 + 카드 조합 ---
        if text and card_output:
            # 텍스트 먼저 → 카드 뒤에 (카카오는 outputs 배열 순서대로 표시)
            if len(text) > 1000:
                text = text[:997] + "..."
            outputs.append({"simpleText": {"text": text}})
            outputs.extend(card_output)
        elif card_output:
            # 카드만
            outputs.extend(card_output)
        elif text:
            # 텍스트만
            if len(text) > 1000:
                text = text[:997] + "..."
            outputs.append({"simpleText": {"text": text}})
        else:
            outputs.append({"simpleText": {"text": "죄송합니다 응답을 생성하지 못했습니다"}})

        return {
            "version": "2.0",
            "template": {"outputs": outputs}
        }

    # =========================================================================
    # 4. 타임아웃 처리
    # =========================================================================

    async def handle_timeout(self, parsed: dict) -> dict:
        """
        5초 타임아웃 초과 시 카카오 콜백 응답 반환

        분할 전송 1단계: useCallback=True + data.text로 안내 메시지 즉시 표시
        안내 메시지는 .env의 GUIDE_MESSAGE_KAKAO에서 읽음
        """
        settings = get_settings()
        guide_msg = settings.GUIDE_MESSAGE_KAKAO

        logger.info(f"카카오 분할전송 안내메시지 발송 user={parsed['user_id']}")

        return {
            "version": "2.0",
            "useCallback": True,
            "data": {
                "text": guide_msg
            }
        }

    # =========================================================================
    # 5. 메인 파이프라인
    # =========================================================================

    async def handle(self, raw_request: dict) -> dict:
        """
        카카오 스킬 요청 처리 메인 파이프라인 (분할 전송 적용)

        처리 흐름:
        1. 요청 파싱 + 타이머 시작
        2. Coze API 호출 (timeout_seconds 타임아웃)
        3-A. 타임아웃 내 응답 완료 -> 즉시 SkillResponse 반환
        3-B. 타임아웃 초과 + callbackUrl 있음 -> 안내 메시지(useCallback) + 백그라운드 콜백
        3-C. 타임아웃 초과 + callbackUrl 없음 -> 에러 메시지 반환
        """
        settings = get_settings()
        request_start = time.monotonic()

        # --- Step 1: 요청 파싱 ---
        parsed = await self.parse_request(raw_request)

        if not parsed["message"].strip():
            return self._text_response("메시지를 입력해주세요")

        # --- Step 2: Coze API 호출 ---
        coze_result = await self.call_coze(parsed)

        # --- Step 3-A: 정상 응답 ---
        if coze_result["success"] and not coze_result["timed_out"]:
            elapsed = time.monotonic() - request_start
            logger.info(f"카카오 즉시응답 완료 elapsed={elapsed:.2f}s user={parsed['user_id']}")
            return await self.format_response(coze_result, parsed)

        # --- Step 3-B: 타임아웃 + 콜백 가능 → 분할 전송 ---
        if coze_result["timed_out"] and parsed["callback_url"]:
            guide_elapsed = time.monotonic() - request_start
            logger.info(
                f"카카오 분할전송 진입 "
                f"guide_elapsed={guide_elapsed:.2f}s "
                f"chat_id={coze_result['chat_id']} "
                f"callback_url 존재"
            )

            asyncio.create_task(
                self._async_callback(
                    callback_url=parsed["callback_url"],
                    chat_id=coze_result["chat_id"],
                    conversation_id=coze_result["conversation_id"],
                    parsed=parsed,
                )
            )

            return await self.handle_timeout(parsed)

        # --- Step 3-C: 타임아웃 + 콜백 불가 ---
        if coze_result["timed_out"] and not parsed["callback_url"]:
            logger.warning("카카오 타임아웃 발생 but 콜백URL 없음 -> 에러 응답")
            return self._error_response(settings.ERROR_MESSAGE)

        # --- 기타 에러 ---
        error_msg = coze_result.get("error", "알 수 없는 오류")
        logger.error(f"카카오 Coze 호출 실패: {error_msg}")
        return self._error_response(settings.ERROR_MESSAGE)

    # =========================================================================
    # 비동기 콜백
    # =========================================================================

    async def _async_callback(
        self,
        callback_url: str,
        chat_id: str,
        conversation_id: str,
        parsed: dict,
    ) -> None:
        """
        백그라운드 태스크: Coze 폴링 완료 후 callbackUrl로 본답변 전송

        분할 전송 2단계: 안내 메시지 이후 실제 AI 응답을 콜백으로 전달
        콜백 URL 유효시간: 1분 → max_wait=55초로 안전마진 확보
        """
        settings = get_settings()
        callback_start = time.monotonic()

        try:
            logger.info(f"카카오 콜백 백그라운드 시작 chat_id={chat_id}")

            coze_result = await self._coze.poll_and_get_result(
                chat_id=chat_id,
                conversation_id=conversation_id,
                max_wait=55.0,
                poll_interval=1.0,
            )

            if coze_result["success"]:
                response_body = await self.format_response(coze_result, parsed)
            else:
                error_msg = coze_result.get("error", "응답 생성에 실패했습니다")
                logger.error(f"카카오 콜백 Coze 폴링 실패: {error_msg}")
                response_body = self._error_response(settings.ERROR_MESSAGE)

            # 콜백 전송 전 최소 대기 (카카오 서버 안정성)
            await asyncio.sleep(1.0)

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    callback_url,
                    json=response_body,
                    headers={"Content-Type": "application/json"},
                )

            callback_elapsed = time.monotonic() - callback_start
            logger.info(
                f"카카오 콜백 전송 완료 "
                f"status={resp.status_code} "
                f"chat_id={chat_id} "
                f"callback_elapsed={callback_elapsed:.2f}s"
            )

            if resp.status_code == 200:
                try:
                    callback_resp = resp.json()
                    status = callback_resp.get("status", "")
                    if status != "SUCCESS":
                        logger.warning(
                            f"카카오 콜백 응답 비정상 "
                            f"status={status} "
                            f"message={callback_resp.get('message', '')}"
                        )
                except Exception:
                    pass
            else:
                try:
                    error_body = resp.text
                    logger.error(
                        f"카카오 콜백 전송 실패 "
                        f"status={resp.status_code} "
                        f"body={error_body[:500]} "
                        f"chat_id={chat_id}"
                    )
                except Exception:
                    pass

        except httpx.TimeoutException:
            logger.error(f"카카오 콜백 전송 HTTP 타임아웃 chat_id={chat_id}")
        except Exception as e:
            logger.error(
                f"카카오 콜백 백그라운드 예외: "
                f"{type(e).__name__}: {str(e)} "
                f"chat_id={chat_id}"
            )

    # =========================================================================
    # 헬퍼 메서드
    # =========================================================================

    @staticmethod
    def _text_response(text: str) -> dict:
        """simpleText SkillResponse 생성 (최대 1000자)"""
        if len(text) > 1000:
            text = text[:997] + "..."

        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": text
                        }
                    }
                ]
            }
        }

    @staticmethod
    def _error_response(message: str) -> dict:
        """에러 상황에서 카카오 정규 포맷 에러 응답 생성"""
        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {
                        "simpleText": {
                            "text": message
                        }
                    }
                ]
            }
        }

    @staticmethod
    def _cards_to_text_fallback(cards: list) -> str:
        """카드 빌드 실패 시 카드 데이터를 텍스트로 변환하는 폴백"""
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
