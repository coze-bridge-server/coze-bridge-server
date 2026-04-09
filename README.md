# Coze Bridge Server

카카오톡 + 네이버톡톡 멀티채널 Coze AI 챗봇 브릿지 서버

## 개요

Coze 워크플로를 카카오톡/네이버톡톡에서 사용할 수 있도록 연결하는 브릿지 서버입니다.
카카오 오픈빌더 스킬 서버 + 네이버톡톡 웹훅 서버를 하나의 FastAPI 앱으로 통합 운영합니다.

## 주요 기능

- **카카오톡 연동** — 오픈빌더 스킬 서버 (텍스트 + BasicCard 캐러셀)
- **네이버톡톡 연동** — 웹훅 수신 + 보내기 API (텍스트 + compositeContent 캐러셀)
- **카드형 말풍선** — 상품 이미지 + 가격 + 버튼 자동 변환
- **5초 콜백 처리** — 카카오 AI 챗봇 콜백 + 톡톡 비동기 보내기
- **멀티 고객사** — `clients.json`으로 고객사별 봇 설정 분리
- **Docker 배포** — docker-compose + Caddy HTTPS 자동 인증서
- **설정 핫 리로드** — 서버 재시작 없이 고객사 설정 변경

## 기술 스택

- Python 3.11 / FastAPI / Uvicorn
- httpx (비동기 HTTP)
- Docker / Docker Compose
- Caddy (HTTPS 리버스 프록시, Let's Encrypt 자동)
- Coze API v3 (Non-streaming)

## 프로젝트 구조

```
coze-bridge-server/
├── app/
│   ├── main.py              # FastAPI 앱 + 라우팅
│   ├── config/
│   │   ├── settings.py      # 환경변수 관리 (Pydantic Settings)
│   │   ├── client_config.py # 멀티 고객사 설정 매니저
│   │   └── logging.py       # 민감정보 마스킹 로거
│   ├── handlers/
│   │   ├── base.py          # 채널 핸들러 추상 클래스
│   │   ├── kakao.py         # 카카오 오픈빌더 핸들러
│   │   └── navertalk.py     # 네이버톡톡 웹훅 핸들러
│   ├── modules/
│   │   └── coze_client.py   # Coze API v3 비동기 클라이언트
│   └── cards/
│       ├── utils.py         # 공통 유틸 (가격 파싱, 포맷팅)
│       ├── kakao_card.py    # 카카오 BasicCard/Carousel 빌더
│       └── navertalk_card.py# 톡톡 compositeContent 빌더
├── scripts/
│   └── duckdns-update.sh    # DuckDNS IP 자동 갱신 스크립트
├── docker-compose.yml       # Docker Compose (app + Caddy)
├── Dockerfile               # Python 앱 이미지
├── Caddyfile                # HTTPS 리버스 프록시 설정
├── .env.example             # 환경변수 템플릿
├── clients.json.example     # 멀티 고객사 설정 템플릿
├── requirements.txt         # Python 의존성
├── OPERATION_GUIDE.md       # 운영 가이드
├── test_coze.py             # Coze API 연동 테스트
└── test_card_link.py        # 카드 빌드 단위 테스트
```

## 빠른 시작

### 1. 환경변수 설정

```bash
cp .env.example .env
cp clients.json.example clients.json
# .env와 clients.json에 실제 값 입력
```

### 2. Docker Compose로 실행

```bash
docker compose up -d
```

### 3. 헬스체크

```bash
curl https://your-domain.duckdns.org/health
```

## 엔드포인트

| 경로 | 용도 |
|------|------|
| `POST /skill/kakao` | 카카오 오픈빌더 스킬 (기본 고객사) |
| `POST /skill/kakao/{client_key}` | 카카오 스킬 (특정 고객사) |
| `POST /skill/navertalk` | 네이버톡톡 웹훅 (기본 고객사) |
| `POST /skill/navertalk/{client_key}` | 톡톡 웹훅 (특정 고객사) |
| `GET /health` | 헬스체크 |
| `POST /admin/reload-config` | 설정 핫 리로드 |
| `GET /admin/clients` | 고객사 목록 (마스킹) |

## 카드형 응답

Coze 봇이 상품 JSON을 반환하면 자동으로 카드형 말풍선으로 변환됩니다.

### Coze 봇 응답 형식

```json
[
  {
    "product_name": "정수기 A모델",
    "image_url": "https://example.com/img.jpg",
    "button_url": "https://example.com/product/1",
    "price": 39900,
    "discount_price": 35900
  }
]
```

또는 텍스트 안에 `[CARDS]...[/CARDS]` 태그로 감싸서 보낼 수도 있습니다.

## 운영

자세한 운영 방법은 [OPERATION_GUIDE.md](OPERATION_GUIDE.md)를 참고하세요.

## 라이선스

Private — 소스코드 무단 배포 금지
