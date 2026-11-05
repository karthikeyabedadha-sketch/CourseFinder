from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import (
    BooleanField,
    DecimalField,
    IntegerField,
    PasswordField,
    RadioField,
    SelectField,
    StringField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Email, EqualTo, Length, NumberRange, Optional, ValidationError

from app.store.constants import COURSE_REPORT_REASONS, DIFFICULTIES, RECOMMENDATION_STATUSES, REVIEW_REPORT_REASONS
from app.utils import is_safe_http_url


class RegisterForm(FlaskForm):
    display_name = StringField("Display name", validators=[DataRequired(), Length(max=80)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, max=128)])


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])


class ContactForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=80)])
    email = StringField("Email", validators=[DataRequired(), Email()])
    message = TextAreaField("Message", validators=[DataRequired(), Length(min=10, max=4000)])


class ReviewForm(FlaskForm):
    rating = RadioField(
        "Rating",
        choices=[
            ("5", "5 — Exceptional"),
            ("4", "4 — Strong"),
            ("3", "3 — Mixed"),
            ("2", "2 — Weak"),
            ("1", "1 — Poor"),
        ],
        validators=[DataRequired()],
    )
    body = TextAreaField("Review", validators=[DataRequired(), Length(min=20, max=4000)])


class CourseForm(FlaskForm):
    title = StringField("Title", validators=[DataRequired(), Length(max=200)])
    official_url = StringField("Official course URL", validators=[DataRequired(), Length(max=500)])
    provider_id = SelectField("Provider", validators=[Optional()], default="", validate_choice=False)
    new_provider_name = StringField("Or add a provider", validators=[Optional(), Length(max=160)])
    new_provider_url = StringField("Provider website", validators=[Optional(), Length(max=500)])
    language = StringField("Course language", validators=[Optional(), Length(max=40)])
    slug = StringField("URL slug (leave blank to keep / auto-generate)", validators=[Optional(), Length(max=120)])
    admin_notes = TextAreaField("Private admin notes (never public)", validators=[Optional(), Length(max=4000)])
    source_url = StringField("Research source URL", validators=[Optional(), Length(max=500)])
    source_checked_at = StringField("Research date (YYYY-MM-DD)", validators=[Optional(), Length(max=10)])
    category_id = SelectField("Category", validators=[DataRequired()])
    subcategory = StringField("Subcategory", validators=[Optional(), Length(max=120)])
    short_description = TextAreaField("Short description", validators=[DataRequired(), Length(max=280)])
    description = TextAreaField("Description", validators=[DataRequired(), Length(max=8000)])
    is_free = SelectField(
        "Pricing",
        choices=[("", "Unknown"), ("true", "Free"), ("false", "Paid")],
        validators=[Optional()],
        default="",
        validate_choice=False,
    )
    price = DecimalField("Price", validators=[Optional(), NumberRange(min=0)])
    currency = StringField("Currency", validators=[Optional(), Length(max=8)], default="USD")
    pricing_notes = TextAreaField("Pricing notes", validators=[Optional(), Length(max=2000)])
    difficulty = SelectField(
        "Difficulty",
        choices=[("", "Unknown")] + [(item, item.replace("-", " ").title()) for item in DIFFICULTIES],
        validators=[Optional()],
        default="",
        validate_choice=False,
    )
    prerequisites = TextAreaField("Prerequisites", validators=[Optional(), Length(max=2000)])
    duration = StringField("Duration", validators=[Optional(), Length(max=200)])
    duration_verified = BooleanField("Duration verified")
    certificate_available = SelectField(
        "Certificate",
        choices=[("", "Unknown"), ("true", "Available"), ("false", "Not available")],
        validators=[Optional()],
        default="",
        validate_choice=False,
    )
    certificate_verified = BooleanField("Certificate details verified")
    teaching_format = StringField("Teaching format", validators=[Optional(), Length(max=200)])
    skills_taught = TextAreaField("Skills taught (one per line)", validators=[Optional(), Length(max=2000)])
    learning_outcomes = TextAreaField("Learning outcomes (one per line)", validators=[Optional(), Length(max=3000)])
    intended_audience = TextAreaField("Intended audience", validators=[Optional(), Length(max=2000)])
    strengths = TextAreaField("Strengths", validators=[Optional(), Length(max=4000)])
    limitations = TextAreaField("Limitations", validators=[Optional(), Length(max=4000)])
    editorial_assessment = TextAreaField("Editorial assessment", validators=[Optional(), Length(max=4000)])
    editorial_score = DecimalField("Editorial score (1–10)", validators=[Optional(), NumberRange(min=1, max=10)])
    editorial_score_public = BooleanField("Show editorial score publicly")
    editorial_why_recommend = TextAreaField("Why recommend it", validators=[Optional(), Length(max=4000)])
    editorial_who_should_take = TextAreaField("Who should take it", validators=[Optional(), Length(max=4000)])
    editorial_who_should_avoid = TextAreaField("Who should avoid it", validators=[Optional(), Length(max=4000)])
    editorial_value_for_money = TextAreaField("Value for money", validators=[Optional(), Length(max=4000)])
    editorial_approved = BooleanField("Approve editorial content for public display")
    recommendation_status = SelectField(
        "Recommendation status",
        choices=[(item, item.replace("_", " ").title()) for item in RECOMMENDATION_STATUSES],
        validators=[Optional()],
        default="none",
        validate_choice=False,
    )
    last_verified_at = StringField("Last verified date (YYYY-MM-DD)", validators=[Optional(), Length(max=10)])
    keywords = StringField("Course tags / search keywords", validators=[Optional(), Length(max=300)])
    is_featured = BooleanField("Featured on homepage")
    is_underrated = BooleanField("Underrated gem")
    needs_verification = BooleanField("Needs verification")
    thumbnail = FileField("Thumbnail", validators=[FileAllowed(["jpg", "jpeg", "png", "webp", "gif"])])

    def validate_official_url(self, field):
        if not is_safe_http_url(field.data):
            raise ValidationError("Enter a valid http or https URL.")

    def validate_new_provider_url(self, field):
        if field.data and not is_safe_http_url(field.data):
            raise ValidationError("Enter a valid http or https URL.")

    def validate_source_url(self, field):
        if field.data and not is_safe_http_url(field.data):
            raise ValidationError("Enter a valid http or https URL.")

    def _date(self, field):
        if field.data:
            from datetime import date

            try:
                date.fromisoformat(field.data)
            except ValueError:
                raise ValidationError("Use the format YYYY-MM-DD.")

    def validate_last_verified_at(self, field):
        self._date(field)

    def validate_source_checked_at(self, field):
        self._date(field)


class CategoryForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=80)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=500)])
    sort_order = IntegerField("Sort order", validators=[Optional()], default=0)
    is_active = BooleanField("Active", default=True)


class ProviderForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=160)])
    website_url = StringField("Website", validators=[Optional(), Length(max=500)])

    def validate_website_url(self, field):
        if field.data and not is_safe_http_url(field.data):
            raise ValidationError("Enter a valid http or https URL.")


class ReportForm(FlaskForm):
    reason = SelectField("Reason", validators=[DataRequired()])
    details = TextAreaField("Details (optional)", validators=[Optional(), Length(max=1000)])

    def __init__(self, target_type="course", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reason.choices = list(COURSE_REPORT_REASONS if target_type == "course" else REVIEW_REPORT_REASONS)


class SubmissionForm(FlaskForm):
    title = StringField("Course title", validators=[DataRequired(), Length(max=200)])
    official_url = StringField("Official course page", validators=[DataRequired(), Length(max=500)])
    provider_name = StringField("Provider", validators=[Optional(), Length(max=160)])
    note = TextAreaField("Why is it worth listing?", validators=[Optional(), Length(max=1000)])

    def validate_official_url(self, field):
        if not is_safe_http_url(field.data):
            raise ValidationError("Enter a valid http or https URL.")


class ProfileForm(FlaskForm):
    display_name = StringField("Display name", validators=[DataRequired(), Length(max=80)])


class PasswordChangeForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    new_password = PasswordField("New password", validators=[DataRequired(), Length(min=8, max=128)])
    confirm_password = PasswordField(
        "Confirm new password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
    )


class ImportForm(FlaskForm):
    file = FileField("CSV file", validators=[FileAllowed(["csv"], "CSV files only")])
