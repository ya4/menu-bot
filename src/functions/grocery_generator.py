"""
Cloud Function for automatic grocery list generation.
Triggered when a meal plan is approved, or on a schedule.
"""

import os
import functions_framework
from slack_sdk import WebClient

from src.integrations.firestore_client import FirestoreClient
from src.core.grocery_optimizer import GroceryOptimizer
from src.bot.slack_utils import format_grocery_list


@functions_framework.http
def generate_grocery_list(request):
    """
    HTTP Cloud Function to generate grocery list from approved meal plan.

    Can be triggered by Cloud Scheduler or via HTTP after meal plan approval.
    """
    # Initialize clients
    db = FirestoreClient()
    slack = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))
    optimizer = GroceryOptimizer(firestore_client=db)

    # Get preferences
    prefs = db.get_preferences()

    if not prefs.bootstrap_complete:
        return "Bootstrap not complete", 200

    # Get current active meal plan
    meal_plan = db.get_current_meal_plan()
    if not meal_plan:
        return "No active meal plan", 200

    # Check if grocery list already exists for this plan
    existing = db.get_grocery_list_for_plan(meal_plan.id)
    if existing:
        return f"Grocery list already exists: {existing.id}", 200

    try:
        # Generate the grocery list
        grocery_list = optimizer.generate_grocery_list(meal_plan)
        list_id = db.save_grocery_list(grocery_list)
        grocery_list.id = list_id

        # Get items by store for display
        items_by_store = optimizer.get_list_by_store(grocery_list)
        summary = optimizer.get_store_summary(grocery_list)

        # Build summary text
        summary_parts = []
        for store_id, info in summary.items():
            summary_parts.append(f"{info['name']}: {info['item_count']} items")
        summary_text = " | ".join(summary_parts)

        # Post to Slack
        if prefs.planning_channel_id:
            parents = db.get_parents()
            parent_mention = " ".join([f"<@{p.slack_user_id}>" for p in parents])

            slack.chat_postMessage(
                channel=prefs.planning_channel_id,
                text=f"Grocery list ready! {parent_mention}",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*Grocery list ready for approval!* {parent_mention}\n\n"
                                f"_{summary_text}_"
                            ),
                        },
                    },
                    *format_grocery_list(grocery_list, items_by_store, show_actions=True)["blocks"],
                ],
            )

        return f"Grocery list generated: {list_id}", 200

    except Exception as e:
        return f"Error: {str(e)}", 500


@functions_framework.http
def sync_grocery_to_tasks(request):
    """
    HTTP Cloud Function to sync approved grocery list to Google Tasks.

    Triggered after grocery list is approved.
    """
    from src.integrations.google_tasks import GoogleTasksClient

    # Get the grocery list ID from request
    request_json = request.get_json(silent=True)
    list_id = request_json.get("list_id") if request_json else None

    db = FirestoreClient()
    google_tasks = GoogleTasksClient()
    slack = WebClient(token=os.environ.get("SLACK_BOT_TOKEN"))

    prefs = db.get_preferences()

    # Get the grocery list
    if list_id:
        grocery_list = db.get_grocery_list(list_id)
    else:
        # Find the most recent approved list
        meal_plan = db.get_current_meal_plan()
        if meal_plan:
            grocery_list = db.get_grocery_list_for_plan(meal_plan.id)
        else:
            grocery_list = None

    if not grocery_list:
        return "No grocery list found", 200

    if grocery_list.status != "approved":
        return "Grocery list not approved yet", 200

    # Get parents with Google Tasks linked
    parents = db.get_parents()
    synced_count = 0

    for parent in parents:
        if parent.google_tasks_linked and parent.google_refresh_token:
            try:
                tasks_id = google_tasks.sync_grocery_list(
                    refresh_token=parent.google_refresh_token,
                    grocery_list=grocery_list,
                )

                # Update grocery list with tasks ID (use first parent's)
                if not grocery_list.google_tasks_id:
                    grocery_list.google_tasks_id = tasks_id
                    db.save_grocery_list(grocery_list)

                synced_count += 1

            except Exception as e:
                # Log but continue with other parents
                print(f"Failed to sync for {parent.name}: {e}")

    if synced_count > 0 and prefs.planning_channel_id:
        slack.chat_postMessage(
            channel=prefs.planning_channel_id,
            text=f"Grocery list synced to Google Tasks for {synced_count} family member(s)!",
        )

    return f"Synced to {synced_count} accounts", 200


@functions_framework.cloud_event
def generate_grocery_list_pubsub(cloud_event):
    """Pub/Sub triggered version."""
    class MockRequest:
        pass
    return generate_grocery_list(MockRequest())
