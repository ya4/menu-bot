"""
Firestore metrics client for tracking ratings and usage history.
Handles time-series data that doesn't need direct human editing.
"""

import os
import logging
from typing import Optional
from datetime import datetime
from dataclasses import dataclass, asdict

from google.cloud import firestore

logger = logging.getLogger(__name__)


@dataclass
class Rating:
    """Individual meal rating event."""
    recipe_name: str
    user_name: str
    user_type: str  # "parent" or "kid"
    score: float  # 1-5 for adults, emoji-mapped for kids
    timestamp: datetime = None
    notes: str = ""

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

    def to_dict(self) -> dict:
        return {
            "recipe_name": self.recipe_name,
            "user_name": self.user_name,
            "user_type": self.user_type,
            "score": self.score,
            "timestamp": self.timestamp,
            "notes": self.notes,
        }


@dataclass
class UsageEvent:
    """Records when a recipe was used in a meal plan."""
    recipe_name: str
    date: str  # YYYY-MM-DD
    meal_plan_week: str
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

    def to_dict(self) -> dict:
        return {
            "recipe_name": self.recipe_name,
            "date": self.date,
            "meal_plan_week": self.meal_plan_week,
            "timestamp": self.timestamp,
        }


class MetricsClient:
    """Client for storing and querying meal metrics in Firestore."""

    def __init__(self, project_id: Optional[str] = None):
        """Initialize the metrics client."""
        self.project_id = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.db = firestore.Client(project=self.project_id)

    # ==================== Rating Operations ====================

    def add_rating(self, rating: Rating) -> str:
        """Add a new rating."""
        doc_ref = self.db.collection("ratings").document()
        doc_ref.set(rating.to_dict())
        logger.info(f"Added rating for {rating.recipe_name} by {rating.user_name}")
        return doc_ref.id

    def get_ratings_for_recipe(self, recipe_name: str) -> list[Rating]:
        """Get all ratings for a recipe."""
        ratings = []
        docs = (
            self.db.collection("ratings")
            .where("recipe_name", "==", recipe_name)
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .stream()
        )

        for doc in docs:
            data = doc.to_dict()
            ratings.append(Rating(
                recipe_name=data.get("recipe_name", ""),
                user_name=data.get("user_name", ""),
                user_type=data.get("user_type", ""),
                score=data.get("score", 0),
                timestamp=data.get("timestamp"),
                notes=data.get("notes", ""),
            ))

        return ratings

    def get_kid_score(self, recipe_name: str) -> Optional[float]:
        """Calculate average kid rating for a recipe."""
        ratings = self.get_ratings_for_recipe(recipe_name)
        kid_ratings = [r.score for r in ratings if r.user_type == "kid"]

        if not kid_ratings:
            return None

        return sum(kid_ratings) / len(kid_ratings)

    def get_average_score(self, recipe_name: str) -> Optional[float]:
        """Calculate overall average rating for a recipe."""
        ratings = self.get_ratings_for_recipe(recipe_name)

        if not ratings:
            return None

        return sum(r.score for r in ratings) / len(ratings)

    def get_recent_ratings(self, limit: int = 20) -> list[Rating]:
        """Get most recent ratings across all recipes."""
        ratings = []
        docs = (
            self.db.collection("ratings")
            .order_by("timestamp", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )

        for doc in docs:
            data = doc.to_dict()
            ratings.append(Rating(
                recipe_name=data.get("recipe_name", ""),
                user_name=data.get("user_name", ""),
                user_type=data.get("user_type", ""),
                score=data.get("score", 0),
                timestamp=data.get("timestamp"),
                notes=data.get("notes", ""),
            ))

        return ratings

    # ==================== Usage Operations ====================

    def record_usage(self, usage: UsageEvent) -> str:
        """Record that a recipe was used."""
        doc_ref = self.db.collection("usage").document()
        doc_ref.set(usage.to_dict())
        logger.info(f"Recorded usage of {usage.recipe_name} on {usage.date}")
        return doc_ref.id

    def get_usage_count(self, recipe_name: str) -> int:
        """Get how many times a recipe has been used."""
        docs = (
            self.db.collection("usage")
            .where("recipe_name", "==", recipe_name)
            .stream()
        )
        return sum(1 for _ in docs)

    def get_last_used(self, recipe_name: str) -> Optional[str]:
        """Get the date a recipe was last used."""
        docs = (
            self.db.collection("usage")
            .where("recipe_name", "==", recipe_name)
            .order_by("date", direction=firestore.Query.DESCENDING)
            .limit(1)
            .stream()
        )

        for doc in docs:
            return doc.to_dict().get("date")

        return None

    def get_recipes_used_since(self, since_date: str) -> list[str]:
        """Get recipe names used since a date (for no-repeat logic)."""
        docs = (
            self.db.collection("usage")
            .where("date", ">=", since_date)
            .stream()
        )

        return list(set(doc.to_dict().get("recipe_name", "") for doc in docs))

    def get_usage_stats(self) -> dict[str, dict]:
        """Get usage statistics for all recipes."""
        stats = {}

        docs = self.db.collection("usage").stream()
        for doc in docs:
            data = doc.to_dict()
            name = data.get("recipe_name", "")
            date = data.get("date", "")

            if name not in stats:
                stats[name] = {"count": 0, "last_used": ""}

            stats[name]["count"] += 1
            if date > stats[name]["last_used"]:
                stats[name]["last_used"] = date

        return stats

    # ==================== Sync Operations ====================

    def compute_recipe_metrics(self, recipe_name: str) -> dict:
        """
        Compute all metrics for a recipe.
        Returns dict with kid_score, health_score, times_used, last_used.
        """
        return {
            "kid_score": self.get_kid_score(recipe_name),
            "average_score": self.get_average_score(recipe_name),
            "times_used": self.get_usage_count(recipe_name),
            "last_used": self.get_last_used(recipe_name),
        }

    def get_stale_recipes(self, days: int = 60) -> list[str]:
        """
        Get recipes that haven't been used in N days.
        Useful for identifying recipes to reconsider.
        """
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        all_stats = self.get_usage_stats()
        stale = []

        for recipe_name, stats in all_stats.items():
            if stats["last_used"] and stats["last_used"] < cutoff:
                stale.append(recipe_name)

        return stale

    def get_favorites(self, min_uses: int = 3, min_score: float = 4.0) -> list[str]:
        """
        Get recipes that are likely favorites based on usage and ratings.
        """
        favorites = []
        stats = self.get_usage_stats()

        for recipe_name, usage in stats.items():
            if usage["count"] >= min_uses:
                avg_score = self.get_average_score(recipe_name)
                if avg_score and avg_score >= min_score:
                    favorites.append(recipe_name)

        return favorites
