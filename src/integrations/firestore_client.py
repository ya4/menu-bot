"""
Firestore client for Menu Bot data persistence.
Handles all database operations for recipes, ratings, meal plans, and family data.
"""

import os
from datetime import datetime, timedelta
from typing import Optional
from google.cloud import firestore
from dataclasses import dataclass, asdict, field


# Data Models

@dataclass
class Ingredient:
    """Represents a recipe ingredient."""
    name: str
    quantity: float
    unit: str
    category: str = "general"
    store_preference: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "Ingredient":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Recipe:
    """Represents a recipe in the system."""
    id: Optional[str] = None
    name: str = ""
    source: str = ""  # "url", "cookbook", "family", "text"
    source_url: Optional[str] = None
    source_details: Optional[str] = None  # e.g., "cookbook:Joy of Cooking:p.123"
    ingredients: list[Ingredient] = field(default_factory=list)
    instructions: list[str] = field(default_factory=list)
    servings: int = 4
    prep_time_min: Optional[int] = None
    cook_time_min: Optional[int] = None
    tags: list[str] = field(default_factory=list)
    seasonal_ingredients: list[str] = field(default_factory=list)
    kid_friendly_score: float = 0.5  # 0-1 scale, updated based on ratings
    health_score: float = 0.5  # 0-1 scale
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    approved: bool = False
    approved_by: Optional[str] = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ingredients"] = [i if isinstance(i, dict) else i.to_dict() for i in self.ingredients]
        if self.created_at:
            data["created_at"] = self.created_at
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict, doc_id: Optional[str] = None) -> "Recipe":
        data = data.copy()
        if doc_id:
            data["id"] = doc_id
        if "ingredients" in data:
            data["ingredients"] = [
                Ingredient.from_dict(i) if isinstance(i, dict) else i
                for i in data["ingredients"]
            ]
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Rating:
    """Represents a meal rating from a family member."""
    id: Optional[str] = None
    recipe_id: str = ""
    user_id: str = ""
    user_name: str = ""
    user_type: str = "adult"  # "adult" or "kid"
    rating: int = 3  # 1-5 for adults, emoji-mapped for kids
    would_repeat: Optional[bool] = None
    notes: Optional[str] = None
    meal_plan_id: Optional[str] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        data = asdict(self)
        if self.created_at:
            data["created_at"] = self.created_at
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict, doc_id: Optional[str] = None) -> "Rating":
        data = data.copy()
        if doc_id:
            data["id"] = doc_id
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class MealPlanEntry:
    """A single meal in a meal plan."""
    date: str  # ISO format date
    day_of_week: str
    recipe_id: str
    recipe_name: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MealPlanEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class MealPlan:
    """Represents a weekly meal plan."""
    id: Optional[str] = None
    week_start: Optional[str] = None  # ISO format date
    meals: list[MealPlanEntry] = field(default_factory=list)
    status: str = "draft"  # "draft", "pending_approval", "active", "completed"
    feedback_collected: bool = False
    created_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["meals"] = [m if isinstance(m, dict) else m.to_dict() for m in self.meals]
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict, doc_id: Optional[str] = None) -> "MealPlan":
        data = data.copy()
        if doc_id:
            data["id"] = doc_id
        if "meals" in data:
            data["meals"] = [
                MealPlanEntry.from_dict(m) if isinstance(m, dict) else m
                for m in data["meals"]
            ]
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class GroceryItem:
    """A single item on the grocery list."""
    name: str
    quantity: float
    unit: str
    store: str = "meijer"
    category: str = "general"
    recipe_sources: list[str] = field(default_factory=list)  # Which recipes need this
    checked: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "GroceryItem":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class GroceryList:
    """Represents a weekly grocery list."""
    id: Optional[str] = None
    meal_plan_id: str = ""
    week_start: str = ""
    items: list[GroceryItem] = field(default_factory=list)
    status: str = "draft"  # "draft", "pending_approval", "approved", "shopping", "completed"
    google_tasks_id: Optional[str] = None
    created_at: Optional[datetime] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        data = asdict(self)
        data["items"] = [i if isinstance(i, dict) else i.to_dict() for i in self.items]
        return {k: v for k, v in data.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict, doc_id: Optional[str] = None) -> "GroceryList":
        data = data.copy()
        if doc_id:
            data["id"] = doc_id
        if "items" in data:
            data["items"] = [
                GroceryItem.from_dict(i) if isinstance(i, dict) else i
                for i in data["items"]
            ]
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class FamilyMember:
    """Represents a family member."""
    slack_user_id: str
    name: str
    user_type: str = "adult"  # "adult" or "kid"
    is_parent: bool = False  # Parents can approve plans
    preference_weight: float = 1.0  # Kids get 1.5 to prioritize their preferences
    google_tasks_linked: bool = False
    google_refresh_token: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "FamilyMember":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Preferences:
    """Global family preferences."""
    bootstrap_complete: bool = False
    preferred_meal_ids: list[str] = field(default_factory=list)
    avoided_ingredients: list[str] = field(default_factory=list)
    health_goals: list[str] = field(default_factory=list)
    favorite_meals: list[str] = field(default_factory=list)  # Text names from initial setup
    location: str = "ann_arbor_mi"
    meal_repeat_buffer_days: int = 14
    planning_channel_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Preferences":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class FirestoreClient:
    """Client for all Firestore database operations."""

    def __init__(self, project_id: Optional[str] = None):
        """Initialize the Firestore client."""
        self.project_id = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.db = firestore.Client(project=self.project_id)

    # ============ Recipe Operations ============

    def save_recipe(self, recipe: Recipe) -> str:
        """Save a recipe to the database."""
        recipe.created_at = recipe.created_at or datetime.utcnow()
        if recipe.id:
            self.db.collection("recipes").document(recipe.id).set(recipe.to_dict())
            return recipe.id
        else:
            doc_ref = self.db.collection("recipes").add(recipe.to_dict())
            return doc_ref[1].id

    def get_recipe(self, recipe_id: str) -> Optional[Recipe]:
        """Get a recipe by ID."""
        doc = self.db.collection("recipes").document(recipe_id).get()
        if doc.exists:
            return Recipe.from_dict(doc.to_dict(), doc.id)
        return None

    def get_all_recipes(self, approved_only: bool = True) -> list[Recipe]:
        """Get all recipes, optionally filtering to approved only."""
        query = self.db.collection("recipes")
        if approved_only:
            query = query.where("approved", "==", True)
        docs = query.stream()
        return [Recipe.from_dict(doc.to_dict(), doc.id) for doc in docs]

    def get_recipes_by_ids(self, recipe_ids: list[str]) -> list[Recipe]:
        """Get multiple recipes by their IDs."""
        recipes = []
        for recipe_id in recipe_ids:
            recipe = self.get_recipe(recipe_id)
            if recipe:
                recipes.append(recipe)
        return recipes

    def approve_recipe(self, recipe_id: str, approved_by: str) -> bool:
        """Approve a recipe (parent only action)."""
        doc_ref = self.db.collection("recipes").document(recipe_id)
        doc_ref.update({
            "approved": True,
            "approved_by": approved_by,
        })
        return True

    def search_recipes_by_name(self, query: str) -> list[Recipe]:
        """Search recipes by name (case-insensitive partial match)."""
        # Firestore doesn't support case-insensitive search natively
        # We'll fetch and filter client-side for now
        all_recipes = self.get_all_recipes(approved_only=False)
        query_lower = query.lower()
        return [r for r in all_recipes if query_lower in r.name.lower()]

    def get_recently_used_recipes(self, days: int = 14) -> list[str]:
        """Get recipe IDs used in the last N days."""
        cutoff = datetime.utcnow() - timedelta(days=days)
        cutoff_str = cutoff.strftime("%Y-%m-%d")

        plans = self.db.collection("meal_plans").where(
            "week_start", ">=", cutoff_str
        ).stream()

        recipe_ids = set()
        for plan in plans:
            plan_data = plan.to_dict()
            for meal in plan_data.get("meals", []):
                recipe_ids.add(meal.get("recipe_id"))

        return list(recipe_ids)

    # ============ Rating Operations ============

    def save_rating(self, rating: Rating) -> str:
        """Save a rating."""
        rating.created_at = rating.created_at or datetime.utcnow()
        doc_ref = self.db.collection("ratings").add(rating.to_dict())
        return doc_ref[1].id

    def get_ratings_for_recipe(self, recipe_id: str) -> list[Rating]:
        """Get all ratings for a specific recipe."""
        docs = self.db.collection("ratings").where(
            "recipe_id", "==", recipe_id
        ).stream()
        return [Rating.from_dict(doc.to_dict(), doc.id) for doc in docs]

    def get_average_rating(self, recipe_id: str) -> dict:
        """Get average ratings for a recipe, broken down by user type."""
        ratings = self.get_ratings_for_recipe(recipe_id)

        adult_ratings = [r.rating for r in ratings if r.user_type == "adult"]
        kid_ratings = [r.rating for r in ratings if r.user_type == "kid"]

        return {
            "adult_avg": sum(adult_ratings) / len(adult_ratings) if adult_ratings else None,
            "kid_avg": sum(kid_ratings) / len(kid_ratings) if kid_ratings else None,
            "adult_count": len(adult_ratings),
            "kid_count": len(kid_ratings),
            "would_repeat_pct": sum(1 for r in ratings if r.would_repeat) / len(ratings) if ratings else None,
        }

    # ============ Meal Plan Operations ============

    def save_meal_plan(self, meal_plan: MealPlan) -> str:
        """Save a meal plan."""
        meal_plan.created_at = meal_plan.created_at or datetime.utcnow()
        if meal_plan.id:
            self.db.collection("meal_plans").document(meal_plan.id).set(meal_plan.to_dict())
            return meal_plan.id
        else:
            doc_ref = self.db.collection("meal_plans").add(meal_plan.to_dict())
            return doc_ref[1].id

    def get_meal_plan(self, plan_id: str) -> Optional[MealPlan]:
        """Get a meal plan by ID."""
        doc = self.db.collection("meal_plans").document(plan_id).get()
        if doc.exists:
            return MealPlan.from_dict(doc.to_dict(), doc.id)
        return None

    def get_current_meal_plan(self) -> Optional[MealPlan]:
        """Get the current active meal plan."""
        docs = self.db.collection("meal_plans").where(
            "status", "==", "active"
        ).limit(1).stream()

        for doc in docs:
            return MealPlan.from_dict(doc.to_dict(), doc.id)
        return None

    def get_pending_meal_plan(self) -> Optional[MealPlan]:
        """Get a meal plan pending approval."""
        docs = self.db.collection("meal_plans").where(
            "status", "==", "pending_approval"
        ).limit(1).stream()

        for doc in docs:
            return MealPlan.from_dict(doc.to_dict(), doc.id)
        return None

    def approve_meal_plan(self, plan_id: str, approved_by: str) -> bool:
        """Approve a meal plan (parent only action)."""
        doc_ref = self.db.collection("meal_plans").document(plan_id)
        doc_ref.update({
            "status": "active",
            "approved_by": approved_by,
            "approved_at": datetime.utcnow(),
        })
        return True

    def get_meal_plans_for_feedback(self) -> list[MealPlan]:
        """Get completed meal plans that haven't had feedback collected."""
        docs = self.db.collection("meal_plans").where(
            "status", "==", "completed"
        ).where(
            "feedback_collected", "==", False
        ).stream()
        return [MealPlan.from_dict(doc.to_dict(), doc.id) for doc in docs]

    # ============ Grocery List Operations ============

    def save_grocery_list(self, grocery_list: GroceryList) -> str:
        """Save a grocery list."""
        grocery_list.created_at = grocery_list.created_at or datetime.utcnow()
        if grocery_list.id:
            self.db.collection("grocery_lists").document(grocery_list.id).set(grocery_list.to_dict())
            return grocery_list.id
        else:
            doc_ref = self.db.collection("grocery_lists").add(grocery_list.to_dict())
            return doc_ref[1].id

    def get_grocery_list(self, list_id: str) -> Optional[GroceryList]:
        """Get a grocery list by ID."""
        doc = self.db.collection("grocery_lists").document(list_id).get()
        if doc.exists:
            return GroceryList.from_dict(doc.to_dict(), doc.id)
        return None

    def get_grocery_list_for_plan(self, meal_plan_id: str) -> Optional[GroceryList]:
        """Get the grocery list associated with a meal plan."""
        docs = self.db.collection("grocery_lists").where(
            "meal_plan_id", "==", meal_plan_id
        ).limit(1).stream()

        for doc in docs:
            return GroceryList.from_dict(doc.to_dict(), doc.id)
        return None

    def get_pending_grocery_list(self) -> Optional[GroceryList]:
        """Get a grocery list pending approval."""
        docs = self.db.collection("grocery_lists").where(
            "status", "==", "pending_approval"
        ).limit(1).stream()

        for doc in docs:
            return GroceryList.from_dict(doc.to_dict(), doc.id)
        return None

    def approve_grocery_list(self, list_id: str, approved_by: str) -> bool:
        """Approve a grocery list (parent only action)."""
        doc_ref = self.db.collection("grocery_lists").document(list_id)
        doc_ref.update({
            "status": "approved",
            "approved_by": approved_by,
            "approved_at": datetime.utcnow(),
        })
        return True

    def update_grocery_item_checked(self, list_id: str, item_name: str, checked: bool) -> bool:
        """Update the checked status of a grocery item."""
        grocery_list = self.get_grocery_list(list_id)
        if not grocery_list:
            return False

        for item in grocery_list.items:
            if item.name == item_name:
                item.checked = checked
                break

        self.save_grocery_list(grocery_list)
        return True

    # ============ Family Member Operations ============

    def save_family_member(self, member: FamilyMember) -> str:
        """Save or update a family member."""
        self.db.collection("family_members").document(member.slack_user_id).set(member.to_dict())
        return member.slack_user_id

    def get_family_member(self, slack_user_id: str) -> Optional[FamilyMember]:
        """Get a family member by Slack user ID."""
        doc = self.db.collection("family_members").document(slack_user_id).get()
        if doc.exists:
            return FamilyMember.from_dict(doc.to_dict())
        return None

    def get_all_family_members(self) -> list[FamilyMember]:
        """Get all family members."""
        docs = self.db.collection("family_members").stream()
        return [FamilyMember.from_dict(doc.to_dict()) for doc in docs]

    def get_parents(self) -> list[FamilyMember]:
        """Get all parent family members."""
        docs = self.db.collection("family_members").where(
            "is_parent", "==", True
        ).stream()
        return [FamilyMember.from_dict(doc.to_dict()) for doc in docs]

    def is_parent(self, slack_user_id: str) -> bool:
        """Check if a user is a parent (has approval permissions)."""
        member = self.get_family_member(slack_user_id)
        return member is not None and member.is_parent

    def update_google_tasks_token(self, slack_user_id: str, refresh_token: str) -> bool:
        """Update the Google Tasks refresh token for a user."""
        doc_ref = self.db.collection("family_members").document(slack_user_id)
        doc_ref.update({
            "google_tasks_linked": True,
            "google_refresh_token": refresh_token,
        })
        return True

    # ============ Preferences Operations ============

    def get_preferences(self) -> Preferences:
        """Get global family preferences."""
        doc = self.db.collection("preferences").document("config").get()
        if doc.exists:
            return Preferences.from_dict(doc.to_dict())
        return Preferences()

    def save_preferences(self, preferences: Preferences) -> bool:
        """Save global family preferences."""
        self.db.collection("preferences").document("config").set(preferences.to_dict())
        return True

    def set_bootstrap_complete(self) -> bool:
        """Mark the bootstrap process as complete."""
        self.db.collection("preferences").document("config").update({
            "bootstrap_complete": True
        })
        return True

    def add_preferred_meal(self, recipe_id: str) -> bool:
        """Add a recipe to the preferred meals list."""
        prefs = self.get_preferences()
        if recipe_id not in prefs.preferred_meal_ids:
            prefs.preferred_meal_ids.append(recipe_id)
            self.save_preferences(prefs)
        return True

    def set_planning_channel(self, channel_id: str) -> bool:
        """Set the Slack channel for meal planning interactions."""
        self.db.collection("preferences").document("config").update({
            "planning_channel_id": channel_id
        })
        return True

    # ============ Utility Operations ============

    def get_recipe_scores(self) -> dict[str, dict]:
        """
        Calculate weighted scores for all recipes based on ratings.
        Kid ratings are weighted higher (1.5x) to prioritize their preferences.
        """
        recipes = self.get_all_recipes(approved_only=True)
        scores = {}

        for recipe in recipes:
            ratings = self.get_ratings_for_recipe(recipe.id)
            if not ratings:
                scores[recipe.id] = {
                    "weighted_score": recipe.kid_friendly_score * 3,  # Default score
                    "total_ratings": 0,
                }
                continue

            weighted_sum = 0
            weight_total = 0

            for rating in ratings:
                weight = 1.5 if rating.user_type == "kid" else 1.0
                weighted_sum += rating.rating * weight
                weight_total += weight

            scores[recipe.id] = {
                "weighted_score": weighted_sum / weight_total if weight_total > 0 else 3,
                "total_ratings": len(ratings),
            }

        return scores
