"""
MongoDB 연결 및 데이터 저장 서비스
"""
import hashlib
import os
from datetime import datetime, timezone

from pymongo import MongoClient

from app.utils.logger import get_logger

logger = get_logger(__name__)

class MongoService:
    _instance = None
    SNAPSHOT_VERSION = 5
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MongoService, cls).__new__(cls)
            cls._instance.initialized = False
        return cls._instance

    def initialize(self):
        if self.initialized:
            return
            
        # 로컬 MongoDB 연결 (기본 포트 27017)
        # 실제 배포 시에는 환경변수에서 MONGO_URI를 가져와야 합니다.
        mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
        db_name = os.getenv("MONGO_DB_NAME", "pulse_db")
        
        self.client = MongoClient(mongo_uri)
        self.db = self.client[db_name] # DB 이름: pulse_db
        self.collection = self.db["analysis_results"] # 컬렉션: analysis_results
        self.raw_task_collection = self.db["raw_reviews"]
        self.raw_snapshot_collection = self.db["raw_review_snapshots"]

        self.collection.create_index("task_id", unique=True)
        self.raw_task_collection.create_index("task_id", unique=True)
        self.raw_snapshot_collection.create_index("store_key", unique=True)
        self.raw_snapshot_collection.create_index("updated_at")
        # get_latest_reviews()의 폴백 조회 패턴(store_name으로 필터 후 updated_at/최신순 정렬)에 대응.
        self.raw_snapshot_collection.create_index([("store_name", 1), ("updated_at", -1)])
        self.raw_task_collection.create_index([("store_name", 1), ("_id", -1)])
        
        self.initialized = True
        logger.info("✅ [MongoService] Connected to MongoDB (pulse_db.analysis_results)")

    @staticmethod
    def build_store_key(store_name: str, address: str) -> str:
        base = f"{(store_name or '').strip()}|{(address or '').strip()}".lower()
        return hashlib.sha1(base.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_lookup_text(text: str) -> str:
        return "".join(character for character in (text or "").lower() if character.isalnum())

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _build_source_label(source: str) -> str:
        return "네이버" if source == "naver" else "카카오"

    def _normalize_review(self, review: dict) -> dict:
        source = review.get("source") or "naver"
        raw_text = (review.get("raw_text") or review.get("text") or "").strip()
        author = review.get("author") or f"{self._build_source_label(source)} 리뷰어"
        date = review.get("date")
        review_id = review.get("id") or hashlib.sha1(
            f"{source}|{date or ''}|{author}|{raw_text}".lower().encode("utf-8")
        ).hexdigest()[:16]

        return {
            "id": review_id,
            "raw_text": raw_text,
            "text": review.get("text") or raw_text,
            "rating": review.get("rating"),
            "date": date,
            "source": source,
            "source_label": review.get("source_label") or self._build_source_label(source),
            "author": author,
            "has_photo": bool(review.get("has_photo", review.get("hasPhoto", False))),
        }

    def save_raw_reviews(self, task_id: str, store_name: str, address: str, reviews: list[dict]):
        """
        리뷰 원문은 가게 기준 최신 스냅샷 하나만 유지하고,
        task 문서에는 스냅샷 참조 메타데이터만 저장합니다.
        """
        if not self.initialized:
            self.initialize()

        normalized_reviews = [self._normalize_review(review) for review in reviews]
        store_key = self.build_store_key(store_name, address)
        now = self._utcnow()
        source_counts = {
            "naver": sum(1 for review in normalized_reviews if review.get("source") == "naver"),
            "kakao": sum(1 for review in normalized_reviews if review.get("source") == "kakao"),
        }

        self.raw_snapshot_collection.update_one(
            {"store_key": store_key},
            {
                "$set": {
                    "store_key": store_key,
                    "store_name": store_name,
                    "address": address,
                    "crawler_version": self.SNAPSHOT_VERSION,
                    "updated_at": now,
                    "latest_task_id": task_id,
                    "reviews_count": len(normalized_reviews),
                    "source_counts": source_counts,
                    "reviews": normalized_reviews,
                },
                "$setOnInsert": {
                    "created_at": now,
                },
            },
            upsert=True,
        )

        self.raw_task_collection.update_one(
            {"task_id": task_id},
            {
                "$set": {
                    "task_id": task_id,
                    "store_key": store_key,
                    "store_name": store_name,
                    "address": address,
                    "reviews_count": len(normalized_reviews),
                    "source_counts": source_counts,
                    "snapshot_ref": store_key,
                    "saved_at": now,
                }
            },
            upsert=True,
        )

        logger.info(f"💾 [MongoService] Raw reviews snapshot saved for task {task_id}")
        return {
            "store_key": store_key,
            "reviews_count": len(normalized_reviews),
            "source_counts": source_counts,
            "last_crawled_at": now.isoformat(),
        }

    def get_latest_reviews(self, store_name: str, address: str):
        if not self.initialized:
            self.initialize()

        store_key = self.build_store_key(store_name, address)
        normalized_store_name = self._normalize_lookup_text(store_name)
        document = self.raw_snapshot_collection.find_one({"store_key": store_key})
        if not document:
            document = self.raw_snapshot_collection.find_one(
                {"store_name": store_name},
                sort=[("updated_at", -1)],
            )
        if not document:
            for candidate in self.raw_snapshot_collection.find({}, sort=[("updated_at", -1)]).limit(20):
                if self._normalize_lookup_text(candidate.get("store_name")) == normalized_store_name:
                    document = candidate
                    break
        if not document:
            legacy_document = self.raw_task_collection.find_one(
                {"store_name": store_name, "reviews": {"$exists": True}},
                sort=[("_id", -1)],
            )
            if not legacy_document:
                for candidate in self.raw_task_collection.find({"reviews": {"$exists": True}}, sort=[("_id", -1)]).limit(50):
                    if self._normalize_lookup_text(candidate.get("store_name")) == normalized_store_name:
                        legacy_document = candidate
                        break
            if legacy_document:
                reviews = legacy_document.get("reviews", [])
                self.save_raw_reviews(
                    legacy_document.get("task_id", "legacy-migration"),
                    store_name,
                    address,
                    reviews,
                )
                document = self.raw_snapshot_collection.find_one({"store_key": store_key})
        if not document:
            return None

        document.pop("_id", None)
        updated_at = document.pop("updated_at", None)
        document.pop("created_at", None)
        document["last_crawled_at"] = updated_at.isoformat() if updated_at else None
        return document

    def save_result(self, task_id: str, data: dict):
        """
        분석 결과를 MongoDB에 저장합니다.
        """
        if not self.initialized:
            self.initialize()
            
        try:
             # task_id를 _id로 사용하거나 별도 필드로 저장
            document = data.copy()
            document["task_id"] = task_id
            
            self.collection.update_one(
                {"task_id": task_id},
                {"$set": document},
                upsert=True,
            )
            logger.info(f"💾 [MongoService] Result saved for task {task_id}")
            return True
        except Exception as e:
            logger.error(f"❌ [MongoService] Failed to save result: {e}")
            return False
