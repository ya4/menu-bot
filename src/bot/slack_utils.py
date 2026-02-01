"""
Slack utility functions for message formatting and interactions.
"""

from typing import Optional

from src.integrations.firestore_client import Recipe, MealPlan, GroceryList, GroceryItem


def format_recipe_preview(recipe: Recipe, show_actions: bool = True) -> dict:
    """
    Format a recipe for Slack display with optional action buttons.

    Args:
        recipe: Recipe to format
        show_actions: Whether to include action buttons

    Returns:
        Slack message payload
    """
    # Build time string
    time_parts = []
    if recipe.prep_time_min:
        time_parts.append(f"Prep: {recipe.prep_time_min}min")
    if recipe.cook_time_min:
        time_parts.append(f"Cook: {recipe.cook_time_min}min")
    time_str = " | ".join(time_parts) if time_parts else "Time not specified"

    # Format ingredients preview
    ing_preview = ", ".join([i.name for i in recipe.ingredients[:5]])
    if len(recipe.ingredients) > 5:
        ing_preview += f", +{len(recipe.ingredients) - 5} more"

    # Build tags string
    tags_str = " ".join([f"`{tag}`" for tag in recipe.tags[:4]]) if recipe.tags else ""

    # Kid-friendly indicator
    kf_indicator = ""
    if recipe.kid_friendly_score >= 0.7:
        kf_indicator = " (Kid favorite!)"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{recipe.name}{kf_indicator}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Time:* {time_str}"},
                {"type": "mrkdwn", "text": f"*Servings:* {recipe.servings}"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Ingredients:* {ing_preview}",
            },
        },
    ]

    if tags_str:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": tags_str}],
        })

    if show_actions and recipe.id:
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Save Recipe"},
                    "style": "primary",
                    "action_id": "recipe_save",
                    "value": recipe.id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Edit"},
                    "action_id": "recipe_edit",
                    "value": recipe.id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Discard"},
                    "style": "danger",
                    "action_id": "recipe_discard",
                    "value": recipe.id,
                },
            ],
        })

    return {
        "text": f"Recipe: {recipe.name}",
        "blocks": blocks,
    }


def format_meal_plan(meal_plan: MealPlan, show_actions: bool = True) -> dict:
    """
    Format a meal plan for Slack display.

    Args:
        meal_plan: MealPlan to format
        show_actions: Whether to include action buttons

    Returns:
        Slack message payload
    """
    # Build meals list
    meals_text = ""
    for meal in meal_plan.meals:
        day = meal.day_of_week[:3]  # Mon, Tue, etc.
        meals_text += f"*{day}:* {meal.recipe_name}\n"

    status_emoji = {
        "draft": "",
        "pending_approval": " (Awaiting Approval)",
        "active": "",
        "completed": "",
    }

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Meal Plan - Week of {meal_plan.week_start}{status_emoji.get(meal_plan.status, '')}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": meals_text,
            },
        },
    ]

    if show_actions and meal_plan.id and meal_plan.status == "pending_approval":
        blocks.append({
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": "meal_plan_approve",
                    "value": meal_plan.id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Regenerate"},
                    "action_id": "meal_plan_regenerate",
                    "value": meal_plan.id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Swap Meals"},
                    "action_id": "meal_plan_swap",
                    "value": meal_plan.id,
                },
            ],
        })

    return {
        "text": f"Meal Plan - Week of {meal_plan.week_start}",
        "blocks": blocks,
    }


