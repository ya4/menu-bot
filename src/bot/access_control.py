"""
Access control utilities for the Slack bot.
Ensures only parents can approve meal plans and grocery lists.
"""

from functools import wraps
from typing import Callable, Optional

from slack_bolt import Ack, Say
from slack_sdk import WebClient

from src.integrations.firestore_client import FirestoreClient


def get_firestore_client() -> FirestoreClient:
    """Get or create a Firestore client instance."""
    # Could use dependency injection in production
    return FirestoreClient()


def require_parent(func: Callable) -> Callable:
    """
    Decorator to require parent role for an action.
    Sends a friendly message if a non-parent tries to perform the action.
    """
    @wraps(func)
    def wrapper(ack: Ack, body: dict, client: WebClient, say: Say, *args, **kwargs):
        ack()

        # Get user ID from the action
        user_id = body.get("user", {}).get("id")
        if not user_id:
            user_id = body.get("user_id")

        if not user_id:
            say("Sorry, I couldn't identify who you are.")
            return

        # Check if user is a parent
        db = get_firestore_client()
        if not db.is_parent(user_id):
            say(
                "This action requires parent approval. "
                "Ask a parent to approve this for you!"
            )
            return

        return func(ack, body, client, say, *args, **kwargs)

    return wrapper


def require_parent_for_command(func: Callable) -> Callable:
    """
    Decorator for slash commands that require parent role.
    """
    @wraps(func)
    def wrapper(ack: Ack, body: dict, client: WebClient, respond, *args, **kwargs):
        ack()

        user_id = body.get("user_id")

        if not user_id:
            respond("Sorry, I couldn't identify who you are.")
            return

        db = get_firestore_client()
        if not db.is_parent(user_id):
            respond(
                "This command requires parent permissions. "
                "Ask a parent to run this command!"
            )
            return

        return func(ack, body, client, respond, *args, **kwargs)

    return wrapper


def check_parent_status(user_id: str) -> bool:
    """
    Check if a user has parent status.

    Args:
        user_id: Slack user ID

    Returns:
        True if user is a parent
    """
    db = get_firestore_client()
    return db.is_parent(user_id)


def get_user_type(user_id: str) -> str:
    """
    Get the user type (adult/kid) for a Slack user.

    Args:
        user_id: Slack user ID

    Returns:
        "adult", "kid", or "unknown"
    """
    db = get_firestore_client()
    member = db.get_family_member(user_id)
    if member:
        return member.user_type
    return "unknown"


def notify_parents(client: WebClient, message: str, channel: Optional[str] = None):
    """
    Send a notification to all parents.

    Args:
        client: Slack WebClient
        message: Message to send
        channel: Optional channel ID (sends DMs if not provided)
    """
    db = get_firestore_client()
    parents = db.get_parents()

    if channel:
        # Post to channel and mention parents
        parent_mentions = " ".join([f"<@{p.slack_user_id}>" for p in parents])
        client.chat_postMessage(
            channel=channel,
            text=f"{parent_mentions} {message}",
        )
    else:
        # Send DMs to each parent
        for parent in parents:
            try:
                # Open DM channel
                response = client.conversations_open(users=[parent.slack_user_id])
                dm_channel = response["channel"]["id"]

                client.chat_postMessage(
                    channel=dm_channel,
                    text=message,
                )
            except Exception:
                pass  # Silently fail for DM issues


def format_approval_required_message(item_type: str, item_id: str) -> dict:
    """
    Format a message with approval buttons.

    Args:
        item_type: "meal_plan" or "grocery_list"
        item_id: ID of the item to approve

    Returns:
        Slack message blocks
    """
    if item_type == "meal_plan":
        text = "A new meal plan is ready for review!"
        action_prefix = "meal_plan"
    else:
        text = "A new grocery list is ready for review!"
        action_prefix = "grocery_list"

    return {
        "text": text,
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{text}*\n_Parent approval required_",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "action_id": f"{action_prefix}_approve",
                        "value": item_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Details"},
                        "action_id": f"{action_prefix}_view",
                        "value": item_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Regenerate"},
                        "style": "danger",
                        "action_id": f"{action_prefix}_regenerate",
                        "value": item_id,
                    },
                ],
            },
        ],
    }
