"""
Seasonal produce helper for Michigan/Ann Arbor region.
Provides information about what's in season and suggests seasonal meals.
"""

import os
from datetime import datetime
from typing import Optional
import yaml


class SeasonalHelper:
    """Helper class for seasonal produce and meal suggestions."""

    def __init__(self, config_path: Optional[str] = None):
        """Initialize with seasonal configuration."""
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(__file__),
                "..", "..", "config", "seasonal.yaml"
            )

        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        self.seasons = self.config.get("seasons", {})
        self.suggestions = self.config.get("seasonal_meal_suggestions", {})

    def get_current_season(self, date: Optional[datetime] = None) -> str:
        """
        Get the current season based on the date.

        Args:
            date: Date to check (defaults to today)

        Returns:
            Season name: "spring", "summer", "fall", or "winter"
        """
        if date is None:
            date = datetime.now()

        month = date.month

        for season_name, season_data in self.seasons.items():
            if month in season_data.get("months", []):
                return season_name

        return "winter"  # Default fallback

    def get_peak_produce(self, date: Optional[datetime] = None) -> list[dict]:
        """
        Get produce that is currently in peak season.

        Args:
            date: Date to check (defaults to today)

        Returns:
            List of produce items with their details
        """
        if date is None:
            date = datetime.now()

        month = date.month
        season = self.get_current_season(date)
        season_data = self.seasons.get(season, {})

        peak_items = []
        for item in season_data.get("peak_produce", []):
            if month in item.get("months", []):
                peak_items.append({
                    "name": item["name"],
                    "notes": item.get("notes", ""),
                })

        return peak_items

    def get_peak_produce_names(self, date: Optional[datetime] = None) -> list[str]:
        """Get just the names of produce currently in peak season."""
        return [item["name"] for item in self.get_peak_produce(date)]

    def is_in_season(self, ingredient: str, date: Optional[datetime] = None) -> bool:
        """
        Check if an ingredient is currently in season.

        Args:
            ingredient: Ingredient name to check
            date: Date to check (defaults to today)

        Returns:
            True if the ingredient is in season
        """
        peak_produce = self.get_peak_produce_names(date)
        ingredient_lower = ingredient.lower()

        for produce in peak_produce:
            if produce.lower() in ingredient_lower or ingredient_lower in produce.lower():
                return True

        return False

    def get_seasonal_score(self, ingredients: list[str], date: Optional[datetime] = None) -> float:
        """
        Calculate what percentage of ingredients are in season.

        Args:
            ingredients: List of ingredient names
            date: Date to check

        Returns:
            Score from 0 to 1 indicating seasonal alignment
        """
        if not ingredients:
            return 0.5  # Neutral score for no ingredients

        # Filter to produce-like ingredients
        produce_keywords = [
            "vegetable", "fruit", "tomato", "pepper", "onion", "garlic",
            "lettuce", "spinach", "kale", "carrot", "potato", "squash",
            "apple", "berry", "melon", "corn", "bean", "pea", "herb",
            "basil", "cilantro", "cucumber", "zucchini", "broccoli",
        ]

        produce_ingredients = []
        for ing in ingredients:
            ing_lower = ing.lower()
            if any(kw in ing_lower for kw in produce_keywords):
                produce_ingredients.append(ing)

        if not produce_ingredients:
            return 0.5  # Neutral if no produce

        in_season_count = sum(1 for ing in produce_ingredients if self.is_in_season(ing, date))
        return in_season_count / len(produce_ingredients)

    def get_meal_suggestions(self, date: Optional[datetime] = None) -> list[str]:
        """
        Get meal suggestions appropriate for the current season.

        Args:
            date: Date to check

        Returns:
            List of meal suggestion strings
        """
        season = self.get_current_season(date)
        return self.suggestions.get(season, [])

    def get_seasonal_context(self, date: Optional[datetime] = None) -> dict:
        """
        Get full seasonal context for meal planning.

        Args:
            date: Date to check

        Returns:
            Dictionary with seasonal information
        """
        if date is None:
            date = datetime.now()

        season = self.get_current_season(date)
        peak_produce = self.get_peak_produce(date)

        return {
            "season": season,
            "month": date.strftime("%B"),
            "peak_produce": peak_produce,
            "peak_produce_names": [p["name"] for p in peak_produce],
            "meal_suggestions": self.get_meal_suggestions(date),
            "notes": self.seasons.get(season, {}).get("notes", ""),
        }

    def suggest_seasonal_swaps(self, ingredients: list[str], date: Optional[datetime] = None) -> list[dict]:
        """
        Suggest seasonal alternatives for out-of-season ingredients.

        Args:
            ingredients: List of ingredient names
            date: Date to check

        Returns:
            List of swap suggestions
        """
        peak_produce = self.get_peak_produce_names(date)
        swaps = []

        # Common swap mappings
        swap_map = {
            "tomatoes": {"winter": ["canned tomatoes", "sun-dried tomatoes"]},
            "corn": {"winter": ["frozen corn"], "spring": ["peas"]},
            "zucchini": {"winter": ["butternut squash"], "fall": ["winter squash"]},
            "berries": {"winter": ["frozen berries", "apples"]},
            "peaches": {"winter": ["canned peaches", "apples"], "spring": ["strawberries"]},
            "asparagus": {"winter": ["broccoli"], "fall": ["brussels sprouts"]},
        }

        season = self.get_current_season(date)

        for ing in ingredients:
            ing_lower = ing.lower()
            if not self.is_in_season(ing, date):
                # Check if we have swap suggestions
                for key, seasons in swap_map.items():
                    if key in ing_lower and season in seasons:
                        swaps.append({
                            "original": ing,
                            "suggestions": seasons[season],
                            "reason": f"{ing} is not in season in {season}",
                        })
                        break

        return swaps
