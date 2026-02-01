"""
Meal planning engine.
Generates weekly meal plans based on family preferences, ratings, and seasonality.
"""

import random
from datetime import datetime, timedelta
from typing import Optional

from src.integrations.firestore_client import (
    FirestoreClient,
    Recipe,
    MealPlan,
    MealPlanEntry,
)
from src.integrations.claude_client import ClaudeClient
from src.core.seasonal import SeasonalHelper


class MealPlanner:
    """Generates and manages meal plans."""

    def __init__(
        self,
        firestore_client: Optional[FirestoreClient] = None,
        claude_client: Optional[ClaudeClient] = None,
        seasonal_helper: Optional[SeasonalHelper] = None,
    ):
        """Initialize the meal planner."""
        self.db = firestore_client or FirestoreClient()
        self.claude = claude_client or ClaudeClient()
        self.seasonal = seasonal_helper or SeasonalHelper()

    def generate_weekly_plan(
        self,
        week_start: Optional[datetime] = None,
        num_days: int = 7,
    ) -> MealPlan:
        """
        Generate a meal plan for the week.

        Args:
            week_start: Start date of the week (defaults to next Monday)
            num_days: Number of days to plan (default 7)

        Returns:
            Generated MealPlan
        """
        if week_start is None:
            # Default to next Monday
            today = datetime.now()
            days_until_monday = (7 - today.weekday()) % 7
            if days_until_monday == 0:
                days_until_monday = 7  # Next Monday if today is Monday
            week_start = today + timedelta(days=days_until_monday)
            week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

        # Get all available recipes
        recipes = self.db.get_all_recipes(approved_only=True)
        if not recipes:
            raise ValueError("No approved recipes available for meal planning")

        # Get recently used recipes to avoid
        prefs = self.db.get_preferences()
        recently_used = set(self.db.get_recently_used_recipes(
            days=prefs.meal_repeat_buffer_days
        ))

        # Get recipe scores
        scores = self.db.get_recipe_scores()

        # Get seasonal context
        seasonal_context = self.seasonal.get_seasonal_context(week_start)

        # Score and rank recipes
        ranked_recipes = self._rank_recipes(
            recipes,
            scores,
            recently_used,
            seasonal_context,
            prefs,
        )

        # Select meals for each day
        meals = []
        used_this_week = set()
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        for i in range(num_days):
            meal_date = week_start + timedelta(days=i)
            day_name = day_names[i % 7]

            # Select a recipe that hasn't been used this week
            recipe = self._select_recipe(
                ranked_recipes,
                used_this_week,
                day_name,
                meal_date,
            )

            if recipe:
                used_this_week.add(recipe.id)
                meals.append(MealPlanEntry(
                    date=meal_date.strftime("%Y-%m-%d"),
                    day_of_week=day_name,
                    recipe_id=recipe.id,
                    recipe_name=recipe.name,
                ))

        # Create the meal plan
        meal_plan = MealPlan(
            week_start=week_start.strftime("%Y-%m-%d"),
            meals=meals,
            status="pending_approval",  # Requires parent approval
        )

        return meal_plan

    def _rank_recipes(
        self,
        recipes: list[Recipe],
        scores: dict[str, dict],
        recently_used: set[str],
        seasonal_context: dict,
        prefs,
    ) -> list[tuple[Recipe, float]]:
        """
        Rank recipes by a combined score.

        Returns:
            List of (recipe, score) tuples sorted by score descending
        """
        ranked = []
        peak_produce = set(p.lower() for p in seasonal_context.get("peak_produce_names", []))

        for recipe in recipes:
            # Skip recently used recipes
            if recipe.id in recently_used:
                continue

            # Base score from ratings (weighted toward kid preferences)
            recipe_scores = scores.get(recipe.id, {})
            base_score = recipe_scores.get("weighted_score", 3.0)

            # Bonus for kid-friendly recipes (prioritize kid preferences)
            kid_bonus = recipe.kid_friendly_score * 1.5

            # Bonus for healthy recipes (balance kid preferences with health)
            health_bonus = recipe.health_score * 0.5

            # Seasonal bonus
            seasonal_bonus = 0
            for ing in recipe.seasonal_ingredients:
                if ing.lower() in peak_produce:
                    seasonal_bonus += 0.3

            # Preferred meal bonus
            if recipe.id in prefs.preferred_meal_ids:
                preferred_bonus = 1.0
            else:
                preferred_bonus = 0

            # Calculate total score
            total_score = (
                base_score +
                kid_bonus +
                health_bonus +
                min(seasonal_bonus, 1.0) +  # Cap seasonal bonus
                preferred_bonus
            )

            ranked.append((recipe, total_score))

        # Sort by score descending
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked

    def _select_recipe(
        self,
        ranked_recipes: list[tuple[Recipe, float]],
        used_this_week: set[str],
        day_name: str,
        date: datetime,
    ) -> Optional[Recipe]:
        """
        Select a recipe for a specific day.

        Uses weighted random selection from top candidates to add variety.
        """
        # Filter out recipes already used this week
        available = [
            (r, s) for r, s in ranked_recipes
            if r.id not in used_this_week
        ]

        if not available:
            return None

        # Take top candidates (more for weekends, fewer for weekdays)
        if day_name in ["Saturday", "Sunday"]:
            # Weekends: more variety, include some adventurous options
            top_n = min(10, len(available))
        elif day_name == "Friday":
            # Friday: often pizza/easy night
            # Look for quick or kid-favorite recipes
            quick_recipes = [
                (r, s + 1.0) for r, s in available
                if "quick" in r.tags or "kid-friendly" in r.tags
            ]
            if quick_recipes:
                available = quick_recipes
            top_n = min(5, len(available))
        else:
            # Weekdays: balanced selection
            top_n = min(7, len(available))

        candidates = available[:top_n]

        # Weighted random selection
        if len(candidates) == 1:
            return candidates[0][0]

        total_score = sum(s for _, s in candidates)
        if total_score == 0:
            return random.choice(candidates)[0]

        # Select based on weighted probability
        r = random.uniform(0, total_score)
        cumulative = 0
        for recipe, score in candidates:
            cumulative += score
            if r <= cumulative:
                return recipe

        return candidates[-1][0]

    def regenerate_meal(
        self,
        meal_plan: MealPlan,
        day_to_replace: str,
    ) -> MealPlan:
        """
        Regenerate a single meal in the plan.

        Args:
            meal_plan: Current meal plan
            day_to_replace: Day of week to regenerate (e.g., "Monday")

        Returns:
            Updated MealPlan
        """
        # Get all recipes and scores
        recipes = self.db.get_all_recipes(approved_only=True)
        scores = self.db.get_recipe_scores()
        prefs = self.db.get_preferences()

        # Get recipes already in this plan
        used_ids = {m.recipe_id for m in meal_plan.meals}

        # Also exclude recently used
        recently_used = set(self.db.get_recently_used_recipes(
            days=prefs.meal_repeat_buffer_days
        ))
        excluded = used_ids | recently_used

        # Find the meal to replace
        meal_index = None
        meal_date = None
        for i, meal in enumerate(meal_plan.meals):
            if meal.day_of_week == day_to_replace:
                meal_index = i
                meal_date = datetime.strptime(meal.date, "%Y-%m-%d")
                # Remove this recipe from excluded so we can potentially keep it
                excluded.discard(meal.recipe_id)
                break

        if meal_index is None:
            return meal_plan

        # Get seasonal context
        seasonal_context = self.seasonal.get_seasonal_context(meal_date)

        # Rank available recipes
        ranked = self._rank_recipes(
            recipes,
            scores,
            excluded,
            seasonal_context,
            prefs,
        )

        # Select a new recipe
        new_recipe = self._select_recipe(
            ranked,
            used_ids,
            day_to_replace,
            meal_date,
        )

        if new_recipe:
            meal_plan.meals[meal_index] = MealPlanEntry(
                date=meal_plan.meals[meal_index].date,
                day_of_week=day_to_replace,
                recipe_id=new_recipe.id,
                recipe_name=new_recipe.name,
            )

        return meal_plan

    def get_plan_explanation(self, meal_plan: MealPlan) -> str:
        """
        Generate a natural language explanation of the meal plan.

        Args:
            meal_plan: The meal plan to explain

        Returns:
            Explanation text
        """
        # Get recipes for the plan
        recipe_ids = [m.recipe_id for m in meal_plan.meals]
        recipes = self.db.get_recipes_by_ids(recipe_ids)
        recipes_by_id = {r.id: r for r in recipes}

        # Gather stats
        kid_friendly_count = sum(
            1 for m in meal_plan.meals
            if recipes_by_id.get(m.recipe_id, Recipe()).kid_friendly_score >= 0.7
        )
        total_meals = len(meal_plan.meals)

        # Get seasonal context
        week_start = datetime.strptime(meal_plan.week_start, "%Y-%m-%d")
        seasonal_context = self.seasonal.get_seasonal_context(week_start)

        # Find seasonal items used
        seasonal_items = []
        peak_produce = set(p.lower() for p in seasonal_context.get("peak_produce_names", []))
        for recipe in recipes:
            for ing in recipe.seasonal_ingredients:
                if ing.lower() in peak_produce and ing not in seasonal_items:
                    seasonal_items.append(ing)

        # Prepare context for Claude
        meals = [
            {"day": m.day_of_week, "name": m.recipe_name}
            for m in meal_plan.meals
        ]

        context = {
            "season": seasonal_context["season"],
            "kid_friendly_pct": kid_friendly_count / total_meals if total_meals > 0 else 0,
            "seasonal_items": seasonal_items[:5],  # Limit to 5
        }

        return self.claude.generate_meal_plan_explanation(meals, context)

    def get_plan_summary(self, meal_plan: MealPlan) -> dict:
        """
        Get a summary of the meal plan with statistics.

        Args:
            meal_plan: The meal plan

        Returns:
            Summary dictionary
        """
        recipe_ids = [m.recipe_id for m in meal_plan.meals]
        recipes = self.db.get_recipes_by_ids(recipe_ids)

        total_prep_time = 0
        total_cook_time = 0
        kid_friendly_count = 0
        healthy_count = 0
        quick_count = 0

        for recipe in recipes:
            if recipe.prep_time_min:
                total_prep_time += recipe.prep_time_min
            if recipe.cook_time_min:
                total_cook_time += recipe.cook_time_min
            if recipe.kid_friendly_score >= 0.7:
                kid_friendly_count += 1
            if recipe.health_score >= 0.7:
                healthy_count += 1
            if "quick" in recipe.tags:
                quick_count += 1

        return {
            "total_meals": len(meal_plan.meals),
            "avg_prep_time": total_prep_time / len(recipes) if recipes else 0,
            "avg_cook_time": total_cook_time / len(recipes) if recipes else 0,
            "kid_friendly_meals": kid_friendly_count,
            "healthy_meals": healthy_count,
            "quick_meals": quick_count,
        }
