"""
Cloud Functions entry points.
This file exposes the function handlers for Google Cloud Functions deployment.
"""

# Import the HTTP handlers from our functions modules
from src.functions.weekly_planner import generate_weekly_plan
from src.functions.feedback_prompt import prompt_meal_feedback, weekly_feedback_summary
from src.functions.grocery_generator import generate_grocery_list, sync_grocery_to_tasks

# Export all functions for Cloud Functions
__all__ = [
    'generate_weekly_plan',
    'prompt_meal_feedback',
    'weekly_feedback_summary',
    'generate_grocery_list',
    'sync_grocery_to_tasks',
]
