"""
카카오 오픈빌더 스킬 서버 핸들러

담당 역할:
- 카카오 오픈빌더의 스킬 요청(SkillPayload)을 파싱
- Coze API를 호출하여 봇 응답을 받아옴
- Coze 응답을 카카오 SkillResponse 정규 포맷으로 변환
- 5초 타임아웃 초과 시 useCallback + callbackUrl 비동기 처리
- 분할 전송: 안내 메시지 즉시 표시 → 본답변 콜백 후발송
- [신규] 첫 메시지 시 웰컴카드(이미지+인사말+버튼3개) 자동 발송
- [신규] Coze Auto-suggestion 추천 질문 → quickReplies 변환 (label truncate)

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


# =========================================================================
# 인메모리 세션 저장소 — 사용자별 첫 메시지 여부 판별
# =========================================================================
# 구조: { user_id: last_message_timestamp }
# 세션 만료 시간(WELCOME_SESSION_TIMEOUT) 이후 재진입 시 다시 웰컴 표시
_user_sessions: dict[str, float] = {}


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
            f"cards={len(result['cards'])} "
            f"suggestions={len(result.get('suggestions', []))}"
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

        추가: suggestions → quickReplies 변환 (label truncate / action value 원본)
        """
        if not coze_result["success"] and not coze_result["timed_out"]:
            return self._error_response("죄송합니다 일시적인 오류가 발생했습니다")

        outputs = []
        text = coze_result.get("text", "")
        cards = coze_result.get("cards", [])
        suggestions = coze_result.get("suggestions", [])

        # --- 카드 빌드 ---
        card_output = []
        if cards:
            try:
                card_output = build_kakao_card_output(cards)
            except Exception as e:
                logger.error(f"카카오 카드 빌드 예외: {type(e).__name__}: {str(e)}")

        # --- 텍스트 + 카드 조합 ---
        if text and card_output:
            if len(text) > 1000:
                text = text[:997] + "..."
            outputs.append({"simpleText": {"text": text}})
            outputs.extend(card_output)
        elif card_output:
            outputs.extend(card_output)
        elif text:
            if len(text) > 1000:
                text = text[:997] + "..."
            outputs.append({"simpleText": {"text": text}})
        else:
            outputs.append({"simpleText": {"text": "죄송합니다 응답을 생성하지 못했습니다"}})

        result = {
            "version": "2.0",
            "template": {"outputs": outputs}
        }

        # --- quickReplies: Coze 추천 질문을 카카오 퀵리플라이로 변환 ---
        quick_replies = self._build_quick_replies(suggestions)
        if quick_replies:
            result["template"]["quickReplies"] = quick_replies

        return result

    # =========================================================================
    # 3-1. quickReplies 빌드 — suggestion truncate 적용
    # =========================================================================

    @staticmethod
    def _build_quick_replies(suggestions: list[str]) -> list[dict]:
        """
        Coze Auto-suggestion 추천 질문을 카카오 quickReplies로 변환

        - label: 최대 SUGGESTION_MAX_LENGTH_KAKAO 글자 (초과 시 "…" 붙여서 자르기)
        - messageText: 원본 전체 텍스트 (클릭 시 발화로 전송)
        - 카카오 quickReplies 최대 10개 제한
        """
        if not suggestions:
            return []

        settings = get_settings()
        max_len = settings.SUGGESTION_MAX_LENGTH_KAKAO
        quick_replies = []

        for suggestion in suggestions[:10]:  # 카카오 최대 10개
            original = suggestion.strip()
            if not original:
                continue

            # label truncate: 최대 글자수 초과 시 "…" 붙이기
            if len(original) > max_len:
                label = original[:max_len - 1] + "…"
            else:
                label = original

            quick_replies.append({
                "action": "message",
                "label": label,
                "messageText": original,  # 원본 전체 텍스트가 발화로 전송
            })

        if quick_replies:
            logger.info(f"카카오 quickReplies {len(quick_replies)}개 생성")

        return quick_replies

    # =========================================================================
    # 4. 타임아웃 처리
    # =========================================================================

    async def handle_timeout(self, parsed: dict) -> dict:
        """
        5초 타임아웃 초과 시 카카오 콜백 응답 반환

        분할 전송 1단계: useCallback=True + data.text로 안내 메시지 즉시 표시
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
    # 5. 웰컴카드 — 첫 메시지 시 자동 표시
    # =========================================================================

    def _is_first_message(self, user_id: str) -> bool:
        """
        사용자의 첫 메시지 여부 판별
        세션 저장소에 user_id가 없거나 세션 만료 시간 초과 → 첫 메시지로 판정
        """
        settings = get_settings()
        now = time.time()

        if user_id not in _user_sessions:
            return True

        last_time = _user_sessions[user_id]
        elapsed = now - last_time

        if elapsed > settings.WELCOME_SESSION_TIMEOUT:
            logger.info(
                f"카카오 세션 만료 user={user_id} "
                f"elapsed={elapsed:.0f}s > timeout={settings.WELCOME_SESSION_TIMEOUT}s"
            )
            return True

        return False

    def _update_session(self, user_id: str) -> None:
        """세션 타임스탬프 갱신"""
        _user_sessions[user_id] = time.time()

    def _build_welcome_response(self) -> dict:
        """
        웰컴카드 카카오 SkillResponse 생성

        구성:
        - BasicCard: 이미지 + 인사말 + 버튼 3개
          - 버튼1: 맞춤 가전 추천받기 (텍스트 발화 → message action)
          - 버튼2: LG전자 구독 혜택 확인 (URL 이동 → webLink action)
          - 버튼3: 상담사 연결(직접 문의) (상담톡 전환 → operator action)
        """
        settings = get_settings()

        welcome_card = {
            "title": " ",
            "description": settings.WELCOME_MESSAGE,
            "thumbnail": {
                "imageUrl": settings.WELCOME_IMAGE_URL,
                "fixedRatio": False,
            },
            "buttons": [
                {
                    "action": "message",
                    "label": settings.WELCOME_BTN1_LABEL,
                    "messageText": settings.WELCOME_BTN1_VALUE,
                },
                {
                    "action": "webLink",
                    "label": settings.WELCOME_BTN2_LABEL,
                    "webLinkUrl": settings.WELCOME_BTN2_URL,
                },
                {
                    "action": "operator",
                    "label": settings.WELCOME_BTN3_LABEL,
                },
            ],
        }

        return {
            "version": "2.0",
            "template": {
                "outputs": [
                    {"basicCard": welcome_card}
                ]
            }
        }

    # =========================================================================
    # 6. 메인 파이프라인
    # =========================================================================

    async def handle(self, raw_request: dict) -> dict:
        """
        카카오 스킬 요청 처리 메인 파이프라인 (분할 전송 + 웰컴카드 적용)

        처리 흐름:
        0. 첫 메시지 판별 → 웰컴카드 응답 + 백그라운드 AI 답변 전송
        1. 요청 파싱 + 타이머 시작
        2. Coze API 호출 (timeout_seconds 타임아웃)
        3-A. 타임아웃 내 응답 완료 -> 즉시 SkillResponse 반환
        3-B. 타임아웃 초과 + callbackUrl 있음 -> 안내 메시지(useCallback) + 백그라운드 콜백
        3-C. 타임아웃 초과 + callbackUrl 없음 -> 에러 메시지 반환
        """
        settings = get_settings()
        request_start = time.monotonic()

        # --- Step 0: 요청 파싱 ---
        parsed = await self.parse_request(raw_request)

        if not parsed["message"].strip():
            return self._text_response("메시지를 입력해주세요")

        # --- Step 1: 첫 메시지 판별 → 웰컴카드 + 백그라운드 AI 답변 ---
        user_id = parsed["user_id"]
        is_first = self._is_first_message(user_id)
        self._update_session(user_id)

        if is_first:
            logger.info(f"카카오 첫 메시지 감지 → 웰컴카드 발송 mode={settings.WELCOME_MODE} user={user_id}")

            # ===== 모드 A: 웰컴카드만 즉시 반환 (AI 답변은 두번째 메시지부터) =====
            if settings.WELCOME_MODE.upper() == "A":
                return self._build_welcome_response()

            # ===== 모드 B: 웰컴카드 즉시 + AI 답변 콜백으로 이어서 전송 =====
            if parsed["callback_url"]:
                # callbackUrl 있음 → 안내 메시지(useCallback) + 백그라운드 AI 답변 콜백
                asyncio.create_task(
                    self._welcome_then_ai_callback(parsed)
                )

                # useCallback 응답: data.text에 웰컴 메시지 표시 → 콜백으로 AI 답변 교체
                return {
                    "version": "2.0",
                    "useCallback": True,
                    "data": {
                        "text": settings.WELCOME_MESSAGE
                    }
                }
            else:
                # callbackUrl 없음 → 모드 B여도 웰컴카드만 반환 (push 불가)
                logger.warning("카카오 첫 메시지 mode=B이나 callbackUrl 없음 → 웰컴카드만 반환")
                return self._build_welcome_response()

        # --- Step 2: 일반 메시지 → Coze API 호출 ---
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
    # 웰컴카드 + AI 답변 백그라운드 전송
    # =========================================================================

    async def _welcome_then_ai_callback(self, parsed: dict) -> None:
        """
        웰컴카드 발송 후 Coze AI 답변을 콜백으로 전송하는 백그라운드 태스크

        흐름:
        1. Coze API 호출 (타임아웃 적용 → 초과 시 폴링 계속)
        2. 응답 포맷팅
        3. callbackUrl로 AI 답변 전송
        """
        settings = get_settings()
        callback_url = parsed["callback_url"]

        try:
            logger.info(f"카카오 웰컴 백그라운드 AI 호출 시작 user={parsed['user_id']}")

            # Coze 호출 (일반 타임아웃 적용)
            coze_result = await self._coze.chat(
                user_id=parsed["user_id"],
                message=parsed["message"],
            )

            # 타임아웃된 경우 폴링으로 대기
            if coze_result["timed_out"] and coze_result["chat_id"]:
                coze_result = await self._coze.poll_and_get_result(
                    chat_id=coze_result["chat_id"],
                    conversation_id=coze_result["conversation_id"],
                    max_wait=55.0,
                    poll_interval=1.0,
                )

            if coze_result["success"]:
                response_body = await self.format_response(coze_result, parsed)
            else:
                response_body = self._error_response(settings.ERROR_MESSAGE)

            # 콜백 응답에서 quickReplies 제거 (카카오 콜백은 quickReplies 미지원 → 400 에러 방지)
            if "template" in response_body and "quickReplies" in response_body.get("template", {}):
                del response_body["template"]["quickReplies"]
                logger.info("카카오 웰컴 콜백 응답에서 quickReplies 제거")

            # 콜백 전송 전 최소 대기
            await asyncio.sleep(1.0)

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    callback_url,
                    json=response_body,
                    headers={"Content-Type": "application/json"},
                )

            logger.info(
                f"카카오 웰컴 백그라운드 콜백 전송 완료 "
                f"status={resp.status_code} "
                f"user={parsed['user_id']}"
            )

        except Exception as e:
            logger.error(
                f"카카오 웰컴 백그라운드 예외: "
                f"{type(e).__name__}: {str(e)} "
                f"user={parsed['user_id']}"
            )

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

            # 콜백 응답에서 quickReplies 제거 (카카오 콜백은 quickReplies 미지원 → 400 에러 방지)
            if "template" in response_body and "quickReplies" in response_body.get("template", {}):
                del response_body["template"]["quickReplies"]
                logger.info("카카오 콜백 응답에서 quickReplies 제거")

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
