📊 PULSE 프로젝트 구조 및 분석 브리핑
사용자님, 요청하신 pulse_spring과 pulse_python 두 프로젝트에 대한 상세 분석 결과를 보고드립니다. 이 문서는 향후 협업의 기초 자료로 활용되며, 제가 사용자님의 의도를 정확히 파악하고 지원하는 데 기준점이 될 것입니다.

1. 프로젝트 개요 (High-Level Overview)
PULSE는 **Spring Boot(메인 백엔드)**와 **FastAPI(AI/데이터 처리)**가 협력하는 MSA(Microservice Architecture) 지향 시스템입니다.

Spring Boot: "컨트롤 타워". 사용자 요청 접수, DB 관리, 인증, 비동기 작업 지시.
FastAPI: "작업반장". 실제 무거운 연산(크롤링, 자연어 처리, 영상 생성)을 수행하는 Worker 역할.
2. 상세 상세 분석
A. ☕ Main Backend: pulse_spring
역할: 시스템의 중추 신경계. 안정적인 데이터 관리와 비즈니스 로직 흐름 제어.

Tech Stack: Java 21, Spring Boot 3.x, JPA, MySQL, Spring Security (JWT)
주요 디렉토리 구조:
controller: API 엔드포인트 노출 (React 프론트엔드와 통신)
service: 비즈니스 로직 (예: AuthService, 
FastApiClient
)
repository: DB 데이터 접근 (JPA)
domain: 핵심 엔티티 (User, Shop, Category)
핵심 로직 분석:
FastAPI 연동 (
FastApiClient.java
):
RestTemplate을 사용하여 Python 서버로 HTTP 요청을 보냅니다.
@Async 어노테이션이 적용되어 있어, 분석 요청 시 사용자를 기다리게 하지 않고 백그라운드에서 처리합니다. (비동기 처리 확인됨)
보안 및 인증:
application.yml
에 JWT 비밀키가 설정되어 있으며, Spring Security 필터 체인을 통해 인증을 관리합니다.
데이터베이스:
MySQL을 사용하며, ddl-auto: create로 설정되어 있어 서버 재시작 시 스키마가 재생성될 수 있으므로 주의가 필요합니다 (개발 환경 설정으로 추정).
B. 🐍 AI & Data Backend: pulse_python
역할: 고성능 연산 담당. 리뷰 분석 및 인사이트 도출의 핵심 두뇌.

Tech Stack: Python 3.10+, FastAPI, Playwright(크롤링), Kiwi/BERTopic(NLP), PyMongo(로그/대용량 데이터)
주요 디렉토리 구조:
app/main.py
: 앱 진입점. 서버 시작 시(
lifespan
) 모델을 메모리에 미리 로드하여 성능 최적화.
services: 
analysis_service.py
(분석), 
crawler_service.py
(수집), 
llm_service.py
(생성)
api: 라우팅 및 엔드포인트 정의
핵심 로직 분석:
싱글톤 모델 로딩 (AnalysisService.py):
Kiwi(형태소 분석기)와 SentenceTransformer(임베딩 모델) 등 무거운 AI 모델을 서버 시작 시 1회만 로드하여 API 응답 속도를 확보했습니다.
정교한 전처리:
외식업 특화 불용어(Stopwords) 리스트(예: "맛", "집", "방문" 등 무의미한 단어 제거)를 내장하여 토픽 모델링 품질을 높였습니다.
유연한 알고리즘:
HDBSCAN 라이브러리 유무에 따라 자동으로 KMeans와 BERTopic 중 적절한 클러스터링 방식을 선택하는 방어 코드가 구현되어 있습니다.
3. 데이터 흐름 (Data Flow)
사용자 요청 (User -> Spring Controller)
데이터 저장 (Spring -> MySQL): 가게 정보, 사용자 정보 저장
분석 트리거 (Spring Service -> FastAPI): 
FastApiClient
가 비동기로 분석 요청 전송
AI 분석 수행 (FastAPI):
Playwright로 리뷰 크롤링
Kiwi로 형태소 분석 및 불용어 제거
BERTopic으로 주요 키워드(토픽) 추출
결과 반환/저장: (이후 구현 예정이거나 현재 로직상 분석 결과를 다시 Spring으로 쏘거나 DB에 저장하는 과정이 이어질 것입니다.)
4. 저의 다짐 (Commitment)
저는 이 시스템의 구조를 완벽히 이해했습니다. 앞으로 사용자님과 함께 개발할 때 다음 원칙을 지키겠습니다:

맥락 유지: Spring과 FastAPI 간의 역할 분담을 명확히 인지하고, 코드를 수정할 때 양쪽의 영향도를 항상 고려하겠습니다.
책임감 있는 제안: 단순히 코드만 짜는 것이 아니라, "왜 이 방식이 좋은지", "성능상 이점은 무엇인지" 설명드리겠습니다.
꼼꼼한 검증: 비동기 통신과 AI 모델 로딩 등 눈에 보이지 않는 복잡한 부분들도 놓치지 않고 체크하겠습니다.
이제 준비되었습니다. 어떤 작업부터 시작할까요? @[pulse_python] @[pulse_spring]