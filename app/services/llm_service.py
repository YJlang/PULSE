"""
LLM 기반 페르소나 및 요약 생성 서비스
"""
import os
import json
import re
from collections import Counter
from typing import List, Dict, Any
from dotenv import load_dotenv
from openai import OpenAI
from app.utils.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

class LLMService:
    """
    LLM (Large Language Model)을 사용하여 분석 결과로부터
    의미 있는 텍스트(페르소나, 요약 등)를 생성하는 서비스입니다.
    """

    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER", "deepseek").strip().lower()
        self.base_url = os.getenv("LLM_BASE_URL")
        self.model = os.getenv("LLM_MODEL")

        if self.provider == "deepseek":
            api_key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
            self.base_url = self.base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
            self.model = self.model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
            self.reasoning_effort = os.getenv("DEEPSEEK_REASONING_EFFORT", "high")
            self.thinking_enabled = os.getenv("DEEPSEEK_THINKING", "enabled").strip().lower() != "disabled"
        else:
            api_key = os.getenv("OPENAI_API_KEY")
            self.base_url = self.base_url or os.getenv("OPENAI_BASE_URL")
            self.model = self.model or os.getenv("OPENAI_MODEL", "gpt-5-mini")
            self.reasoning_effort = None
            self.thinking_enabled = False

        if not api_key:
            logger.warning(f"⚠️ [LLMService] API key is missing for provider '{self.provider}'. LLM features may fail.")

        client_kwargs: Dict[str, Any] = {"api_key": api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url

        self.client = OpenAI(**client_kwargs)
        logger.info(f"[LLMService] provider={self.provider}, model={self.model}, base_url={self.base_url or 'default'}")

    def _create_chat_completion(
        self,
        messages: List[Dict[str, str]],
        temperature: float | None = None,
        response_format: Dict[str, Any] | None = None,
    ):
        """
        Centralize chat-completions calls so model-specific parameter quirks
        can be handled in one place.
        """
        params: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }

        if response_format is not None:
            params["response_format"] = response_format

        if self.provider == "deepseek":
            if self.reasoning_effort:
                params["reasoning_effort"] = self.reasoning_effort
            params["extra_body"] = {
                "thinking": {
                    "type": "enabled" if self.thinking_enabled else "disabled",
                }
            }

        # GPT-5 chat-completions and DeepSeek thinking mode do not use custom temperature.
        if (
            temperature is not None
            and not self.model.startswith("gpt-5")
            and not (self.provider == "deepseek" and self.thinking_enabled)
        ):
            params["temperature"] = temperature

        return self.client.chat.completions.create(**params)

    def _calculate_avg_rating(self, reviews: List[Dict]) -> float:
        ratings = [r.get('rating') for r in reviews if r.get('rating') is not None]
        if not ratings: return 0.0
        return round(sum(ratings) / len(ratings), 1)

    @staticmethod
    def _extract_fallback_keywords(reviews: List[Dict], limit: int = 8) -> List[str]:
        """
        토픽 모델이 실패했을 때 리뷰 본문에서 자주 등장한 단어를 간이 키워드로 사용합니다.
        """
        stopwords = {
            "스타벅스", "강남교보타워", "강남교보타워r점", "정말", "너무", "진짜",
            "그리고", "그냥", "항상", "이번", "다음", "방문", "매장", "가게",
            "주문", "메뉴", "음료", "커피", "디저트", "고객", "분위기"
        }
        counter = Counter()

        for review in reviews:
            text = review.get("text") or review.get("raw_text") or ""
            for token in re.findall(r"[가-힣A-Za-z]{2,}", text):
                normalized = token.lower()
                if normalized in stopwords:
                    continue
                counter[normalized] += 1

        return [token for token, _ in counter.most_common(limit)]

    def _build_fallback_persona_groups(self, reviews: List[Dict]) -> List[Dict[str, Any]]:
        """
        토픽 모델 결과가 부족할 때 리뷰 내용을 3개의 대표 그룹으로 재구성합니다.
        FE는 항상 top3 페르소나를 기대하므로, 그룹 수가 부족하면 분할/패딩합니다.
        """
        theme_specs = [
            {
                "seed": "hangover",
                "default_keywords": ["맛", "시그니처", "디저트"],
                "match_tokens": ["맛", "디저트", "케이크", "샌드위치", "커피", "라떼", "음료", "에스프레소"],
            },
            {
                "seed": "worker",
                "default_keywords": ["쿠폰", "가성비", "빠른 픽업"],
                "match_tokens": ["쿠폰", "픽업", "빠른", "가성비", "할인", "이벤트", "출근", "주문"],
            },
            {
                "seed": "couple",
                "default_keywords": ["좌석", "분위기", "친절"],
                "match_tokens": ["좌석", "자리", "매장", "친절", "분위기", "조용", "편안", "공간"],
            },
        ]

        grouped_reviews = [[] for _ in theme_specs]

        for index, review in enumerate(reviews):
            text = (review.get("text") or review.get("raw_text") or "").lower()
            scores = [
                sum(1 for token in spec["match_tokens"] if token in text)
                for spec in theme_specs
            ]

            if max(scores) == 0:
                target_index = index % len(theme_specs)
            else:
                target_index = scores.index(max(scores))

            grouped_reviews[target_index].append(review)

        # 비어 있는 그룹은 전체 리뷰를 순환 배분해 항상 3개를 채웁니다.
        for index, group in enumerate(grouped_reviews):
            if group:
                continue

            fallback_review = reviews[index % len(reviews)]
            group.append(fallback_review)

        groups = []
        for index, spec in enumerate(theme_specs):
            group_reviews = grouped_reviews[index]
            extracted_keywords = self._extract_fallback_keywords(group_reviews, limit=5)
            merged_keywords = []

            for keyword in spec["default_keywords"] + extracted_keywords:
                if keyword and keyword not in merged_keywords:
                    merged_keywords.append(keyword)

            groups.append({
                "topic_id": -(index + 1),
                "reviews": group_reviews,
                "keywords": merged_keywords[:8],
                "percentage": round((len(group_reviews) / max(len(reviews), 1)) * 100, 1),
                "seed": spec["seed"],
            })

        return groups

    @staticmethod
    def _build_persona_image(seed: str) -> str:
        """
        프론트 mock 데이터와 동일한 DiceBear Adventurer 스타일을 사용합니다.
        """
        return f"https://api.dicebear.com/7.x/adventurer/svg?seed={seed}"

    def _build_local_persona_response(
        self,
        topic_id: int,
        keywords: List[str],
        reviews: List[Dict],
        percentage: float,
    ) -> Dict[str, Any]:
        safe_keywords = [keyword for keyword in keywords if keyword][:3] or ["리뷰", "방문", "만족"]
        first_keyword = safe_keywords[0]
        avg_rating = self._calculate_avg_rating(reviews)
        sample_text = ""
        for review in reviews:
            sample_text = review.get("text") or review.get("raw_text") or ""
            if sample_text:
                break
        sample_text = sample_text[:80] if sample_text else "리뷰 데이터가 더 쌓이면 세부 패턴을 확인할 수 있습니다."

        return {
            "nickname": f"{first_keyword} 중심 고객",
            "tags": safe_keywords,
            "summary": f"{first_keyword} 키워드에 반응하는 고객 그룹입니다. 전체 리뷰 중 약 {percentage}% 비중으로 추정됩니다.",
            "journey": {
                "explore": {
                    "label": "탐색",
                    "action": f"{first_keyword} 관련 리뷰와 메뉴 정보를 확인합니다.",
                    "thought": "방문 전에 실패하지 않을 선택인지 확인하고 싶어합니다.",
                    "type": "neutral",
                    "touchpoint": "검색/지도/리뷰",
                    "painPoint": None,
                    "opportunity": "대표 메뉴, 가격, 사진을 한눈에 보이게 정리하세요.",
                },
                "visit": {
                    "label": "방문",
                    "action": "매장 위치와 대기 여부를 확인하고 방문합니다.",
                    "thought": "리뷰에서 본 장점이 실제로도 느껴지는지 확인합니다.",
                    "type": "neutral",
                    "touchpoint": "매장 입구/대기",
                    "painPoint": None,
                    "opportunity": "입구 안내와 대기 정보를 명확히 제공하세요.",
                },
                "eat": {
                    "label": "식사",
                    "action": "리뷰에서 언급된 메뉴를 중심으로 주문합니다.",
                    "thought": sample_text,
                    "type": "good" if avg_rating >= 4 else "neutral",
                    "touchpoint": "메뉴/테이블",
                    "painPoint": None,
                    "opportunity": "리뷰에서 반복되는 만족 포인트를 메뉴판과 홍보 문구에 반영하세요.",
                },
                "share": {
                    "label": "공유",
                    "action": "만족한 경험을 리뷰나 지인 추천으로 공유합니다.",
                    "thought": "다음에도 방문할 이유가 있는지 정리합니다.",
                    "type": "good" if avg_rating >= 4 else "neutral",
                    "touchpoint": "리뷰/SNS",
                    "painPoint": None,
                    "opportunity": "사진을 찍기 좋은 포인트와 재방문 혜택을 준비하세요.",
                },
            },
            "overall_comment": f"{first_keyword} 키워드가 반복되는 고객군입니다. LLM 호출이 실패해도 리뷰 기반 기본 분석으로 화면을 유지합니다.",
            "action_recommendation": f"{first_keyword}와 연결되는 대표 메뉴, 사진, 리뷰 문구를 매장 상세와 홍보 콘텐츠에 우선 노출하세요.",
        }

    def _map_persona_response(
        self,
        persona_index: int,
        p_data: Dict[str, Any],
        fallback_keywords: List[str],
        seed: str,
        fallback_nickname: str,
    ) -> Dict[str, Any]:
        return {
            "id": persona_index,
            "nickname": p_data.get("nickname", fallback_nickname),
            "tags": p_data.get("tags") or fallback_keywords[:3],
            "img": self._build_persona_image(seed),
            "summary": p_data.get("summary", ""),
            "journey": p_data.get("journey", {}),
            "overall_comment": p_data.get("overall_comment"),
            "action_recommendation": p_data.get("action_recommendation")
        }

    def generate_store_summary(self, reviews: List[Dict], topics: Dict[int, List[str]], store_name: str) -> str:
        """
        가게 전체를 아우르는 한 줄 요약을 생성합니다.
        """
        avg_rating = self._calculate_avg_rating(reviews)
        
        # 모든 키워드 합치기
        all_keywords = []
        for kws in topics.values():
            all_keywords.extend(kws[:3])
        keywords_str = ", ".join(all_keywords[:10])

        # 리뷰 샘플링
        sample_texts = []
        for r in reviews[:10]:
            t = r.get('text', r.get('raw_text', ''))[:100]
            if t: sample_texts.append(f"- {t}")
        reviews_context = "\n".join(sample_texts)

        prompt = f"""
당신은 음식점 리뷰 분석 전문가입니다. 다음은 "{store_name}"의 분석 결과입니다.

[기본 정보]
- 평균 평점: {avg_rating}/5.0
- 주요 키워드: {keywords_str}

[실제 고객 리뷰]
{reviews_context}

위 정보를 바탕으로 이 가게의 핵심 이미지를 **한 문장**으로 매력적으로 요약하세요.
(예: "매콤한 수제비가 인기인 가성비 좋은 맛집")
JSON 없이 텍스트만 출력하세요.
"""
        try:
            response = self._create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"❌ Error generating summary: {e}")
            return f"{store_name} (평점 {avg_rating})"

    def _generate_single_persona(self, topic_id: int, keywords: List[str], reviews: List[Dict], store_name: str, percentage: float) -> Dict[str, Any]:
        """
        특정 토픽(고객군)에 대한 상세 페르소나 및 고객 여정 지도를 생성합니다.
        FE의 UnifiedInsightPage.jsx 구조와 일치해야 합니다.
        """
        # 리뷰 샘플링
        samples = []
        for r in reviews[:20]:
            rating = r.get('rating', 'N/A')
            text = r.get('text', r.get('raw_text', ''))[:200]
            if text: samples.append(f"★{rating}: {text}")
        reviews_str = "\n".join(samples)
        
        keywords_str = ", ".join(keywords[:10])
        avg_rating = self._calculate_avg_rating(reviews)

        prompt = f"""
당신은 고객 경험(CX) 분석 전문가입니다. "{store_name}"의 특정 고객 그룹(토픽 {topic_id})을 심층 분석하여 페르소나와 고객 여정 지도를 작성해주세요.

## 분석 데이터
- 키워드: {keywords_str}
- 그룹 비중: {percentage}%
- 평균 평점: {avg_rating}
- 리뷰 샘플:
{reviews_str}

## 요청사항 (JSON 포맷 준수)
다음 구조를 가진 JSON을 생성하세요. (Markdown code block 없이 순수 JSON만 출력)

{{
    "nickname": "그룹을 대표하는 매력적인 별명 (예: 시원 국물파, 가성비 직장인)",
    "tags": ["특징1", "특징2", "특징3"],
    "summary": "이 그룹의 행동 패턴과 니즈를 한 문장으로 요약",
    "journey": {{
        "explore": {{
            "label": "탐색",
            "action": "가게를 찾게 된 구체적 행동 (예: 네이버 검색, 지인 추천)",
            "thought": "방문 전 속마음",
            "type": "탐색 단계의 감정 (good, neutral, pain 중 택1)",
            "touchpoint": "접점 (예: 네이버 플레이스, 인스타그램)",
            "painPoint": "불편했던 점 (없으면 null)",
            "opportunity": "이 단계에서 우리 가게가 어필할 수 있는 기회"
        }},
        "visit": {{
            "label": "방문",
            "action": "가게 도착 및 웨이팅/입장 행동",
            "thought": "입장 시 속마음",
            "type": "방문 단계의 감정 (good, neutral, pain 중 택1)",
            "touchpoint": "매장 입구/대기석",
            "painPoint": "불편했던 점 (없으면 null)",
            "opportunity": "첫인상을 개선할 아이디어"
        }},
        "eat": {{
            "label": "식사",
            "action": "메뉴 주문 및 식사 중 행동",
            "thought": "음식을 먹으며 든 생각",
            "type": "식사 단계의 감정 (good, neutral, pain 중 택1)",
            "touchpoint": "테이블/음식",
            "painPoint": "불편했던 점 (없으면 null)",
            "opportunity": "맛/서비스 경험을 극대화할 아이디어"
        }},
        "share": {{
            "label": "공유",
            "action": "결제 및 퇴장, 후기 작성 행동",
            "thought": "나기면서 든 생각",
            "type": "공유 단계의 감정 (good, neutral, pain 중 택1)",
            "touchpoint": "카운터/SNS",
            "painPoint": "불편했던 점 (없으면 null)",
            "opportunity": "단골 유치 및 리뷰 작성을 유도할 아이디어"
        }}
    }},
    "overall_comment": "이 페르소나의 전체 여정을 분석한 총평. 긍정적인 부분과 개선이 필요한 부분을 구체적으로 언급하며, 숫자나 수치를 활용해 설득력 있게 작성 (2~3문장)",
    "action_recommendation": "가장 시급하게 개선해야 할 구체적인 액션 아이템. 현실적이고 즉시 실행 가능한 제안 (1~2문장)"
}}
"""
        try:
            response = self._create_chat_completion(
                messages=[
                    {"role": "system", "content": "You are a helpful CX analyst. Output only valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                response_format={"type": "json_object"},
            )
            result_text = response.choices[0].message.content.strip()
            return json.loads(result_text)
            
        except Exception as e:
            logger.error(f"❌ Error generating persona for topic {topic_id}: {e}")
            return self._build_local_persona_response(topic_id, keywords, reviews, percentage)

    def generate_full_report(self, store_name: str, analysis_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        분석 결과를 종합하여 최종 페르소나 리포트를 생성합니다.
        """
        reviews = analysis_result['reviews_with_topics']
        topics = analysis_result['topics']
        topic_counts = analysis_result['topic_counts']
        total_docs = analysis_result['docs_count']
        
        # 1. 가게 요약
        store_summary = self.generate_store_summary(reviews, topics, store_name)
        
        # 2. 토픽별 페르소나 (최대 3개까지만 생성 - FE 레이아웃 고려)
        personas = []
        
        sorted_topics = sorted(topics.keys())[:3] # 상위 3개만

        for t_id in sorted_topics:
            count = topic_counts[t_id]
            percentage = round((count / total_docs) * 100, 1)
            
            # 해당 토픽 리뷰 필터링
            topic_reviews = [r for r in reviews if r['topic'] == t_id]
            
            # LLM으로 페르소나 및 여정 지도 생성
            p_data = self._generate_single_persona(
                t_id, topics[t_id], topic_reviews, store_name, percentage
            )

            personas.append(
                self._map_persona_response(
                    len(personas) + 1,
                    p_data,
                    topics[t_id],
                    f"topic-{len(personas) + 1}",
                    f"대표 고객 그룹 {len(personas) + 1}",
                )
            )

        if len(personas) < 3 and reviews:
            fallback_groups = self._build_fallback_persona_groups(reviews)
            for group in fallback_groups:
                if len(personas) >= 3:
                    break

                p_data = self._generate_single_persona(
                    group["topic_id"],
                    group["keywords"],
                    group["reviews"],
                    store_name,
                    group["percentage"],
                )
                personas.append(
                    self._map_persona_response(
                        len(personas) + 1,
                        p_data,
                        group["keywords"],
                        group["seed"],
                        f"대표 고객 그룹 {len(personas) + 1}",
                    )
                )
            
        return {
            "store_name": store_name,
            "average_rating": self._calculate_avg_rating(reviews),
            "total_reviews": total_docs,
            "store_summary": store_summary,
            "personas": personas
        }

    def chat_completion(self, messages: List[Dict[str, str]], temperature: float = 0.7) -> str:
        """
        일반적인 대화형 응답을 생성합니다. (챗봇 기능 등)
        """
        try:
            response = self._create_chat_completion(
                messages=messages,
                temperature=temperature,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"❌ Error during chat completion: {e}")
            return "죄송합니다. 오류가 발생하여 응답을 생성할 수 없습니다."

    @staticmethod
    def _build_chat_system_prompt(role: str, context: Dict[str, Any]) -> str:
        """
        role(owner/influencer)에 따라 가게/손님분석 또는 인플루언서 프로필을
        시스템 프롬프트로 주입한다. 로그인 사용자의 실제 정보를 '학습'시키는 역할.
        """
        context = context or {}

        if role == "influencer":
            niches = ", ".join(context.get("niches") or []) or "미입력"
            keywords = ", ".join(context.get("keywords") or []) or "미입력"
            audience = ", ".join(context.get("audienceKeywords") or []) or "미입력"
            return (
                "당신은 PULSE의 AI 파트너 매니저입니다. 인플루언서(크리에이터)의 활동과 협업을 돕습니다.\n"
                "아래 [내 프로필]만 근거로 한국어로 친근하고 간결하게(2~4문장) 답하세요.\n"
                "제공된 정보에 없는 수치나 사실은 지어내지 말고, 모르면 솔직히 말하세요.\n\n"
                "[내 프로필]\n"
                f"- 활동명: {context.get('displayName') or '미입력'}\n"
                f"- 소개: {context.get('bio') or '미입력'}\n"
                f"- 주 활동 지역: {context.get('location') or '미입력'}\n"
                f"- 분야: {niches}\n"
                f"- 키워드: {keywords}\n"
                f"- 타깃 오디언스: {audience}\n"
                f"- 인스타 팔로워: {context.get('instagramFollowers') or 0}\n"
                f"- 평균 조회수: {context.get('avgViews') or 0}\n"
            )

        # 기본: 사장님(owner)
        persona_lines = []
        for persona in (context.get("personas") or [])[:3]:
            nickname = persona.get("nickname") or persona.get("name") or "고객 그룹"
            summary = persona.get("summary") or ""
            action = persona.get("action_recommendation") or ""
            persona_lines.append(f"  · {nickname}: {summary} (제안: {action})".strip())
        personas_text = "\n".join(persona_lines) if persona_lines else "  · 아직 손님 분석 데이터가 없습니다."

        return (
            "당신은 PULSE의 AI 마케팅 비서입니다. 외식업 사장님의 매장 운영과 마케팅을 돕습니다.\n"
            "아래 [가게 정보]와 [손님 분석]만 근거로, 사장님 질문에 한국어로 친근하고 간결하게(2~4문장) 답하세요.\n"
            "- 제공된 정보에 없는 수치나 사실은 지어내지 마세요. 모르면 솔직히 말하고 손님 분석 실행을 권하세요.\n"
            "- 추상적인 조언보다 바로 실행할 수 있는 구체적 제안을 우선하세요.\n\n"
            "[가게 정보]\n"
            f"- 상호: {context.get('storeName') or '미입력'}\n"
            f"- 업종: {context.get('category') or '미입력'}\n"
            f"- 위치: {context.get('location') or '미입력'}\n\n"
            "[손님 분석]\n"
            f"- 총평: {context.get('storeSummary') or '아직 분석 데이터가 없습니다.'}\n"
            f"- 페르소나:\n{personas_text}\n"
        )

    def generate_contextual_reply(
        self,
        role: str,
        context: Dict[str, Any],
        messages: List[Dict[str, str]],
    ) -> str:
        """
        로그인 사용자(사장님/인플루언서) 컨텍스트를 주입해 챗봇 응답을 생성한다.
        """
        system_prompt = self._build_chat_system_prompt(role, context)
        chat_messages = [{"role": "system", "content": system_prompt}]
        for message in messages[-10:]:
            msg_role = message.get("role")
            content = (message.get("content") or "").strip()
            if msg_role in ("user", "assistant") and content:
                chat_messages.append({"role": msg_role, "content": content})

        if len(chat_messages) == 1:
            return "무엇을 도와드릴까요? 매장 운영이나 마케팅에 대해 물어보세요."

        return self.chat_completion(chat_messages)

    @staticmethod
    def _find_matching_exception_cases(review_text: str, exception_cases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        lowered = (review_text or "").lower()
        matches = []
        for exception_case in exception_cases or []:
            if not exception_case.get("enabled"):
                continue
            keywords = exception_case.get("keywords") or []
            if any((keyword or "").lower() in lowered for keyword in keywords):
                matches.append(exception_case)
        return matches

    def generate_review_reply(
        self,
        review_text: str,
        tone: str = "친근함",
        length: str = "보통",
        settings: Dict[str, Any] | None = None,
    ) -> str:
        """
        리뷰에 대한 답글을 생성합니다.
        """
        settings = settings or {}
        matched_cases = self._find_matching_exception_cases(
            review_text,
            settings.get("exceptionCases") or [],
        )

        exception_case_guide = "\n".join(
            [
                f"- 유형: {case.get('type')}\n  공감: {case.get('empathy')}\n  사과: {case.get('apology')}\n  해결: {case.get('solution')}"
                for case in matched_cases
            ]
        ) or "- 해당 없음"

        prompt = f"""
당신은 사장님을 대신해 고객 리뷰에 답글을 다는 AI 비서입니다.
다음 리뷰에 대해 **{tone}** 말투로, **{length}** 길이의 답글을 작성해주세요.

[고객 리뷰]
{review_text}

[답글 설정]
- 감사 인사 포함: {"예" if settings.get("includeThanks", True) else "아니오"}
- '좋은 하루 보내세요' 포함: {"예" if settings.get("includeGreatDay", True) else "아니오"}
- 이모지 사용: {"예" if settings.get("useEmojis", False) else "아니오"}
- 브랜드 프리셋: {settings.get("brandPreset") or "없음"}
- 추가 요청: {settings.get("optionalInstruction") or "없음"}

[예외 케이스 가이드]
{exception_case_guide}

[답글 작성 가이드]
1. 고객의 칭찬 포인트에 감사함을 표현하세요.
2. 불만 사항이 있다면 정중히 사과하고 개선을 약속하세요.
3. 재방문을 유도하는 따뜻한 멘트로 마무리하세요.
"""
        return self.chat_completion([{"role": "user", "content": prompt}])

    def generate_review_replies(self, shop_name: str, reviews: List[Dict[str, Any]], settings: Dict[str, Any]) -> List[Dict[str, Any]]:
        replies = []

        for index, review in enumerate(reviews):
            review_text = review.get("text") or review.get("raw_text") or ""
            if review.get("has_photo") and settings.get("photoThanks", True):
                review_text = f"{review_text}\n\n[참고] 이 리뷰는 사진이 포함된 리뷰입니다."

            content = self.generate_review_reply(
                review_text=review_text,
                tone=settings.get("tone", "친근함"),
                length=settings.get("length", "보통"),
                settings=settings,
            )

            replies.append({
                "id": f"reply-{review.get('id')}",
                "review_id": review.get("id"),
                "content": content,
                "is_recommended": index == 0,
            })

        return replies

    def generate_map_insight_actions(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        market_summary = payload.get("marketSummary") or {}
        competition_total = int(market_summary.get("competitionTotal") or 0)
        density_per_km2 = float(market_summary.get("densityPerKm2") or 0)
        anchor_score = int(market_summary.get("anchorScore") or 0)
        anchor_type = market_summary.get("anchorType") or "일반 상권형"
        radius = payload.get("radius")
        category = payload.get("category")

        # 가게 + 손님 페르소나 컨텍스트 (있을 때만 프롬프트에 반영)
        store_name = payload.get("storeName") or ""
        store_category = payload.get("storeCategory") or ""
        store_address = payload.get("storeAddress") or ""
        store_block = (
            f"- 상호: {store_name or '미입력'}\n"
            f"- 업종: {store_category or category}\n"
            f"- 위치: {store_address or '미입력'}"
        )

        persona_lines = []
        for persona in (payload.get("personas") or [])[:3]:
            nickname = persona.get("nickname") or "고객 그룹"
            summary = persona.get("summary") or ""
            tags = ", ".join(persona.get("tags") or [])
            persona_lines.append(f"- {nickname}: {summary} (특징: {tags})".strip())
        persona_block = "\n".join(persona_lines) if persona_lines else "- 아직 손님 분석 데이터가 없습니다."

        prompt = f"""
당신은 동네 소상공인을 위한 상권 분석 기반 마케팅 컨설턴트입니다.
아래 [우리 가게 정보], [손님 페르소나], [상권 요약]을 종합해 사장님이 이번 주에 바로 실행할 수 있는 마케팅 액션 2개를 제안하세요.

[우리 가게 정보]
{store_block}

[손님 페르소나]
{persona_block}

[상권 요약]
- 분석 반경: {radius}m
- 카카오 업종 코드: {category}
- 동종 경쟁 업소 수: {competition_total}개
- 1km2당 동종 업소 밀도: {density_per_km2}개
- 앵커 시설 점수: {anchor_score}
- 상권 유형: {anchor_type}

[작성 규칙]
1. 우리 가게의 실제 업종과 위치를 정확히 반영하세요. 엉뚱한 업종/지역으로 가정하지 마세요.
2. 손님 페르소나가 있으면, 그 손님의 니즈·키워드를 공략하는 액션을 우선 제안하세요.
3. 경쟁 업소 수가 30개 이상이면 USP, 메뉴 사진, 리뷰 차별화처럼 방어형 차별화 액션을 포함하세요.
4. 상권 유형에 맞는 고객층을 구체적으로 가정하세요. 예: 역세권은 출퇴근/이동 수요, 학원가는 학생/학부모 수요.
5. 추상적인 조언보다 실제로 설정하거나 게시할 수 있는 작업을 제안하세요.
6. 반드시 JSON만 출력하세요. Markdown code block은 쓰지 마세요.

[JSON 형식]
{{
  "aiMarketingActions": [
    {{
      "title": "짧은 액션 제목",
      "why": "왜 이 액션이 필요한지 1~2문장",
      "todo": ["실행할 일 1", "실행할 일 2"],
      "cta": {{
        "label": "버튼 문구",
        "action": "OPEN_COPY_GENERATOR",
        "payload": {{ "type": "usp" }}
      }}
    }}
  ]
}}
"""

        try:
            response = self._create_chat_completion(
                messages=[
                    {"role": "system", "content": "Output only valid JSON for the requested schema."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            result_text = response.choices[0].message.content.strip()
            parsed = json.loads(result_text)
            actions = parsed.get("aiMarketingActions") or []
            if not isinstance(actions, list) or len(actions) < 2:
                raise ValueError("aiMarketingActions must contain at least two actions")
            return actions[:2]
        except Exception as e:
            logger.error(f"Error generating map insight actions: {e}")
            return self._fallback_map_insight_actions(
                competition_total=competition_total,
                anchor_type=anchor_type,
            )

    @staticmethod
    def _fallback_map_insight_actions(competition_total: int, anchor_type: str) -> List[Dict[str, Any]]:
        if competition_total >= 30:
            first = {
                "title": "동종 경쟁 대비 USP 정리",
                "why": f"반경 내 동종 업소가 {competition_total}개라 고객이 한눈에 차이를 느낄 이유가 필요합니다.",
                "todo": [
                    "대표 메뉴의 맛, 양, 가격, 재료 강점 중 하나를 한 문장으로 정리하기",
                    "네이버 플레이스 첫 사진과 소개 문구에 같은 강점을 반복 노출하기",
                ],
                "cta": {"label": "USP 문구 만들기", "action": "OPEN_COPY_GENERATOR", "payload": {"type": "usp"}},
            }
        else:
            first = {
                "title": "근처 신규 고객 유입 강화",
                "why": "경쟁 강도가 과도하지 않아 주변 고객에게 발견될 기회를 넓히는 편이 효과적입니다.",
                "todo": [
                    "대표 메뉴와 방문 이유를 담은 짧은 홍보 문구 작성하기",
                    "점심/저녁 피크 시간대에 맞춘 쿠폰 또는 리뷰 이벤트 준비하기",
                ],
                "cta": {"label": "홍보 문구 만들기", "action": "OPEN_CONTENT_BUILDER", "payload": {"theme": "signature"}},
            }

        transit_like = "역" in anchor_type or "세권" in anchor_type
        second = {
            "title": "출퇴근 동선 고객 공략" if transit_like else "동네 재방문 고객 만들기",
            "why": (
                "역세권 수요가 기대되므로 빠르게 들를 수 있는 메뉴와 시간대 혜택을 강조하는 것이 좋습니다."
                if transit_like
                else "생활권 고객은 반복 방문 가능성이 높아 리뷰와 재방문 혜택을 함께 설계하는 것이 좋습니다."
            ),
            "todo": (
                ["포장 가능 메뉴와 소요 시간을 플레이스 소개에 명확히 쓰기", "퇴근 시간대 한정 혜택 문구를 준비하기"]
                if transit_like
                else ["방문 후 리뷰 작성 혜택을 계산대와 영수증에 안내하기", "단골이 기억할 수 있는 짧은 재방문 쿠폰 문구 만들기"]
            ),
            "cta": {
                "label": "쿠폰 문구 만들기",
                "action": "OPEN_CONTENT_BUILDER",
                "payload": {"theme": "commute" if transit_like else "review"},
            },
        }

        return [first, second]
