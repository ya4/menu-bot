"""Tests for the seasonal helper."""

import pytest
from datetime import datetime
from unittest.mock import patch, mock_open

from src.core.seasonal import SeasonalHelper


# Sample config for testing
SAMPLE_CONFIG = """
region: "ann_arbor_mi"
timezone: "America/Detroit"

seasons:
  spring:
    months: [4, 5, 6]
    peak_produce:
      - name: "asparagus"
        months: [4, 5, 6]
      - name: "strawberries"
        months: [5, 6]

  summer:
    months: [7, 8, 9]
    peak_produce:
      - name: "tomatoes"
        months: [7, 8, 9]
      - name: "corn"
        months: [7, 8, 9]
      - name: "zucchini"
        months: [7, 8, 9]

  fall:
    months: [10, 11]
    peak_produce:
      - name: "apples"
        months: [9, 10, 11]
      - name: "pumpkin"
        months: [10, 11]

  winter:
    months: [12, 1, 2, 3]
    peak_produce:
      - name: "potatoes"
        months: [12, 1, 2, 3]
    notes: "Focus on storage vegetables"

seasonal_meal_suggestions:
  spring:
    - "Light salads"
    - "Grilled dishes"
  summer:
    - "Fresh tomato salads"
    - "Grilled corn"
  fall:
    - "Apple dishes"
    - "Squash soups"
  winter:
    - "Slow cooker meals"
    - "Hearty soups"
"""


@pytest.fixture
def seasonal_helper():
    """Create a seasonal helper with mocked config."""
    with patch("builtins.open", mock_open(read_data=SAMPLE_CONFIG)):
        return SeasonalHelper(config_path="/fake/path.yaml")


class TestSeasonalHelper:
    """Tests for SeasonalHelper class."""

    def test_get_current_season_spring(self, seasonal_helper):
        """Test spring season detection."""
        april = datetime(2024, 4, 15)
        assert seasonal_helper.get_current_season(april) == "spring"

        may = datetime(2024, 5, 1)
        assert seasonal_helper.get_current_season(may) == "spring"

    def test_get_current_season_summer(self, seasonal_helper):
        """Test summer season detection."""
        july = datetime(2024, 7, 15)
        assert seasonal_helper.get_current_season(july) == "summer"

        august = datetime(2024, 8, 20)
        assert seasonal_helper.get_current_season(august) == "summer"

    def test_get_current_season_fall(self, seasonal_helper):
        """Test fall season detection."""
        october = datetime(2024, 10, 15)
        assert seasonal_helper.get_current_season(october) == "fall"

    def test_get_current_season_winter(self, seasonal_helper):
        """Test winter season detection."""
        january = datetime(2024, 1, 15)
        assert seasonal_helper.get_current_season(january) == "winter"

        december = datetime(2024, 12, 25)
        assert seasonal_helper.get_current_season(december) == "winter"

    def test_get_peak_produce_summer(self, seasonal_helper):
        """Test getting peak produce for summer."""
        august = datetime(2024, 8, 15)
        produce = seasonal_helper.get_peak_produce(august)

        names = [p["name"] for p in produce]
        assert "tomatoes" in names
        assert "corn" in names
        assert "zucchini" in names
        assert "asparagus" not in names  # Spring only

    def test_get_peak_produce_spring(self, seasonal_helper):
        """Test getting peak produce for spring."""
        may = datetime(2024, 5, 15)
        produce = seasonal_helper.get_peak_produce(may)

        names = [p["name"] for p in produce]
        assert "asparagus" in names
        assert "strawberries" in names

    def test_is_in_season_true(self, seasonal_helper):
        """Test in-season detection for seasonal produce."""
        august = datetime(2024, 8, 15)
        assert seasonal_helper.is_in_season("tomatoes", august) is True
        assert seasonal_helper.is_in_season("fresh corn", august) is True

    def test_is_in_season_false(self, seasonal_helper):
        """Test in-season detection for out-of-season produce."""
        january = datetime(2024, 1, 15)
        assert seasonal_helper.is_in_season("tomatoes", january) is False
        assert seasonal_helper.is_in_season("asparagus", january) is False

    def test_get_seasonal_score(self, seasonal_helper):
        """Test seasonal score calculation."""
        august = datetime(2024, 8, 15)

        # All in-season ingredients
        in_season = ["tomatoes", "corn", "zucchini"]
        score = seasonal_helper.get_seasonal_score(in_season, august)
        assert score == 1.0

        # No produce ingredients
        no_produce = ["chicken", "pasta", "rice"]
        score = seasonal_helper.get_seasonal_score(no_produce, august)
        assert score == 0.5  # Neutral

        # Empty list
        score = seasonal_helper.get_seasonal_score([], august)
        assert score == 0.5  # Neutral

    def test_get_meal_suggestions(self, seasonal_helper):
        """Test getting seasonal meal suggestions."""
        august = datetime(2024, 8, 15)
        suggestions = seasonal_helper.get_meal_suggestions(august)

        assert "Fresh tomato salads" in suggestions
        assert "Grilled corn" in suggestions

    def test_get_seasonal_context(self, seasonal_helper):
        """Test getting full seasonal context."""
        august = datetime(2024, 8, 15)
        context = seasonal_helper.get_seasonal_context(august)

        assert context["season"] == "summer"
        assert context["month"] == "August"
        assert "tomatoes" in context["peak_produce_names"]
        assert len(context["meal_suggestions"]) > 0

    def test_suggest_seasonal_swaps(self, seasonal_helper):
        """Test suggesting seasonal swaps."""
        january = datetime(2024, 1, 15)

        # Out of season ingredients
        ingredients = ["tomatoes", "fresh corn"]
        swaps = seasonal_helper.suggest_seasonal_swaps(ingredients, january)

        # Should suggest swaps for tomatoes
        tomato_swap = next((s for s in swaps if "tomato" in s["original"].lower()), None)
        assert tomato_swap is not None
        assert len(tomato_swap["suggestions"]) > 0
