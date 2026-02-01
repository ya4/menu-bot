"""
Grocery list optimizer.
Generates shopping lists organized by store based on preferences and item categories.
"""

import os
from collections import defaultdict
from typing import Optional
import yaml

from src.integrations.firestore_client import (
    FirestoreClient,
    MealPlan,
    GroceryList,
    GroceryItem,
    Recipe,
    Ingredient,
)


class GroceryOptimizer:
    """Generates and optimizes grocery lists."""

    def __init__(
        self,
        firestore_client: Optional[FirestoreClient] = None,
        config_path: Optional[str] = None,
    ):
        """Initialize the grocery optimizer."""
        self.db = firestore_client or FirestoreClient()

        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(__file__),
                "..", "..", "config", "stores.yaml"
            )

        with open(config_path, "r") as f:
            self.config = yaml.safe_load(f)

        self.stores = self.config.get("stores", {})
        self.categories = self.config.get("ingredient_categories", {})

    def generate_grocery_list(self, meal_plan: MealPlan) -> GroceryList:
        """
        Generate a grocery list for a meal plan.

        Args:
            meal_plan: The meal plan to generate a list for

        Returns:
            Optimized GroceryList
        """
        # Get all recipes in the meal plan
        recipe_ids = [m.recipe_id for m in meal_plan.meals]
        recipes = self.db.get_recipes_by_ids(recipe_ids)

        # Aggregate ingredients across all recipes
        aggregated = self._aggregate_ingredients(recipes)

        # Assign stores to each ingredient
        items = []
        for (name, unit), (total_qty, recipe_sources, category) in aggregated.items():
            store = self._assign_store(name, category, total_qty)
            items.append(GroceryItem(
                name=name,
                quantity=total_qty,
                unit=unit,
                store=store,
                category=category,
                recipe_sources=recipe_sources,
                checked=False,
            ))

        # Sort items by store, then category, then name
        store_order = ["trader_joes", "costco", "buschs", "meijer"]
        items.sort(key=lambda x: (
            store_order.index(x.store) if x.store in store_order else 99,
            x.category,
            x.name,
        ))

        # Create the grocery list
        grocery_list = GroceryList(
            meal_plan_id=meal_plan.id,
            week_start=meal_plan.week_start,
            items=items,
            status="pending_approval",  # Requires parent approval
        )

        return grocery_list

    def _aggregate_ingredients(
        self,
        recipes: list[Recipe],
    ) -> dict[tuple[str, str], tuple[float, list[str], str]]:
        """
        Aggregate ingredients across multiple recipes.

        Returns:
            Dictionary mapping (name, unit) to (total_quantity, recipe_sources, category)
        """
        # Group by normalized ingredient name and unit
        aggregated = defaultdict(lambda: [0, [], "general"])

        for recipe in recipes:
            for ingredient in recipe.ingredients:
                # Normalize the ingredient name
                name = self._normalize_ingredient_name(ingredient.name)
                unit = self._normalize_unit(ingredient.unit)
                key = (name, unit)

                aggregated[key][0] += ingredient.quantity
                if recipe.name not in aggregated[key][1]:
                    aggregated[key][1].append(recipe.name)

                # Use the ingredient's category or infer it
                category = ingredient.category
                if category == "general":
                    category = self._infer_category(name)
                aggregated[key][2] = category

        return dict(aggregated)

    def _normalize_ingredient_name(self, name: str) -> str:
        """Normalize an ingredient name for aggregation."""
        # Convert to lowercase and strip
        name = name.lower().strip()

        # Remove common modifiers
        modifiers = [
            "fresh", "dried", "ground", "whole", "chopped", "diced",
            "minced", "sliced", "shredded", "grated", "crushed",
            "large", "medium", "small", "ripe", "raw", "cooked",
        ]
        for mod in modifiers:
            name = name.replace(f"{mod} ", "")

        # Normalize common variations
        normalizations = {
            "garlic cloves": "garlic",
            "cloves garlic": "garlic",
            "clove garlic": "garlic",
            "onions": "onion",
            "tomatoes": "tomato",
            "potatoes": "potato",
            "carrots": "carrot",
            "peppers": "pepper",
            "eggs": "egg",
            "lemons": "lemon",
            "limes": "lime",
        }

        for old, new in normalizations.items():
            if old in name:
                name = name.replace(old, new)

        return name.strip()

    def _normalize_unit(self, unit: str) -> str:
        """Normalize a unit for aggregation."""
        unit = unit.lower().strip()

        # Normalize common unit variations
        unit_map = {
            "tablespoon": "tbsp",
            "tablespoons": "tbsp",
            "tbsps": "tbsp",
            "teaspoon": "tsp",
            "teaspoons": "tsp",
            "tsps": "tsp",
            "cups": "cup",
            "ounce": "oz",
            "ounces": "oz",
            "pound": "lb",
            "pounds": "lb",
            "lbs": "lb",
            "clove": "cloves",
            "piece": "each",
            "pieces": "each",
            "": "each",
        }

        return unit_map.get(unit, unit)

    def _infer_category(self, ingredient_name: str) -> str:
        """Infer the category of an ingredient from its name."""
        name_lower = ingredient_name.lower()

        for category, data in self.categories.items():
            items = data.get("items", [])
            for item in items:
                if item.lower() in name_lower or name_lower in item.lower():
                    return category

        return "pantry"  # Default to pantry

    def _assign_store(self, name: str, category: str, quantity: float) -> str:
        """
        Assign the best store for an ingredient based on category and quantity.

        Args:
            name: Ingredient name
            category: Ingredient category
            quantity: Total quantity needed

        Returns:
            Store identifier
        """
        # Check category preferences first
        category_data = self.categories.get(category, {})
        preferred = category_data.get("preferred_store")
        bulk_store = category_data.get("bulk_store")

        # Check if this should go to Costco based on quantity thresholds
        costco_config = self.stores.get("costco", {})
        bulk_thresholds = costco_config.get("bulk_thresholds", {})

        # Check meat threshold
        if category == "meat":
            meat_threshold = bulk_thresholds.get("meat_lbs", 2.0)
            if quantity >= meat_threshold:
                return "costco"

        # Check cheese threshold
        if category == "cheese":
            cheese_threshold = bulk_thresholds.get("cheese_lbs", 1.0)
            if quantity >= cheese_threshold:
                return "costco"

        # Check pantry bulk threshold
        if category == "pantry":
            pantry_threshold = bulk_thresholds.get("pantry_items", 3)
            if quantity >= pantry_threshold:
                return bulk_store or "costco"

        # Use preferred store for category
        if preferred:
            return preferred

        # Check each store's priority categories
        for store_id, store_data in self.stores.items():
            if category in store_data.get("priority_categories", []):
                return store_id

        # Default to Meijer (cost-effective default)
        return "meijer"

    def get_list_by_store(self, grocery_list: GroceryList) -> dict[str, list[GroceryItem]]:
        """
        Group grocery list items by store.

        Args:
            grocery_list: The grocery list

        Returns:
            Dictionary mapping store ID to list of items
        """
        by_store = defaultdict(list)
        for item in grocery_list.items:
            by_store[item.store].append(item)

        # Sort items within each store by category then name
        for store in by_store:
            by_store[store].sort(key=lambda x: (x.category, x.name))

        return dict(by_store)

    def get_store_summary(self, grocery_list: GroceryList) -> dict[str, dict]:
        """
        Get a summary of items per store.

        Args:
            grocery_list: The grocery list

        Returns:
            Dictionary with store summaries
        """
        store_names = {
            "meijer": "Meijer",
            "trader_joes": "Trader Joe's",
            "costco": "Costco",
            "buschs": "Busch's",
        }

        by_store = self.get_list_by_store(grocery_list)

        summaries = {}
        for store_id, items in by_store.items():
            summaries[store_id] = {
                "name": store_names.get(store_id, store_id.title()),
                "item_count": len(items),
                "categories": list(set(item.category for item in items)),
            }

        return summaries

    def format_list_text(self, grocery_list: GroceryList) -> str:
        """
        Format the grocery list as plain text.

        Args:
            grocery_list: The grocery list

        Returns:
            Formatted text
        """
        store_names = {
            "meijer": "Meijer",
            "trader_joes": "Trader Joe's",
            "costco": "Costco",
            "buschs": "Busch's",
        }

        by_store = self.get_list_by_store(grocery_list)
        lines = [f"Grocery List - Week of {grocery_list.week_start}", ""]

        # Order stores
        store_order = ["trader_joes", "costco", "buschs", "meijer"]
        for store_id in store_order:
            if store_id not in by_store:
                continue

            items = by_store[store_id]
            store_name = store_names.get(store_id, store_id.title())

            lines.append(f"--- {store_name} ({len(items)} items) ---")

            for item in items:
                qty_str = self._format_quantity(item.quantity, item.unit)
                check = "[x]" if item.checked else "[ ]"
                lines.append(f"{check} {item.name} ({qty_str})")

            lines.append("")

        return "\n".join(lines)

    def _format_quantity(self, quantity: float, unit: str) -> str:
        """Format quantity and unit for display."""
        if quantity == 0:
            return unit if unit else "to taste"

        # Format nicely (remove .0 for whole numbers)
        if quantity == int(quantity):
            qty_str = str(int(quantity))
        else:
            qty_str = f"{quantity:.1f}".rstrip("0").rstrip(".")

        if unit and unit != "each":
            return f"{qty_str} {unit}"
        return qty_str

    def update_item_store(
        self,
        grocery_list: GroceryList,
        item_name: str,
        new_store: str,
    ) -> GroceryList:
        """
        Update the store assignment for an item.

        Args:
            grocery_list: The grocery list
            item_name: Name of the item to update
            new_store: New store to assign

        Returns:
            Updated GroceryList
        """
        for item in grocery_list.items:
            if item.name.lower() == item_name.lower():
                item.store = new_store
                break

        return grocery_list
