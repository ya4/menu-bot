"""
Cloud Function for prompting meal feedback.
Triggered daily in the evening to collect ratings for that day's meal.
"""

import os
from datetime import datetime
import functions_framework
from slack_sdk import WebClient

from src.integrations.firestore_client import FirestoreClient
from src.bot.slack_utils import format_rating_prompt


@functions_framework.http
def prompt_meal_feedback(request):
    """
    HTTP Cloud Function to prompt for meal feedback.

    Triggered by Cloud Scheduler daily around dinner time (e.g., 7 PM).
    """
    # Initialize clients
    db = FirestoreClient()
    slack = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))

    # Get preferences
    prefs = db.get_preferences()

    if not prefs.bootstrap_complete or not prefs.planning_channel_id:
        return "Not configured", 200

    # Get current meal plan
    meal_plan = db.get_current_meal_plan()
    if not meal_plan:
        return "No active meal plan", 200

    # Find today's meal
    today = datetime.now().strftime("%Y-%m-%d")
    todays_meal = None

    for meal in meal_plan.meals:
        if meal.date == today:
            todays_meal = meal
            break

    if not todays_meal:
        return "No meal scheduled for today", 200

    # Send rating prompt
    try:
        slack.chat_postMessage(
            channel=prefs.planning_channel_id,
            **format_rating_prompt(todays_meal.recipe_name, todays_meal.recipe_id),
        )
        return f"Feedback prompt sent for: {todays_meal.recipe_name}", 200

    except Exception as e:
        return f"Error: {str(e)}", 500


@functions_framework.http
def weekly_feedback_summary(request):
    """
    HTTP Cloud Function to send weekly feedback summary.

    Triggered by Cloud Scheduler at end of week (e.g., Sunday evening).
    """
    db = FirestoreClient()
    slack = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))

    prefs = db.get_preferences()

    if not prefs.bootstrap_complete or not prefs.planning_channel_id:
        return "Not configured", 200

    # Get current meal plan
    meal_plan = db.get_current_meal_plan()
    if not meal_plan:
        return "No active meal plan", 200

    # Gather ratings for this week's meals
    meal_summaries = []
    for meal in meal_plan.meals:
        avg_ratings = db.get_average_rating(meal.recipe_id)

        adult_text = f"Adults: {avg_ratings['adult_avg']:.1f}/5" if avg_ratings['adult_avg'] else "Adults: -"
        kid_text = f"Kids: {avg_ratings['kid_avg']:.1f}/5" if avg_ratings['kid_avg'] else "Kids: -"

        meal_summaries.append(f"*{meal.recipe_name}*\n  {adult_text} | {kid_text}")

    summary_text = "\n".join(meal_summaries)

    # Send summary
    try:
        slack.chat_postMessage(
            channel=prefs.planning_channel_id,
            text="Weekly meal ratings summary",
            blocks=[
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "This Week's Ratings",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": summary_text if summary_text else "_No ratings collected this week_",
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            "Thanks for your feedback! I'll use this to make "
                            "next week's meal plan even better."
                        ),
                    },
                },
            ],
        )

        # Mark meal plan as completed and feedback collected
        meal_plan.status = "completed"
        meal_plan.feedback_collected = True
        db.save_meal_plan(meal_plan)

        return "Weekly summary sent", 200

    except Exception as e:
        return f"Error: {str(e)}", 500


@functions_framework.cloud_event
def prompt_meal_feedback_pubsub(cloud_event):
    """Pub/Sub triggered version."""
    class MockRequest:
        pass
    return prompt_meal_feedback(MockRequest())


@functions_framework.cloud_event
def weekly_feedback_summary_pubsub(cloud_event):
    """Pub/Sub triggered version."""
    class MockRequest:
        pass
    return weekly_feedback_summary(MockRequest())