def format_grocery_list(
    grocery_list: GroceryList,
    items_by_store: dict[str, list[GroceryItem]],
    show_actions: bool = True,
) -> dict:
    """
    Format a grocery list for Slack display.

    Args:
        grocery_list: GroceryList to format
        items_by_store: Items grouped by store
        show_actions: Whether to include action buttons

    Returns:
        Slack message payload
    """
    store_names = {
        "meijer": "Meijer",
        "trader_joes": "Trader Joe's",
        "costco": "Costco",
        "buschs": "Busch's",
    }

    store_order = ["trader_joes", "costco", "buschs", "meijer"]

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Grocery List - Week of {grocery_list.week_start}",
            },
        },
    ]

    for store_id in store_order:
        if store_id not in items_by_store:
            continue

        items = items_by_store[store_id]
        store_name = store_names.get(store_id, store_id.title())

        # Format items
        items_text = f"*{store_name}* ({len(items)} items)\n"
        for item in items[:10]:  # Limit to 10 per store in preview
            qty_str = _format_quantity(item.quantity, item.unit)
            checkbox = ":white_check_mark:" if item.checked else ":white_large_square:"
            items_text += f"{checkbox} {item.name} ({qty_str})\n"

        if len(items) > 10:
            items_text += f"_+{len(items) - 10} more items..._\n"

        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": items_text},
        })

    if show_actions and grocery_list.id:
        action_elements = []

        if grocery_list.status == "pending_approval":
            action_elements.extend([
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve"},
                    "style": "primary",
                    "action_id": "grocery_list_approve",
                    "value": grocery_list.id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Edit Items"},
                    "action_id": "grocery_list_edit",
                    "value": grocery_list.id,
                },
            ])

        if grocery_list.status == "approved":
            action_elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Send to Google Tasks"},
                "style": "primary",
                "action_id": "grocery_list_sync_tasks",
                "value": grocery_list.id,
            })

        if action_elements:
            blocks.append({
                "type": "actions",
                "elements": action_elements,
            })

    return {
        "text": f"Grocery List - Week of {grocery_list.week_start}",
        "blocks": blocks,
    }


def format_rating_prompt(recipe_name: str, recipe_id: str) -> dict:
    """
    Format a rating prompt for a meal.

    Args:
        recipe_name: Name of the recipe
        recipe_id: ID of the recipe

    Returns:
        Slack message payload
    """
    return {
        "text": f"How was {recipe_name}?",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"How was *{recipe_name}*?",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Adults* - Rate 1-5:",
                },
                "accessory": {
                    "type": "static_select",
                    "action_id": f"rating_adult_{recipe_id}",
                    "placeholder": {"type": "plain_text", "text": "Select rating"},
                    "options": [
                        {"text": {"type": "plain_text", "text": f"{i} star{'s' if i > 1 else ''}"}, "value": str(i)}
                        for i in range(1, 6)
                    ],
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Kids* - How was it?",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Yummy!"},
                        "action_id": f"rating_kid_good_{recipe_id}",
                        "value": "5",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "It's okay"},
                        "action_id": f"rating_kid_ok_{recipe_id}",
                        "value": "3",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Yucky"},
                        "action_id": f"rating_kid_bad_{recipe_id}",
                        "value": "1",
                    },
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Make again?*",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Yes"},
                        "style": "primary",
                        "action_id": f"rating_repeat_yes_{recipe_id}",
                        "value": "yes",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "No"},
                        "style": "danger",
                        "action_id": f"rating_repeat_no_{recipe_id}",
                        "value": "no",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Maybe"},
                        "action_id": f"rating_repeat_maybe_{recipe_id}",
                        "value": "maybe",
                    },
                ],
            },
        ],
    }


def format_bootstrap_welcome() -> dict:
    """Format the initial bootstrap welcome message."""
    return {
        "text": "Welcome to Menu Bot!",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Welcome to Menu Bot!",
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "I'll help your family plan meals and create grocery lists. "
                        "Let's get started by setting up your family!\n\n"
                        "*First, tell me about your family members.* "
                        "Who are the parents, and who are the kids?"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "Use `/menu-setup` to configure family members, "
                        "or just mention me and I'll walk you through it!"
                    ),
                },
            },
        ],
    }


def _format_quantity(quantity: float, unit: str) -> str:
    """Format quantity and unit for display."""
    if quantity == 0:
        return unit if unit else "to taste"

    if quantity == int(quantity):
        qty_str = str(int(quantity))
    else:
        qty_str = f"{quantity:.1f}".rstrip("0").rstrip(".")

    if unit and unit != "each":
        return f"{qty_str} {unit}"
    return qty_str
