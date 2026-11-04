# CourseFinder 🎓

**Discover. Compare. Learn.**

CourseFinder is a course discovery platform designed to help learners find high-quality online courses across a wide range of subjects, including Computer Science, Artificial Intelligence, Machine Learning, Finance, Business, Design, and Web Development.

The platform aims to make online learning easier by bringing together carefully curated course recommendations, detailed course information, and authentic learner feedback in one place.

Rather than simply listing popular courses, CourseFinder aims to help users discover valuable learning opportunities, including lesser-known courses that deserve more attention.

## ✨ Key Features

- **Course Discovery:** Explore courses across multiple subjects and learning categories.
- **Curated Recommendations:** Discover courses selected and reviewed by the platform's administrator.
- **Course Details:** Access information about course content, duration, difficulty, prerequisites, and certification.
- **User Accounts:** Register and log in to access account-based features.
- **Reviews and Ratings:** Share feedback and help other learners make informed decisions.
- **Search and Filtering:** Find courses based on relevant categories and preferences.
- **Owner Studio:** A dedicated administration interface for managing course listings and platform content.
- **AI-Assisted Content:** Support for generating and improving course descriptions and recommendations, subject to owner review.

## 📚 Course Categories

CourseFinder is designed to cover a variety of learning areas:

- Computer Science
- Artificial Intelligence
- Machine Learning
- Data Science
- Programming
- Web Development
- Finance and Investing
- Business and Entrepreneurship
- Design and Creativity
- Other Professional Skills

## 🛠️ Technology Stack

| Technology | Purpose |
|---|---|
| Python | Backend programming |
| Flask | Web application framework |
| HTML | Page structure |
| CSS | Styling and layout |
| JavaScript | Frontend interactivity |
| SQLite | Local database |
| Git and GitHub | Version control |

*The technology list should be updated if the project uses additional frameworks, libraries, or database services.*

## 🚀 Getting Started

Follow these steps to run CourseFinder locally.

### 1. Clone the repository

```bash
git clone YOUR_GITHUB_REPOSITORY_URL
```

### 2. Open the project directory

Navigate into the directory containing the Flask application.

```bash
cd YOUR_PROJECT_FOLDER
```

### 3. Create a virtual environment

```bash
python -m venv .venv
```

### 4. Activate the virtual environment

**Windows PowerShell:**

```powershell
.\.venv\Scripts\Activate.ps1
```

### 5. Install dependencies

```bash
pip install -r requirements.txt
```

### 6. Configure environment variables

Create a local `.env` file using the project's environment-variable template, if provided.

Add the required configuration values before starting the application. Never upload passwords, secret keys, or API credentials to GitHub.

### 7. Run the application

If `wsgi.py` is the project's entry point:

```bash
python wsgi.py
```

Open the local application in your browser:

```text
http://127.0.0.1:5000
```

## 🔐 Administration

CourseFinder includes a dedicated Owner Studio for managing the platform's course catalogue and content.

The administration interface is intended to allow authorized owners to:

- Add and edit course listings.
- Manage course categories and information.
- Review and publish course content.
- Improve descriptions and learning recommendations.
- Manage platform content.

Administrative access should remain restricted to authorized users.

## 🎯 Project Goals

CourseFinder is built around three main goals:

1. **Improve course discovery:** Make it easier to find relevant and worthwhile learning resources.
2. **Promote informed decisions:** Provide useful course information so learners can evaluate their options.
3. **Highlight quality learning:** Give both popular and underrated courses an opportunity to be discovered.

## 🔮 Future Improvements

Potential future developments include:

- More advanced course recommendation algorithms.
- Improved personalization based on learning interests.
- Expanded course reviews and rating features.
- Additional learning categories and curated collections.
- Improved mobile responsiveness.
- Production deployment and database integration.
- More advanced AI-assisted discovery tools.

## 👨‍💻 Development

CourseFinder is an independently developed educational technology project focused on improving online course discovery.

The project is also an opportunity to explore practical software development, backend engineering, database management, user authentication, and application design.

## 📄 License

No open-source license has currently been specified. All rights remain with the copyright holder unless permission is explicitly granted.

---

**CourseFinder — Find the right course. Build the right future.**
