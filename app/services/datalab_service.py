"""
네이버 DataLab 통합검색어 트렌드 서비스.
대시보드 '오늘의 기회 신호' 위젯에 쓰일 검색 추세를 계산한다.

- 후보 키워드들을 한 번에 질의(최대 5개)하고, 최근 구간 상승폭이 가장 큰 키워드를 고른다.
- DataLab의 ratio는 절대 검색량이 아니라 기간 내 상대값(0~100)이므로,
  '증가/감소'는 최근 7일 평균 vs 직전 7일 평균으로 판단한다.
"""
import os
from datetime import date, timedelta
from typing import Any, Dict, List

import requests
from dotenv import load_dotenv

from app.utils.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

DATALAB_URL = "https://openapi.naver.com/v1/datalab/search"

# 검색 트렌드로서 의미가 거의 없는 추상·마케팅 단어 (후보에서 제외)
EXCLUDE_KEYWORDS = {
    "재방문", "가성비", "친절", "회식", "직장인점심", "단골", "분위기", "서비스",
    "추천", "인기", "매장", "가게", "사장님", "방문", "후기", "리뷰", "데이트",
    "모임", "퇴근후", "친구모임", "로컬", "동네맛집",
    "food", "cafe", "dessert", "western", "bar",
}

# 업종별 실제로 검색되는 메뉴/음식 키워드 (정제된 후보 부족분을 채운다)
CATEGORY_FOOD_KEYWORDS = {
    "한식": ["국밥", "칼국수", "수제비", "해장국", "비빔밥"],
    "일식": ["라멘", "스시", "돈카츠", "우동"],
    "중식": ["마라탕", "탕수육", "짜장면", "양꼬치"],
    "양식": ["파스타", "스테이크", "브런치", "피자"],
    "카페": ["디저트", "베이글", "케이크", "브런치"],
    "카페/디저트": ["디저트", "베이글", "케이크", "브런치"],
    "주점": ["하이볼", "이자카야", "포차", "안주"],
}
# 업종 정보가 없을 때 쓰는 일반 외식 트렌드 키워드
GENERAL_FOOD_TRENDS = ["맛집", "마라탕", "탕후루", "디저트", "브런치"]


class DataLabService:
    def __init__(self) -> None:
        self.client_id = os.getenv("NAVER_CLIENT_ID")
        self.client_secret = os.getenv("NAVER_CLIENT_SECRET")
        # 요청마다 새 커넥션을 맺지 않도록 세션을 재사용한다.
        self._session = requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def _headers(self) -> Dict[str, str]:
        return {
            "X-Naver-Client-Id": self.client_id or "",
            "X-Naver-Client-Secret": self.client_secret or "",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _clean_keywords(keywords: List[str]) -> List[str]:
        seen: set[str] = set()
        cleaned: List[str] = []
        for raw in keywords or []:
            token = str(raw or "").replace("#", "").strip()
            # DataLab 키워드는 너무 길거나 공백이 많으면 검색량이 거의 없다.
            if not token or len(token) > 20:
                continue
            key = token.lower()
            if key in seen:
                continue
            # 추상·마케팅 단어는 검색 트렌드로서 의미가 없어 제외한다.
            if token in EXCLUDE_KEYWORDS or key in EXCLUDE_KEYWORDS:
                continue
            seen.add(key)
            cleaned.append(token)
        return cleaned

    def fetch_search_signal(self, candidates: List[str], category: str | None = None) -> Dict[str, Any]:
        if not self.enabled:
            raise RuntimeError("Naver DataLab credentials are not configured.")

        # 1) 후보에서 추상어를 걷어내고, 2) 업종별 음식 키워드를 우선 포함해
        #    실제로 검색되는 메뉴/음식 위주로 최대 5개를 질의한다.
        food_keywords = CATEGORY_FOOD_KEYWORDS.get((category or "").strip(), GENERAL_FOOD_TRENDS)
        cleaned = self._clean_keywords(list(food_keywords) + list(candidates or []))[:5]
        if not cleaned:
            cleaned = self._clean_keywords(GENERAL_FOOD_TRENDS)[:5]

        end = date.today()
        start = end - timedelta(days=30)
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "timeUnit": "date",
            "keywordGroups": [{"groupName": kw, "keywords": [kw]} for kw in cleaned],
        }

        response = self._session.post(DATALAB_URL, headers=self._headers(), json=body, timeout=15)
        response.raise_for_status()
        results = response.json().get("results", [])

        best = None
        for result in results:
            series = result.get("data", [])
            if len(series) < 8:
                continue
            ratios = [float(point.get("ratio", 0) or 0) for point in series]
            recent = ratios[-7:]
            prev = ratios[-14:-7] or ratios[:-7]
            recent_avg = sum(recent) / len(recent) if recent else 0.0
            prev_avg = sum(prev) / len(prev) if prev else 0.0

            if prev_avg <= 0:
                growth = recent_avg  # 0에서 상승 → 절대값으로 취급
            else:
                growth = (recent_avg - prev_avg) / prev_avg * 100.0

            candidate = {
                "keyword": result.get("title"),
                "growth": growth,
                "recent_avg": recent_avg,
            }
            # 상승폭이 더 크거나, 동률이면 최근 검색 비중이 큰 키워드 우선
            if best is None or (candidate["growth"], candidate["recent_avg"]) > (best["growth"], best["recent_avg"]):
                best = candidate

        if best is None:
            raise RuntimeError("DataLab returned no usable trend data.")

        growth = best["growth"]
        if growth >= 30:
            signal, intensity = "검색 급증", "high"
        elif growth >= 8:
            signal, intensity = "검색 증가", "medium"
        elif growth >= -8:
            signal, intensity = "관심 유지", "low"
        else:
            signal, intensity = "검색 감소", "low"

        logger.info(
            "DataLab signal: keyword=%s growth=%.1f%% intensity=%s",
            best["keyword"], growth, intensity,
        )
        return {
            "keyword": best["keyword"],
            "signal": signal,
            "intensity": intensity,
            "source": "네이버 DataLab",
            "period": "최근 30일",
            "growthRate": round(growth, 1),
        }
