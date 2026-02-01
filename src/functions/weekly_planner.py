"""
Cloud Function for weekly meal plan generation.
Triggered by Cloud Scheduler every week (e.g., Saturday morning).
"""

import os
import functions_framework
from slack_sdk import WebClient

from src.integrations.firestore_client import FirestoreClient
from src.integrations.claude_client import ClaudeClient
from src.core.meal_planner import MealPlanner
from src.bot.slack_utils import format_meal_plan


@functions_framework.http
def generate_weekly_plan(request):
    """
    HTTP Cloud Function to generate weekly meal plan.

    Triggered by Cloud Scheduler.
    """
    # Initialize clients
    db = FirestoreClient()
    claude = ClaudeClient()
    slack = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))
    planner = MealPlanner(firestore_client=db, claude_client=claude)

    # Get preferences
    prefs = db.get_preferences()

    # Check if bootstrap is complete
    if not prefs.bootstrap_complete:
        return "Bootstrap not complete", 200

    # Check for sufficient recipes
    recipes = db.get_all_recipes(approved_only=True)
    if len(recipes) < 7:
        return f"Insufficient recipes: {len(recipes)}", 200

    # Check if there's already a pending plan
    pending = db.get_pending_meal_plan()
    if pending:
        return "Plan already pending", 200

    try:
        # Generate the plan
        plan = planner.generate_weekly_plan()
        plan_id = db.save_meal_plan(plan)
        plan.id = plan_id

        # Get explanation
        explanation = planner.get_plan_explanation(plan)
        summary = planner.get_plan_summary(plan)

        summary_text = (
            f"_{summary['kid_friendly_meals']}/{summary['total_meals']} kid-friendly meals, "
            f"{summary['quick_meals']} quick meals_"
        )

        # Post to Slack
        if prefs.planning_channel_id:
            parents = db.get_parents()
            parent_mention = " ".join([f"<@{p.slack_user_id}>" for p in parents])

            slack.chat_postMessage(
                channel=prefs.planning_channel_id,
                text=f"Time to plan next week's meals! {parent_mention}",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*Time to plan next week's meals!* {parent_mention}\n\n"
                                f"{explanation}\n\n{summary_text}"
                            ),
                        },
                    },
                    *format_meal_plan(plan, show_actions=True)["blocks"],
                ],
            )

        return f"Plan generated: {plan_id}", 200

    except Exception as e:
        return f"Error: {str(e)}", 500


@functions_framework.cloud_event
def generate_weekly_plan_pubsub(cloud_event):
    """
    Pub/Sub triggered version for Cloud Scheduler.
    """
    # Reuse HTTP function logic
    class MockRequest:
        pass

    return generate_weekly_plan(MockRequest())
