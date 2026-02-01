"""Tests for the grocery optimizer."""

import pytest
from unittest.mock import MagicMock, patch

from src.integrations.firestore_client import (
    Recipe,
    Ingredient,
    MealPlan,
    MealPlanEntry,
    GroceryItem,
)
from src.core.grocery_optimizer import GroceryOptimizer


@pytest.fixture
def optimizer():
    """Create a grocery optimizer with mocked Firestore."""
    with patch('src.core.grocery_optimizer.FirestoreClient'):
        opt = GroceryOptimizer()
        opt.db = MagicMock()
        return opt


@pytest.fixture
def sample_recipes():
    """Create sample recipes for testing."""
    return [
        Recipe(
            id="recipe1",
            name="Spaghetti",
            ingredients=[
                Ingredient(name="pasta", quantity=1, unit="lb", category="pantry"),
                Ingredient(name="tomatoes", quantity=2, unit="lb", category="produce"),
                Ingredient(name="ground beef", quantity=1, unit="lb", category="meat"),
                Ingredient(name="garlic", quantity=3, unit="cloves", category="produce"),
            ],
        ),
        Recipe(
            id="recipe2",
            name="Tacos",
            ingredients=[
                Ingredient(name="ground beef", quantity=1.5, unit="lb", category="meat"),
                Ingredient(name="tortillas", quantity=1, unit="package", category="bread"),
                Ingredient(name="cheese", quantity=0.5, unit="lb", category="cheese"),
                Ingredient(name="tomatoes", quantity=1, unit="lb", category="produce"),
            ],
        ),
    ]


@pytest.fixture
def sample_meal_plan():
    """Create a sample meal plan."""
    return MealPlan(
        id="plan1",
        week_start="2024-01-08",
        meals=[
            MealPlanEntry(date="2024-01-08", day_of_week="Monday", recipe_id="recipe1", recipe_name="Spaghetti"),
            MealPlanEntry(date="2024-01-09", day_of_week="Tuesday", recipe_id="recipe2", recipe_name="Tacos"),
        ],
    )


class TestGroceryOptimizer:
    """Tests for GroceryOptimizer class."""

    def test_normalize_ingredient_name(self, optimizer):
        """Test ingredient name normalization."""
        assert optimizer._normalize_ingredient_name("Fresh Tomatoes") == "tomato"
        assert optimizer._normalize_ingredient_name("garlic cloves") == "garlic"
        assert optimizer._normalize_ingredient_name("CHOPPED ONIONS") == "onion"
        assert optimizer._normalize_ingredient_name("  ground beef  ") == "ground beef"

    def test_normalize_unit(self, optimizer):
        """Test unit normalization."""
        assert optimizer._normalize_unit("tablespoons") == "tbsp"
        assert optimizer._normalize_unit("Teaspoon") == "tsp"
        assert optimizer._normalize_unit("pounds") == "lb"
        assert optimizer._normalize_unit("cups") == "cup"
        assert optimizer._normalize_unit("") == "each"

    def test_infer_category(self, optimizer):
        """Test category inference from ingredient names."""
        assert optimizer._infer_category("tomatoes") == "produce"
        assert optimizer._infer_category("chicken breast") == "meat"
        assert optimizer._infer_category("cheddar cheese") == "cheese"
        assert optimizer._infer_category("olive oil") == "pantry"
        assert optimizer._infer_category("basil") == "fresh_herbs"

    def test_assign_store_produce(self, optimizer):
        """Test that produce goes to Trader Joe's."""
        store = optimizer._assign_store("tomatoes", "produce", 2)
        assert store == "trader_joes"

    def test_assign_store_bulk_meat(self, optimizer):
        """Test that bulk meat goes to Costco."""
        # Under threshold - should go to Meijer
        store = optimizer._assign_store("chicken", "meat", 1.5)
        assert store == "meijer"

        # Over threshold - should go to Costco
        store = optimizer._assign_store("chicken", "meat", 3.0)
        assert store == "costco"

    def test_assign_store_default(self, optimizer):
        """Test that unknown items default to Meijer."""
        store = optimizer._assign_store("random item", "unknown", 1)
        assert store == "meijer"

    def test_aggregate_ingredients(self, optimizer, sample_recipes):
        """Test ingredient aggregation across recipes."""
        aggregated = optimizer._aggregate_ingredients(sample_recipes)

        # Ground beef should be combined (1 + 1.5 = 2.5 lbs)
        beef_key = ("ground beef", "lb")
        assert beef_key in aggregated
        assert aggregated[beef_key][0] == 2.5  # Total quantity
        assert len(aggregated[beef_key][1]) == 2  # From 2 recipes

        # Tomatoes should be combined (2 + 1 = 3 lbs)
        tomato_key = ("tomato", "lb")
        assert tomato_key in aggregated
        assert aggregated[tomato_key][0] == 3.0

    def test_generate_grocery_list(self, optimizer, sample_recipes, sample_meal_plan):
        """Test full grocery list generation."""
        optimizer.db.get_recipes_by_ids.return_value = sample_recipes

        grocery_list = optimizer.generate_grocery_list(sample_meal_plan)

        assert grocery_list.meal_plan_id == "plan1"
        assert grocery_list.week_start == "2024-01-08"
        assert len(grocery_list.items) > 0

        # Check items are assigned to correct stores
        item_stores = {item.name: item.store for item in grocery_list.items}
        assert item_stores.get("tomato") == "trader_joes"  # Produce
        assert item_stores.get("cheese") == "trader_joes"  # Cheese

    def test_get_list_by_store(self, optimizer):
        """Test grouping items by store."""
        items = [
            GroceryItem(name="tomatoes", quantity=2, unit="lb", store="trader_joes", category="produce"),
            GroceryItem(name="pasta", quantity=1, unit="lb", store="meijer", category="pantry"),
            GroceryItem(name="cheese", quantity=0.5, unit="lb", store="trader_joes", category="cheese"),
        ]

        from src.integrations.firestore_client import GroceryList
        grocery_list = GroceryList(id="test", meal_plan_id="plan1", week_start="2024-01-08", items=items)

        by_store = optimizer.get_list_by_store(grocery_list)

        assert "trader_joes" in by_store
        assert len(by_store["trader_joes"]) == 2
        assert "meijer" in by_store
        assert len(by_store["meijer"]) == 1

    def test_format_list_text(self, optimizer):
        """Test text formatting of grocery list."""
        items = [
            GroceryItem(name="tomatoes", quantity=2, unit="lb", store="trader_joes", category="produce"),
            GroceryItem(name="pasta", quantity=1, unit="lb", store="meijer", category="pantry"),
        ]

        from src.integrations.firestore_client import GroceryList
        grocery_list = GroceryList(id="test", meal_plan_id="plan1", week_start="2024-01-08", items=items)

        text = optimizer.format_list_text(grocery_list)

        assert "Trader Joe's" in text
        assert "Meijer" in text
        assert "tomatoes" in text
        assert "pasta" in text
