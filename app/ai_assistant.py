"""AI Content Assistant for CourseFinder Owner Studio.

Helps the administrator generate and improve:
- Short descriptions (<= 280 chars)
- Full structured course descriptions
- Skills taught (one per line)
- Search keywords / tags
- Intended audience & prerequisites suggestions
- Personal recommendation / editorial assessment outlines

SAFETY & EDITORIAL RULES:
1. Generates editorial text only; it does not generate prices, durations, certificates, or user reviews.
2. Never automatically publishes, approves, or features a course.
3. Uses deterministic, rule-based editorial synthesis entirely within the application.
   It does not call an external LLM or require an AI API key.
"""

from __future__ import annotations

import re
from app.utils import clean_text, split_lines

CATEGORY_SKILLS = {
    "computer-science": [
        "Computational thinking",
        "Algorithms and data structures",
        "Systems architecture",
        "Problem solving",
        "Software design principles",
    ],
    "artificial-intelligence": [
        "Machine learning fundamentals",
        "Neural networks and deep learning",
        "Model evaluation and validation",
        "AI system design",
        "Applied AI workflows",
    ],
    "programming": [
        "Clean code and modular design",
        "Debugging and testing",
        "Software engineering workflows",
        "API integration",
        "Application development",
    ],
    "data-science": [
        "Exploratory data analysis",
        "Statistical modeling",
        "Data cleaning and wrangling",
        "Data visualization",
        "Quantitative reasoning",
    ],
    "finance-and-investing": [
        "Financial statement analysis",
        "Valuation fundamentals",
        "Risk management",
        "Portfolio construction concepts",
        "Capital markets literacy",
    ],
    "business": [
        "Business strategy",
        "Operational execution",
        "Unit economics and decision making",
        "Product and market positioning",
        "Leadership and management",
    ],
    "marketing": [
        "Audience research and positioning",
        "Growth and acquisition strategy",
        "Content and messaging frameworks",
        "Conversion and funnel analysis",
        "Brand communication",
    ],
    "design": [
        "User interface (UI) principles",
        "User experience (UX) research",
        "Visual hierarchy and typography",
        "Design systems and component thinking",
        "Interactive prototyping",
    ],
    "mathematics": [
        "Mathematical proofs and rigor",
        "Linear algebra concepts",
        "Probability and statistics",
        "Calculus and optimization",
        "Analytical problem solving",
    ],
    "personal-development": [
        "Structured learning habits",
        "Technical communication",
        "Critical thinking",
        "Research methodology",
        "Time and project management",
    ],
}

CATEGORY_HINTS = (
    ("artificial-intelligence", ("machine learning", "artificial intelligence", "deep learning", "transformer", "neural network", "llm")),
    ("computer-science", ("algorithm", "operating system", "compiler", "distributed systems", "computer science")),
    ("data-science", ("data science", "data analysis", "data visualization", "pandas", "statistics", "analytics")),
    ("finance-and-investing", ("finance", "investing", "portfolio", "valuation", "accounting", "capital markets")),
    ("business", ("entrepreneur", "business", "startup", "management", "operations", "strategy")),
    ("marketing", ("marketing", "seo", "copywriting", "customer acquisition", "growth strategy")),
    ("design", ("design", "figma", "user experience", "ux", "typography", "prototyping")),
    ("mathematics", ("mathematics", "calculus", "linear algebra", "probability", "mathematical proof")),
    ("programming", ("programming", "python", "javascript", "software engineering", "coding", "rust", "java")),
)

CATEGORY_NAMES = {
    "artificial-intelligence": "Artificial Intelligence & Machine Learning",
    "computer-science": "Computer Science",
    "data-science": "Data Science",
    "finance-and-investing": "Finance and Investing",
    "business": "Business & Entrepreneurship",
    "marketing": "Marketing",
    "design": "Design",
    "mathematics": "Mathematics",
    "programming": "Programming",
}

TOPIC_SKILL_MAP = {
    "python": "Python programming",
    "javascript": "JavaScript development",
    "typescript": "TypeScript",
    "react": "React frontend development",
    "sql": "SQL and relational databases",
    "postgres": "PostgreSQL",
    "c++": "C++ programming",
    "rust": "Rust systems programming",
    "golang": "Go (Golang)",
    "java": "Java programming",
    "pytorch": "PyTorch deep learning",
    "tensorflow": "TensorFlow",
    "llm": "Large Language Models (LLMs)",
    "nlp": "Natural Language Processing (NLP)",
    "transformer": "Transformer architectures",
    "bayesian": "Bayesian statistical inference",
    "pandas": "Data analysis with pandas",
    "algorithm": "Algorithmic analysis",
    "distributed": "Distributed systems",
    "compiler": "Compiler construction",
    "operating system": "Operating systems",
    "linux": "Linux and command-line tools",
    "git": "Version control with Git",
    "docker": "Containerization with Docker",
    "security": "Application and systems security",
    "cryptography": "Applied cryptography",
    "accounting": "Financial accounting",
    "valuation": "Corporate valuation",
    "options": "Derivatives and options pricing",
    "figma": "Interface design in Figma",
    "ux": "User experience (UX) design",
    "seo": "Search engine optimization (SEO)",
    "copywriting": "Conversion copywriting",
    "calculus": "Calculus",
    "linear algebra": "Linear algebra",
    "probability": "Probability theory",
}

STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "that", "this", "your", "you", "are",
    "will", "learn", "course", "introduction", "intro", "complete", "guide", "how",
    "use", "using", "build", "building", "fundamentals", "basics", "advanced",
    "intermediate", "beginner", "online", "class", "lectures", "part", "about",
    "over", "under", "more", "most", "some", "such", "than", "then", "them", "they",
}

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
WORD_RE = re.compile(r"[a-zA-Z0-9+#.\-]{2,}")


def _extract_keywords(title: str, category_slug: str, provider_name: str, notes: str, skills: list[str]) -> str:
    tokens = []
    seen = set()

    def add_token(tok: str):
        t = tok.strip().lower().strip(".,;:()[]{}\"'")
        if len(t) < 2 or t in STOPWORDS or t.isdigit():
            return
        if t not in seen:
            seen.add(t)
            tokens.append(t)

    for source in (title, provider_name, category_slug.replace("-", " "), " ".join(skills), notes):
        for match in WORD_RE.findall(source or ""):
            add_token(match)
            if len(tokens) >= 18:
                break
        if len(tokens) >= 18:
            break

    return ", ".join(tokens[:16])


def _infer_skills(title: str, category_slug: str, notes: str, existing_skills: str) -> list[str]:
    current = split_lines(existing_skills)
    seen = {s.lower() for s in current}
    result = list(current)

    combined = f"{title} {notes}".lower()
    for trigger, label in TOPIC_SKILL_MAP.items():
        if trigger in combined and label.lower() not in seen:
            seen.add(label.lower())
            result.append(label)

    # Extract bullet lines from raw notes if user pasted syllabus bullets
    for line in (notes or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(("-", "*", "•")) and 4 <= len(stripped) <= 75:
            item = stripped.lstrip("-*• ").strip().rstrip(".")
            if item and item.lower() not in seen:
                seen.add(item.lower())
                result.append(item)

    # Pad with category foundational skills up to at least 4-6 items
    defaults = CATEGORY_SKILLS.get(category_slug or "", CATEGORY_SKILLS["personal-development"])
    for skill in defaults:
        if len(result) >= 6:
            break
        if skill.lower() not in seen:
            seen.add(skill.lower())
            result.append(skill)

    return result[:8]


def _polish_sentences(text: str) -> str:
    text = clean_text(text, 6000)
    if not text:
        return ""
    sentences = [s.strip() for s in SENTENCE_SPLIT_RE.split(text) if s.strip()]
    cleaned = []
    for s in sentences:
        if s and s[0].islower():
            s = s[0].upper() + s[1:]
        if s and s[-1] not in ".!?":
            s += "."
        cleaned.append(s)
    return " ".join(cleaned)


def _suggest_category(title: str, notes: str, category_slug: str, category_name: str):
    text = f"{title} {notes}".lower()
    for slug, hints in CATEGORY_HINTS:
        if any(hint in text for hint in hints):
            return slug, CATEGORY_NAMES[slug]
    return category_slug, category_name


def generate_course_assistance(
    title: str = "",
    provider_name: str = "",
    category_slug: str = "",
    category_name: str = "",
    difficulty: str = "",
    short_description: str = "",
    description: str = "",
    skills_taught: str = "",
    learning_outcomes: str = "",
    prerequisites: str = "",
    intended_audience: str = "",
    raw_notes: str = "",
    mode: str = "all",
) -> dict:
    """Generate or improve editorial course fields without inventing unverified facts.

    Returns a dictionary of suggested field values plus an explicit editorial reminder.
    """
    title = clean_text(title, 200) or "Untitled Course"
    provider_name = clean_text(provider_name, 160)
    category_slug = clean_text(category_slug, 80).lower()
    category_name = clean_text(category_name, 80) or (
        category_slug.replace("-", " ").title() if category_slug else "Professional Studies"
    )
    difficulty = clean_text(difficulty, 30).lower()
    diff_label = difficulty.replace("-", " ") if difficulty in ("beginner", "intermediate", "advanced") else ""

    combined_notes = "\n".join(filter(None, [clean_text(raw_notes, 4000), clean_text(description, 4000)]))
    inferred_skills = _infer_skills(title, category_slug, combined_notes, skills_taught)
    top_skills_phrase = ", ".join(s.lower() for s in inferred_skills[:3]) if inferred_skills else "core domain concepts"
    suggested_category_slug, suggested_category_name = _suggest_category(
        title, combined_notes, category_slug, category_name
    )

    # 1. Short description (<= 280 chars)
    provider_clause = f" from {provider_name}" if provider_name else ""
    level_clause = f"A {diff_label} {category_name.lower()} course" if diff_label else f"A {category_name.lower()} course"

    if mode == "improve" and short_description.strip():
        base_short = _polish_sentences(short_description)
        if len(base_short) < 60:
            base_short = f"{base_short.rstrip('.')} covering {top_skills_phrase}."
        suggested_short = base_short[:277] + ("..." if len(base_short) > 277 else "")
    else:
        suggested_short = (
            f"{level_clause}{provider_clause} focused on {top_skills_phrase} through structured study."
        )[:280]

    # 2. Full description
    polished_existing = _polish_sentences(raw_notes or description)
    intro_para = (
        f"{title} is {level_clause.lower()}{provider_clause} in {category_name}. "
        f"It focuses on building practical fluency in {', '.join(inferred_skills[:4])}."
    )
    body_para = (
        polished_existing
        if polished_existing and polished_existing.lower() != title.lower()
        else (
            f"Learners work through core concepts and applied exercises designed to strengthen "
            f"{top_skills_phrase}."
        )
    )
    transparency_para = (
        "CourseFinder lists this as an external course recommendation and does not host the lessons. "
        "Please verify current syllabus details, scheduling, pricing, and certificate options directly on the official provider page."
    )
    suggested_description = f"{intro_para}\n\n{body_para}\n\n{transparency_para}"

    # Learning outcomes are editable suggestions derived from inferred skills, not claims about a verified syllabus.
    if mode == "improve" and learning_outcomes.strip():
        suggested_outcomes = "\n".join(
            _polish_sentences(outcome) for outcome in split_lines(learning_outcomes)
        )
    else:
        outcome_verbs = ("Explain", "Apply", "Analyze", "Design", "Evaluate")
        suggested_outcomes = "\n".join(
            f"{outcome_verbs[index]} {skill} in structured learning exercises."
            for index, skill in enumerate(inferred_skills[:5])
        )

    # 3. Prerequisites & Intended audience
    if prerequisites.strip():
        suggested_prereqs = _polish_sentences(prerequisites)
    elif difficulty == "beginner":
        suggested_prereqs = "No formal prior background required; willingness to complete structured exercises is recommended."
    elif difficulty == "intermediate":
        suggested_prereqs = f"Foundational familiarity with {category_name.lower()} concepts before tackling intermediate material."
    elif difficulty == "advanced":
        suggested_prereqs = f"Solid prior preparation in {category_name.lower()} and comfort with rigorous independent problem solving."
    else:
        suggested_prereqs = "Check the official course page for current prerequisite expectations."

    if intended_audience.strip():
        suggested_audience = _polish_sentences(intended_audience)
    elif diff_label:
        suggested_audience = (
            f"{diff_label.title()} learners looking for a structured {category_name.lower()} curriculum "
            f"covering {top_skills_phrase}."
        )
    else:
        suggested_audience = f"Students and professionals seeking grounded skills in {category_name.lower()}."

    # 4. Personal Recommendation / Editorial Assessment scaffolding
    suggested_editorial = (
        f"[Editorial Draft — Review & Verify Before Approving] {title}{provider_clause} offers a structured path into "
        f"{top_skills_phrase}. Evaluate workload depth, exercise quality, and current pricing on the official syllabus before marking editorial notes public."
    )
    suggested_who_should_take = (
        f"Learners who want focused coverage of {top_skills_phrase} and can commit regular study time."
    )
    suggested_who_should_avoid = (
        "Learners looking for a passive overview without hands-on practice, or those seeking topics outside this syllabus."
    )

    keywords_str = _extract_keywords(title, category_slug, provider_name, combined_notes, inferred_skills)

    return {
        "short_description": suggested_short,
        "description": suggested_description,
        "skills_taught": "\n".join(inferred_skills),
        "learning_outcomes": suggested_outcomes,
        "category_suggestion_slug": suggested_category_slug,
        "category_suggestion": suggested_category_name,
        "keywords": keywords_str,
        "prerequisites": suggested_prereqs,
        "intended_audience": suggested_audience,
        "editorial_assessment": suggested_editorial,
        "editorial_who_should_take": suggested_who_should_take,
        "editorial_who_should_avoid": suggested_who_should_avoid,
        "notice": (
            "AI Content Assistant generated draft suggestions. Review and verify every detail against the official "
            "course page before saving. Nothing is published or approved automatically."
        ),
    }
