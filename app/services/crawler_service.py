"""
Naver and Kakao review crawling service.
"""

import asyncio
import hashlib
import json
import re
import sys
import threading
from datetime import datetime
from typing import Dict, List
from urllib.parse import quote

import requests
from playwright.async_api import Page, async_playwright

from app.utils.logger import get_logger

logger = get_logger(__name__)


class CrawlerService:
    # 네이버 방문자 리뷰: 헤드리스 브라우저는 봇으로 탐지·차단되므로(동일 IP라도 1회차 차단),
    # 모바일 플레이스 내부 HTTP API(검색 + GraphQL)를 직접 호출한다. 카카오는 기존 브라우저 방식 유지.
    NAVER_MOBILE_UA = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
    )
    NAVER_PCMAP_LIST_URL = "https://pcmap.place.naver.com/restaurant/list"
    NAVER_GRAPHQL_URL = "https://api.place.naver.com/graphql"
    # getVisitorReviews 는 size 상한 50, page 파라미터 무시 → 한 가게당 최신 50개가 수집 한계.
    NAVER_REVIEW_MAX_PAGE_SIZE = 50

    def __init__(self) -> None:
        # 네이버 검색/리뷰 API 호출마다 TCP 커넥션을 새로 맺지 않도록 세션을 재사용한다.
        self._session = requests.Session()

    # UTF-8-safe normalizer: 정규식이 Windows 터미널에서 깨져 한글이
    # 잘려나가는 것을 막기 위해 명시적인 문자 클래스를 사용한다.
    @staticmethod
    def _normalize_search_text(text: str) -> str:
        cleaned = re.sub(r"\([^)]*\)", " ", text or "")
        cleaned = re.sub(r"[^0-9A-Za-z\uAC00-\uD7A3\s]", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    @classmethod
    def _build_search_query(
        cls,
        store_name: str,
        address: str,
        include_address: bool = True,
    ) -> str:
        normalized_store = cls._normalize_search_text(store_name)
        normalized_address = cls._normalize_search_text(address)
        if normalized_address in {"주소 미입력", "미입력"}:
            normalized_address = ""
        address_parts = normalized_address.split()
        concise_address = " ".join(address_parts[:4]) if address_parts else ""
        if include_address and normalized_store and concise_address:
            return f"{normalized_store} {concise_address}".strip()
        return normalized_store or concise_address

    @classmethod
    def _compact_search_text(cls, text: str) -> str:
        return re.sub(r"\s+", "", cls._normalize_search_text(text).lower())

    @classmethod
    def _tokenize_search_text(cls, text: str, limit: int | None = None) -> List[str]:
        tokens = [
            token
            for token in cls._compact_search_text(text).split()
            if token
        ]
        if not tokens:
            tokens = [
                token
                for token in (
                    cls._compact_search_text(chunk)
                    for chunk in cls._normalize_search_text(text).lower().split()
                )
                if len(token) >= 2
            ]
        return tokens[:limit] if limit is not None else tokens

    @classmethod
    def _build_match_context(cls, store_name: str, address: str) -> Dict[str, object]:
        store_tokens = [
            token
            for token in (
                cls._compact_search_text(chunk)
                for chunk in cls._normalize_search_text(store_name).lower().split()
            )
            if len(token) >= 2
        ]
        address_tokens = [
            token
            for token in (
                cls._compact_search_text(chunk)
                for chunk in cls._normalize_search_text(address).lower().split()
            )
            if len(token) >= 2
        ][:5]
        branch_tokens = store_tokens[1:] if len(store_tokens) > 1 else store_tokens
        return {
            "store_compact": cls._compact_search_text(store_name),
            "store_tokens": store_tokens,
            "branch_tokens": branch_tokens,
            "address_tokens": address_tokens,
        }

    @classmethod
    def _score_search_candidate(cls, candidate: Dict[str, str], store_name: str, address: str) -> int:
        match_context = cls._build_match_context(store_name, address)
        title_compact = cls._compact_search_text(candidate.get("title", ""))
        address_compact = cls._compact_search_text(candidate.get("address", ""))
        context_compact = cls._compact_search_text(candidate.get("context", ""))
        merged_compact = " ".join(
            value
            for value in (title_compact, address_compact, context_compact)
            if value
        )

        score = 0
        store_compact = match_context["store_compact"]
        if store_compact:
            if title_compact == store_compact:
                score += 160
            elif store_compact in title_compact:
                score += 120
            elif store_compact in merged_compact:
                score += 80

        brand_tokens = match_context["store_tokens"]
        if brand_tokens:
            brand_token = brand_tokens[0]
            if brand_token in title_compact:
                score += 12
            elif brand_token in merged_compact:
                score += 6

        for token in match_context["branch_tokens"]:
            if token in title_compact:
                score += 36
            elif token in merged_compact:
                score += 18

        address_hits = 0
        for token in match_context["address_tokens"]:
            if token in address_compact:
                score += 14
                address_hits += 1
            elif token in merged_compact:
                score += 8
                address_hits += 1

        if address_hits >= 2:
            score += 24
        if address_hits >= 3:
            score += 18

        return score

    @classmethod
    def _select_best_candidate(
        cls,
        candidates: List[Dict[str, str]],
        store_name: str,
        address: str,
    ) -> Dict[str, str] | None:
        scored_candidates = []
        for candidate in candidates:
            scored_candidate = dict(candidate)
            scored_candidate["match_score"] = cls._score_search_candidate(
                candidate,
                store_name,
                address,
            )
            scored_candidates.append(scored_candidate)

        if not scored_candidates:
            return None

        scored_candidates.sort(key=lambda candidate: candidate.get("match_score", 0), reverse=True)
        return scored_candidates[0]

    @staticmethod
    def _clean_review_text(text: str) -> str:
        cleaned = (text or "").replace("더보기", " ").replace("펼쳐보기", " ")
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    @staticmethod
    def _normalize_review_date(date_text: str | None) -> str | None:
        if not date_text:
            return None

        compact = re.sub(r"\s+", "", date_text)

        korean_match = re.search(r"(\d{4})년(\d{1,2})월(\d{1,2})일", compact)
        if korean_match:
            year, month, day = korean_match.groups()
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

        full_match = re.search(r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})", compact)
        if full_match:
            year, month, day = full_match.groups()
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"

        short_match = re.search(r"(\d{2})[.-](\d{1,2})[.-](\d{1,2})", compact)
        if short_match:
            year, month, day = short_match.groups()
            return f"{2000 + int(year):04d}-{int(month):02d}-{int(day):02d}"

        return None

    @staticmethod
    def _build_review_id(source: str, raw_text: str, date: str | None, author: str | None) -> str:
        base = f"{source}|{date or ''}|{author or ''}|{raw_text.strip()}".lower()
        return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _build_source_label(source: str) -> str:
        return "네이버" if source == "naver" else "카카오"

    @staticmethod
    def _sort_reviews_by_latest(reviews: List[Dict]) -> List[Dict]:
        def sort_key(review: Dict):
            return review.get("date") or "0000-00-00"

        return sorted(reviews, key=sort_key, reverse=True)

    async def _extract_kakao_search_candidates(self, page: Page) -> List[Dict[str, str]]:
        return await page.evaluate(
            """
            () => Array.from(document.querySelectorAll('li[data-id]')).slice(0, 10).map((item) => ({
              data_id: item.getAttribute('data-id') || '',
              title:
                item.querySelector('strong')?.innerText?.trim() ||
                item.querySelector('.tit_location')?.innerText?.trim() ||
                '',
              address:
                item.querySelector('.txt_address')?.innerText?.trim() ||
                item.querySelector('.addr')?.innerText?.trim() ||
                '',
              context: (item.innerText || '').trim(),
            })).filter((candidate) => candidate.data_id);
            """
        )

    def _normalize_naver_review_date(self, date_text: str | None) -> str | None:
        """네이버 방문자 리뷰 API의 상대 날짜 표기를 YYYY-MM-DD로 변환.

        관측된 형식:
          - 'M.D.요일'      (올해 작성분, 연도 생략)        예) '6.7.일'
          - 'YY.M.D.요일'   (작년 이전, 2자리 연도)         예) '25.6.11.수'
        그 외 형식은 기존 _normalize_review_date 로 폴백한다.
        """
        if not date_text:
            return None

        nums = []
        for token in str(date_text).split("."):
            digits = re.sub(r"[^0-9]", "", token)
            if digits:
                nums.append(int(digits))

        today = datetime.now()
        if len(nums) >= 3:
            year = 2000 + nums[0] if nums[0] < 100 else nums[0]
            month, day = nums[1], nums[2]
        elif len(nums) == 2:
            month, day, year = nums[0], nums[1], today.year
        else:
            # 예상 밖 형식은 기존 절대표기 파서로 시도
            return self._normalize_review_date(date_text)

        try:
            parsed = datetime(year, month, day)
        except ValueError:
            return None

        # 연도를 생략한(올해로 가정한) 리뷰가 미래로 계산되면 작년으로 보정
        # (예: 1월에 조회한 12월 리뷰)
        if len(nums) == 2 and parsed.date() > today.date():
            try:
                parsed = datetime(year - 1, month, day)
            except ValueError:
                return None

        return parsed.strftime("%Y-%m-%d")

    @staticmethod
    def _extract_apollo_state(html: str) -> dict | None:
        """SSR HTML 에 임베드된 window.__APOLLO_STATE__ JSON 을 추출한다."""
        match = re.search(
            r"window\.__APOLLO_STATE__\s*=\s*(\{.*?\})\s*;?\s*</script>", html, re.DOTALL
        )
        if not match:
            match = re.search(r"__APOLLO_STATE__\s*=\s*(\{.*\});", html, re.DOTALL)
        if not match:
            return None

        raw = match.group(1)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # 뒤에 다른 스크립트가 붙은 경우 가장 바깥 균형 중괄호까지만 잘라 재시도
            depth = 0
            end = 0
            for index, char in enumerate(raw):
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        end = index + 1
                        break
            if not end:
                return None
            try:
                return json.loads(raw[:end])
            except json.JSONDecodeError:
                return None

    def _resolve_naver_business_id(self, store_name: str, address: str) -> str | None:
        """pcmap 검색 결과(SSR)에서 가게 후보를 추출하고 점수 매칭으로 businessId 를 고른다."""
        query = self._build_search_query(store_name, address)
        try:
            response = self._session.get(
                self.NAVER_PCMAP_LIST_URL,
                params={"query": query},
                headers={
                    "User-Agent": self.NAVER_MOBILE_UA,
                    "Referer": "https://pcmap.place.naver.com/",
                },
                timeout=15,
            )
        except requests.RequestException as exc:
            logger.error("[Naver] 검색 요청 실패: %s", exc)
            return None

        if response.status_code != 200:
            logger.warning("[Naver] 검색 응답 비정상 status=%s", response.status_code)
            return None

        apollo = self._extract_apollo_state(response.text)
        if not apollo:
            logger.warning("[Naver] 검색 결과 APOLLO_STATE 파싱 실패 (query=%s)", query)
            return None

        candidates: List[Dict[str, str]] = []
        for value in apollo.values():
            if not isinstance(value, dict):
                continue
            if value.get("__typename") != "PlaceListBusinessesItem":
                continue
            business_id = value.get("id")
            name = value.get("name")
            if not (business_id and str(business_id).isdigit() and name):
                continue
            addr = (
                value.get("roadAddress")
                or value.get("commonAddress")
                or value.get("address")
                or ""
            )
            candidates.append(
                {
                    "data_id": str(business_id),
                    "title": name,
                    "address": addr,
                    "context": f"{name} {addr}".strip(),
                }
            )

        if not candidates:
            logger.warning("[Naver] 검색 후보 없음 (query=%s)", query)
            return None

        best = self._select_best_candidate(candidates, store_name, address)
        if not best:
            return None

        logger.info(
            "[Naver] Selected candidate: %s (id=%s, score=%s)",
            best.get("title"),
            best.get("data_id"),
            best.get("match_score", 0),
        )
        return best.get("data_id")

    def _fetch_naver_visitor_reviews(self, business_id: str, max_reviews: int) -> List[Dict]:
        """getVisitorReviews GraphQL 로 방문자 리뷰 원본 item 목록을 받는다(최신 최대 50개)."""
        size = max(1, min(max_reviews, self.NAVER_REVIEW_MAX_PAGE_SIZE))
        gql_query = (
            "query getVisitorReviews($input: VisitorReviewsInput) {"
            " visitorReviews(input: $input) {"
            " total"
            " items { id rating author { nickname } body created visited"
            " media { type } originType }"
            " } }"
        )
        payload = [
            {
                "operationName": "getVisitorReviews",
                "variables": {
                    "input": {
                        "businessId": str(business_id),
                        "businessType": "restaurant",
                        "item": "0",
                        "page": 1,
                        "size": size,
                        "isPhotoUsed": False,
                        "includeContent": True,
                        "cidList": [],
                    }
                },
                "query": gql_query,
            }
        ]
        try:
            response = self._session.post(
                self.NAVER_GRAPHQL_URL,
                json=payload,
                headers={
                    "User-Agent": self.NAVER_MOBILE_UA,
                    "Content-Type": "application/json",
                    "Referer": f"https://m.place.naver.com/restaurant/{business_id}/review/visitor",
                },
                timeout=20,
            )
        except requests.RequestException as exc:
            logger.error("[Naver] 리뷰 API 요청 실패: %s", exc)
            return []

        if response.status_code != 200:
            logger.warning("[Naver] 리뷰 API 응답 비정상 status=%s", response.status_code)
            return []

        try:
            node = response.json()
            node = node[0] if isinstance(node, list) else node
        except (ValueError, IndexError) as exc:
            logger.error("[Naver] 리뷰 API JSON 파싱 실패: %s", exc)
            return []

        if isinstance(node, dict) and node.get("errors"):
            logger.warning(
                "[Naver] 리뷰 API GraphQL 오류: %s",
                str(node["errors"][0].get("message", ""))[:200],
            )
            return []

        visitor_reviews = ((node or {}).get("data") or {}).get("visitorReviews") or {}
        return visitor_reviews.get("items") or []

    def _collect_naver_reviews(self, store_name: str, address: str, max_reviews: int) -> List[Dict]:
        """동기 컨텍스트에서 네이버 리뷰를 수집해 기존과 '동일한 형식'의 dict 리스트로 변환한다."""
        business_id = self._resolve_naver_business_id(store_name, address)
        if not business_id:
            return []

        items = self._fetch_naver_visitor_reviews(business_id, max_reviews)

        reviews: List[Dict] = []
        seen_ids = set()
        for item in items:
            raw_text = item.get("body") or ""
            text = self._clean_review_text(raw_text)
            if len(text) < 2:
                continue

            author = ((item.get("author") or {}).get("nickname") or "").strip() or "네이버 리뷰어"
            date = self._normalize_naver_review_date(item.get("created"))
            review_id = self._build_review_id("naver", text, date, author)
            if review_id in seen_ids:
                continue
            seen_ids.add(review_id)

            reviews.append(
                {
                    "id": review_id,
                    "raw_text": raw_text,
                    "text": text,
                    "rating": None,
                    "date": date,
                    "source": "naver",
                    "source_label": self._build_source_label("naver"),
                    "author": author,
                    "has_photo": bool(item.get("media")),
                }
            )
            if len(reviews) >= max_reviews:
                break

        reviews = self._sort_reviews_by_latest(reviews)
        logger.info("[Naver] Collected %s reviews (API)", len(reviews))
        return reviews

    async def crawl_naver(self, store_name: str, address: str, max_reviews: int = 80) -> List[Dict]:
        query = self._build_search_query(store_name, address)
        logger.info("[Naver] Searching for: %s", query)
        try:
            loop = asyncio.get_running_loop()
            # requests(동기)를 별도 스레드에서 실행해 카카오 크롤링과의 동시성을 유지한다.
            reviews = await loop.run_in_executor(
                None, self._collect_naver_reviews, store_name, address, max_reviews
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[Naver] Crawling error: %s", exc)
            return []

        return reviews[:max_reviews]

    async def crawl_kakao(self, store_name: str, address: str, max_reviews: int = 80) -> List[Dict]:
        query = self._build_search_query(store_name, address, include_address=False)
        logger.info("[Kakao] Searching for: %s", query)
        reviews: List[Dict] = []
        seen_ids = set()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
                viewport={"width": 375, "height": 812},
            )
            page = await context.new_page()

            try:
                search_url = f"https://m.map.kakao.com/actions/searchView?q={quote(query)}"
                await page.goto(search_url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2000)

                candidates = await self._extract_kakao_search_candidates(page)
                if not candidates:
                    logger.warning("[Kakao] No search results found.")
                    return []

                best_candidate = self._select_best_candidate(candidates, store_name, address)
                if not best_candidate:
                    logger.warning("[Kakao] No candidate matched the requested store.")
                    return []

                logger.info(
                    "[Kakao] Selected candidate: %s (score=%s)",
                    best_candidate.get("title") or best_candidate.get("data_id"),
                    best_candidate.get("match_score", 0),
                )

                data_id = best_candidate.get("data_id")
                review_url = f"https://place.map.kakao.com/{data_id}#review"
                await page.goto(review_url, wait_until="networkidle", timeout=30000)
                await page.wait_for_timeout(2500)

                body_text = await page.locator("body").inner_text(timeout=5000)
                if "매장주 요청으로 후기가 제공되지 않는 장소" in body_text or "후기미제공" in body_text:
                    logger.warning(
                        "[Kakao] Place reviews are unavailable for '%s' (%s). Kakao page says reviews are not provided.",
                        best_candidate.get("title") or store_name,
                        data_id,
                    )
                    return []

                stagnant_rounds = 0
                max_rounds = 35
                while len(reviews) < max_reviews and stagnant_rounds < max_rounds:
                    before_count = len(reviews)
                    review_items = page.locator("ul.list_review > li")
                    item_count = await review_items.count()

                    for index in range(item_count):
                        item = review_items.nth(index)
                        try:
                            review_text_block = item.locator("p.desc_review").first
                            if await review_text_block.count() == 0:
                                continue

                            more = review_text_block.locator(".btn_more").first
                            if await more.count() > 0 and await more.is_visible():
                                await more.click(timeout=800)
                                await page.wait_for_timeout(100)

                            text = (await review_text_block.inner_text()).replace("더보기", "").strip()
                            if not text:
                                continue

                            author = "카카오 리뷰어"
                            try:
                                author_text = await item.locator(".link_user, .name_user, strong").first.inner_text()
                                author = author_text.strip() or author
                            except Exception:
                                pass

                            date = None
                            try:
                                date_text = await item.locator(".txt_date").first.inner_text()
                                date = self._normalize_review_date(date_text)
                            except Exception:
                                date = None

                            rating = None
                            try:
                                spans = await item.locator(".starred_grade .screen_out").all()
                                for span in spans:
                                    star_text = await span.inner_text()
                                    if star_text.replace(".", "").isdigit():
                                        rating = float(star_text)
                                        break
                            except Exception:
                                rating = None

                            review_id = self._build_review_id("kakao", text, date, author)
                            if review_id in seen_ids:
                                continue

                            has_photo = False
                            try:
                                has_photo = await item.locator("a.link_photo, ul.list_photo img, span.ico_photo").count() > 0
                            except Exception:
                                has_photo = False

                            reviews.append(
                                {
                                    "id": review_id,
                                    "raw_text": text,
                                    "text": text,
                                    "rating": rating,
                                    "date": date,
                                    "source": "kakao",
                                    "source_label": self._build_source_label("kakao"),
                                    "author": author,
                                    "has_photo": has_photo,
                                }
                            )
                            seen_ids.add(review_id)
                        except Exception:
                            continue

                        if len(reviews) >= max_reviews:
                            break

                    try:
                        more_link = page.locator("a.link_more:has-text('후기 더보기'), a.link_more:has-text('더보기')").first
                        if await more_link.count() > 0 and await more_link.is_visible():
                            await more_link.click()
                            await page.wait_for_timeout(900)
                    except Exception:
                        pass

                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(1000)

                    if len(reviews) == before_count:
                        stagnant_rounds += 1
                    else:
                        stagnant_rounds = 0

                reviews = self._sort_reviews_by_latest(reviews)
                logger.info("[Kakao] Collected %s reviews", len(reviews))
            except Exception as exc:
                logger.error("[Kakao] Crawling error: %s", exc)
            finally:
                await browser.close()

        return reviews[:max_reviews]

    async def collect_all_reviews(self, store_name: str, address: str) -> List[Dict]:
        query = self._build_search_query(store_name, address)
        logger.info("Starting concurrent crawling for: %s", query)

        async def _crawl_all():
            naver_task = asyncio.create_task(self.crawl_naver(store_name, address, max_reviews=80))
            kakao_task = asyncio.create_task(self.crawl_kakao(store_name, address, max_reviews=80))
            return await asyncio.gather(naver_task, kakao_task)

        if sys.platform == "win32":
            result_container = {}

            def _run_in_thread():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    if not isinstance(loop, asyncio.ProactorEventLoop):
                        loop.close()
                        loop = asyncio.ProactorEventLoop()
                        asyncio.set_event_loop(loop)
                    result_container["result"] = loop.run_until_complete(_crawl_all())
                except Exception as exc:
                    result_container["error"] = exc
                finally:
                    loop.close()

            thread = threading.Thread(target=_run_in_thread)
            thread.start()

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, thread.join)

            if "error" in result_container:
                raise result_container["error"]
            results = result_container["result"]
        else:
            results = await _crawl_all()

        all_reviews = results[0] + results[1]
        logger.info(
            "Total reviews collected: %s (Naver: %s, Kakao: %s)",
            len(all_reviews),
            len(results[0]),
            len(results[1]),
        )
        return all_reviews
