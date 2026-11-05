"""Publication workflow for courses.

This module is the single source of truth for which status changes are allowed.
It is enforced in three places so that no single mistake can publish a course:

1. here, in Python, before any change is saved;
2. in the store layer (``set_course_status`` always calls ``check_transition``);
3. in the database (a trigger blocks ``published`` unless the old status was
   ``approved``; see ``app/store/local.py`` and ``supabase/migrations``).

Nothing is ever published automatically. Imports, user submissions and sample
data always start as ``draft``. Only an administrator can move a course to
``published``, and only from ``approved``.
"""

DRAFT = "draft"
PENDING_REVIEW = "pending_review"
APPROVED = "approved"
PUBLISHED = "published"
ARCHIVED = "archived"

STATUSES = (DRAFT, PENDING_REVIEW, APPROVED, PUBLISHED, ARCHIVED)

# from-status -> statuses it may move to
TRANSITIONS = {
    DRAFT: (PENDING_REVIEW, ARCHIVED),
    PENDING_REVIEW: (APPROVED, DRAFT, ARCHIVED),
    APPROVED: (PUBLISHED, DRAFT, ARCHIVED),
    PUBLISHED: (DRAFT, ARCHIVED),  # moving to draft is "unpublish"
    ARCHIVED: (DRAFT,),
}

STATUS_LABELS = {
    DRAFT: "Draft",
    PENDING_REVIEW: "Pending review",
    APPROVED: "Approved",
    PUBLISHED: "Published",
    ARCHIVED: "Archived",
}

STATUS_HELP = {
    DRAFT: "Private. Only administrators can see it.",
    PENDING_REVIEW: "Private. Waiting for an administrator's decision.",
    APPROVED: "Private. Checked and ready, but not live until you press Publish.",
    PUBLISHED: "Live on the public site.",
    ARCHIVED: "Private. Retired from the catalogue but kept for the record.",
}

# (from, to) -> button label shown to the administrator
ACTION_LABELS = {
    (DRAFT, PENDING_REVIEW): "Submit for review",
    (PENDING_REVIEW, APPROVED): "Approve (does not publish)",
    (PENDING_REVIEW, DRAFT): "Send back to draft",
    (APPROVED, PUBLISHED): "Publish now",
    (APPROVED, DRAFT): "Return to draft",
    (PUBLISHED, DRAFT): "Unpublish (back to draft)",
    (DRAFT, ARCHIVED): "Archive",
    (PENDING_REVIEW, ARCHIVED): "Archive",
    (APPROVED, ARCHIVED): "Archive",
    (PUBLISHED, ARCHIVED): "Archive (removes from site)",
    (ARCHIVED, DRAFT): "Restore to draft",
}


class WorkflowError(ValueError):
    """Raised when a status change is not allowed."""


def is_valid_status(value):
    return value in STATUSES


def allowed_targets(current):
    return TRANSITIONS.get(current, ())


def check_transition(current, target):
    """Return silently if the change is allowed, otherwise raise WorkflowError."""
    if not is_valid_status(target):
        raise WorkflowError("Unknown status.")
    if not is_valid_status(current):
        raise WorkflowError("The course has an unknown status.")
    if current == target:
        raise WorkflowError(f"The course is already {STATUS_LABELS[current].lower()}.")
    if target not in TRANSITIONS[current]:
        if target == PUBLISHED:
            raise WorkflowError("A course must be approved before it can be published.")
        raise WorkflowError(
            f"A {STATUS_LABELS[current].lower()} course cannot move to {STATUS_LABELS[target].lower()}."
        )


def actions_for(current):
    """List of (target_status, button_label) the administrator may take now."""
    return [(target, ACTION_LABELS.get((current, target), STATUS_LABELS[target])) for target in allowed_targets(current)]


def is_public(status):
    return status == PUBLISHED
